#!/usr/bin/env python3
"""Exercise the tracked routine YAML in an isolated instance of installed HA.

Runs inside ct-ha's existing image, with temporary storage and fake services.
No live HA API, credentials, device commands, reloads or configuration writes.
"""

import argparse
import inspect
import json
from pathlib import Path
import shlex
import subprocess


async def exercise(payload):
    import asyncio
    import copy
    import logging
    import tempfile
    import time

    from homeassistant import loader
    from homeassistant.config_entries import ConfigEntries
    from homeassistant.core import HomeAssistant, SupportsResponse
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers import area_registry, device_registry, entity_registry, floor_registry, label_registry
    from homeassistant.helpers.template import Template
    from homeassistant.setup import async_setup_component
    import yaml

    logging.basicConfig(level=logging.ERROR)
    source = yaml.safe_load(payload["scripts"])
    dashboard = yaml.safe_load(payload["dashboard"])
    def contents(value):
        if isinstance(value, dict):
            if "content" in value:
                yield value["content"]
            for child in value.values():
                yield from contents(child)
        elif isinstance(value, list):
            for child in value:
                yield from contents(child)
    result_panel = next(value for value in contents(dashboard)
        if "input_text.household_routine_cameras" in value)
    # Shorten only waits. Production actions, templates and control flow are used.
    def shorten(value):
        if isinstance(value, dict):
            if "wait_template" in value:
                value["timeout"] = 0
            for child in value.values():
                shorten(child)
        elif isinstance(value, list):
            for child in value:
                shorten(child)

    cameras = ["camera.cam_soggiorno", "camera.camera_2", "camera.camera_3"]
    shutters = ["cover.shellyplus2pm_8813bfd0a3b8", "cover.shellyplus2pm_8813bfd0b9ec",
                "cover.shellyplus2pm_8813bfd4933c", "cover.shellyplus2pm_2cbcbb2d92d8",
                "cover.shellyplus2pm_78ee4ccf104c"]

    async def scenario(case):
        with tempfile.TemporaryDirectory(prefix="ha-routine-test-") as temporary:
            hass = HomeAssistant(temporary)
            loader.async_setup(hass)
            hass.config_entries = ConfigEntries(hass, {})
            device_registry.async_setup(hass)
            await asyncio.gather(*(registry.async_load(hass) for registry in
                (area_registry, device_registry, entity_registry, floor_registry, label_registry)))
            calls = []
            power_on = False
            requested_power = asyncio.Event()
            config = copy.deepcopy(source)
            shorten(config)
            if case == "latest_intent":
                # Leave Night waiting for camera frames until Rientro supersedes it.
                def extend(value):
                    if isinstance(value, dict):
                        if "wait_template" in value:
                            value["timeout"] = 5
                        for v in value.values():
                            extend(v)
                    elif isinstance(value, list):
                        for v in value:
                            extend(v)
                extend(config["household_apply_camera_policy"])
            try:
                assert await async_setup_component(hass, "script", {"script": config})
                await hass.async_start()
                await hass.async_block_till_done()

                def state(entity, value, attributes=None):
                    hass.states.async_set(entity, value, attributes or {})

                initial = {
                    "input_select.household_presence": "In casa",
                    "input_select.household_routine_status": "Pronta",
                    "input_boolean.household_night": "off",
                    "input_boolean.household_guests": "off",
                    "input_boolean.household_long_absence": "off",
                    "input_boolean.household_shutters_enabled": "on",
                    "input_boolean.household_morning_shutters": "on",
                    "sensor.household_open_windows": "0",
                    "binary_sensor.front_door_door": "off",
                }
                for entity, value in initial.items():
                    state(entity, value)
                for key in ("name", "step", "result", "cameras", "shutters"):
                    state("input_text.household_routine_" + key, "")
                for key in ("office", "master", "guest_left", "guest_right", "kitchen"):
                    state("input_number.night_shutter_" + key, "15")
                state("input_number.morning_shutter_position", "100")
                for entity in shutters:
                    state(entity, "open", {"current_position": 100, "supported_features": 15})
                for entity in cameras:
                    state(entity, "idle")
                for i in range(1, 4):
                    state(f"sensor.camera_{i}_camera_fps", "0")
                    state(f"switch.camera_{i}_recordings", "on")

                def update_power():
                    state("sensor.camera_power", "ok", {
                        "checked_at": time.time(), "ready": power_on,
                        "privacy_verified": not power_on,
                    })
                    for i in range(1, 4):
                        broken = case in ("missing_video", "latest_intent") and i == 2
                        state(f"sensor.camera_{i}_camera_fps", "5" if power_on and not broken else "0")
                update_power()

                async def handle(call):
                    nonlocal power_on
                    domain, service, data = call.domain, call.service, call.data
                    calls.append((domain, service, dict(data)))
                    entities = data.get("entity_id", [])
                    if isinstance(entities, str):
                        entities = [entities]
                    if domain in ("input_text", "input_select", "input_boolean", "input_datetime"):
                        for entity in entities:
                            value = data.get("value", data.get("option"))
                            if domain == "input_boolean":
                                value = "on" if service == "turn_on" else "off"
                            if domain == "input_datetime":
                                value = "2026-11-04 22:00:00"
                            state(entity, value)
                    elif domain == "shell_command":
                        requested_power.set()
                        if case == "power_failure":
                            return {"returncode": 1, "stdout": "", "stderr": "test failure"}
                        power_on = service.endswith("_on")
                        return {"returncode": 0, "stdout": "{}", "stderr": ""}
                    elif domain == "homeassistant":
                        update_power()
                    elif domain == "camera":
                        if case == "frigate_failure" and service == "turn_off":
                            raise HomeAssistantError("simulated Frigate outage")
                        for entity in entities:
                            state(entity, "recording" if service == "turn_on" else "idle")
                    elif domain == "switch":
                        for entity in entities:
                            state(entity, "on")
                    elif domain == "cover":
                        for entity in entities:
                            if case == "stuck_shutter" and entity == shutters[1]:
                                raise HomeAssistantError("simulated stuck shutter")
                            state(entity, "open", {"current_position": data["position"], "supported_features": 15})

                services = {
                    "input_text": ["set_value"], "input_select": ["select_option"],
                    "input_boolean": ["turn_on", "turn_off"], "input_datetime": ["set_datetime"],
                    "camera": ["turn_on", "turn_off"], "switch": ["turn_on"],
                    "cover": ["set_cover_position"], "homeassistant": ["update_entity"],
                    "persistent_notification": ["create", "dismiss"], "logbook": ["log"],
                    "shell_command": ["household_camera_power_on", "household_camera_power_off"],
                }
                for domain, names in services.items():
                    for name in names:
                        hass.services.async_register(domain, name, handle,
                            supports_response=SupportsResponse.OPTIONAL if domain == "shell_command" else SupportsResponse.NONE)

                async def run(routine):
                    await hass.services.async_call("script", "household_run_routine", {"routine": routine}, blocking=True)
                    await hass.async_block_till_done()

                mode = "morning" if case in ("morning", "repeat", "frigate_failure") else "night"
                if mode == "morning":
                    for entity in shutters:
                        state(entity, "open", {"current_position": 15, "supported_features": 15})
                    state("input_boolean.household_night", "on")
                if case == "guests":
                    state("input_boolean.household_guests", "on")
                    mode = "leaving"
                if case == "open_window":
                    state("sensor.household_open_windows", "1")
                if case == "invalid_position":
                    state("input_number.night_shutter_master", "-10")
                if case == "unknown_presence":
                    state("input_select.household_presence", "Da verificare")
                    result = await hass.services.async_call("script", "household_apply_camera_policy", {},
                        blocking=True, return_response=True)
                    assert result["ok"] is False
                    assert not calls
                    return
                if case == "latest_intent":
                    night_task = asyncio.create_task(run("night"))
                    await asyncio.wait_for(requested_power.wait(), 5)
                    # Shutters must respond while Night is still waiting for video.
                    for _ in range(40):
                        if any(c[0] == "cover" for c in calls):
                            break
                        await asyncio.sleep(0)
                    assert any(c[0] == "cover" for c in calls)
                    assert hass.states.get("script.household_apply_camera_policy").state == "on"
                    await run("arriving")
                    await asyncio.gather(night_task, return_exceptions=True)
                    assert hass.states.get("input_boolean.household_night").state == "off"
                    assert power_on is False
                    # Rientro does not add or reverse the earlier Night commands.
                    cover_calls = [c for c in calls if c[0] == "cover"]
                    assert len(cover_calls) <= 5
                    assert all(c[2]["position"] == 15 for c in cover_calls)
                else:
                    await run(mode)
                    if case == "repeat":
                        await run(mode)

                result = hass.states.get("input_select.household_routine_status").state
                partial = case in ("guests", "open_window", "power_failure", "stuck_shutter", "missing_video", "invalid_position")
                assert result == ("Da controllare" if partial else "Completata"), (case, result)
                cover_calls = [c for c in calls if c[0] == "cover"]
                if case == "guests":
                    assert not cover_calls and not [c for c in calls if c[0] in ("camera", "shell_command")]
                    assert hass.states.get("input_select.household_presence").state == "In casa"
                elif case not in ("latest_intent",):
                    expected = 3 if mode == "morning" else 4 if case == "invalid_position" else 5
                    assert len(cover_calls) == expected, (case, cover_calls)
                    assert all(c[2]["position"] == (100 if mode == "morning" else 15) for c in cover_calls)
                    if mode == "morning":
                        assert all(entity not in str(cover_calls) for entity in shutters[2:4])
                        assert power_on is False
                    if case == "repeat":
                        assert len([c for c in calls if c[0] == "shell_command"]) == 2
                    if case == "stuck_shutter":
                        assert "Camera matrimoniale" in hass.states.get("input_text.household_routine_shutters").state
                    if case == "missing_video":
                        assert "non attivi" in hass.states.get("input_text.household_routine_cameras").state
                rendered = Template(result_panel, hass).async_render(parse_result=False)
                assert hass.states.get("input_text.household_routine_name").state in rendered
                assert hass.states.get("input_text.household_routine_cameras").state in rendered
                assert hass.states.get("input_text.household_routine_shutters").state in rendered
            finally:
                await hass.async_stop(force=True)

    cases = ("night", "morning", "repeat", "guests", "open_window", "power_failure",
             "frigate_failure", "stuck_shutter", "missing_video", "invalid_position",
             "unknown_presence", "latest_intent")
    for case in cases:
        await scenario(case)
        print("PASS", case, flush=True)
    print(f"{len(cases)} isolated Home Assistant scenarios passed; no live device services were used.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="ct-ha")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = "import asyncio,json,sys\n" + inspect.getsource(exercise)
    source += "\nasyncio.run(exercise(json.load(sys.stdin)))\n"
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    user_config = Path.home() / ".ssh/config"
    if user_config.is_file():
        ssh += ["-F", str(user_config)]
    payload = {"scripts": (root / "stacks/ct-ha/config/scripts.yaml").read_text(),
               "dashboard": (root / "stacks/ct-ha/config/dashboards/casa.yaml").read_text()}
    result = subprocess.run([*ssh, "--", args.host,
        "docker exec -i homeassistant python -c " + shlex.quote(source)],
        input=json.dumps(payload), text=True, timeout=180)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
