#!/usr/bin/env python3
"""Control only the three verified camera PoE ports using a private UniFi key.

Credential/mapping file: /config/.camera-power.json (0600, never tracked).
TLS is pinned to that gateway's certificate before transmitting the API key.
"""

import argparse
import copy
import fcntl
import hashlib
import http.client
import json
from pathlib import Path
import re
import socket
import ssl
import sys
import time

CONFIG = Path("/config/.camera-power.json")
CAMERAS = ("camera_1", "camera_2", "camera_3")


class Controller:
    def __init__(self, settings):
        self.settings = settings
        if set(settings["cameras"]) != set(CAMERAS):
            raise ValueError("Expected exactly the three camera mappings")
        ports = [c["port"] for c in settings["cameras"].values()]
        if len(set(ports)) != 3 or any(p not in range(1, 9) for p in ports):
            raise ValueError("Camera mappings must use three distinct PoE ports")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", settings["site"]):
            raise ValueError("Invalid site reference")

    def request(self, method, path, data=None):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        connection = http.client.HTTPSConnection(
            self.settings["host"], timeout=12, context=context,
        )
        try:
            connection.connect()
            certificate = connection.sock.getpeercert(binary_form=True)
            if hashlib.sha256(certificate).hexdigest() != self.settings["certificate_sha256"]:
                raise RuntimeError("Gateway certificate changed; review the pin before reconnecting")
            connection.request(method, "/proxy/network/api/s/" + self.settings["site"] + path,
                body=None if data is None else json.dumps(data),
                headers={"X-API-KEY": self.settings["api_key"],
                         "Accept": "application/json", "Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError(f"UniFi request failed: HTTP {response.status}")
            result = json.load(response)
            if result.get("meta", {}).get("rc") != "ok":
                raise RuntimeError("UniFi did not acknowledge the request")
            return result["data"]
        finally:
            connection.close()

    def device(self):
        rows = self.request("GET", "/stat/device")
        device = next((d for d in rows if d.get("_id") == self.settings["switch_id"]), None)
        if not device or device.get("mac") != self.settings["switch_mac"]:
            raise RuntimeError("The configured camera switch was not found")
        if device.get("state") != 1 or time.time() - device.get("last_seen", 0) > 120:
            raise RuntimeError("Camera switch telemetry is offline or stale")
        return device

    def verify_bindings(self, clients):
        for name, camera in self.settings["cameras"].items():
            for client in clients:
                if client.get("mac") == camera["mac"] and (
                    client.get("sw_mac") != self.settings["switch_mac"]
                    or client.get("sw_port") != camera["port"]
                ):
                    raise RuntimeError(f"{name} moved to another port; review its mapping")
                if (client.get("sw_mac") == self.settings["switch_mac"]
                    and client.get("sw_port") == camera["port"]
                    and client.get("mac") != camera["mac"]):
                    raise RuntimeError(f"A different device is connected to {name}'s port")

    def power(self, enabled):
        # The lock serializes this helper; external controller edits are detected
        # immediately before PUT. UniFi's API has no conditional-update contract.
        self.verify_bindings(self.request("GET", "/stat/sta"))
        device = self.device()
        before = device.get("port_overrides", [])
        overrides = copy.deepcopy(before)
        desired = "auto" if enabled else "off"
        camera_ports = {c["port"] for c in self.settings["cameras"].values()}
        for port in camera_ports:
            override = next((o for o in overrides if o.get("port_idx") == port), None)
            if override is None:
                # Require the explicitly verified VLAN/port profile to exist.
                raise RuntimeError("A camera port override is missing; review the port configuration")
            override["poe_mode"] = desired
        if overrides != before:
            if self.device().get("port_overrides", []) != before:
                raise RuntimeError("Switch configuration changed concurrently; retry after review")
            self.request("PUT", "/rest/device/" + self.settings["switch_id"],
                         {"port_overrides": overrides})
        # Controller acknowledgement is distinct from actual power telemetry.
        return {"requested": desired, "camera_ports": sorted(camera_ports)}

    def status(self):
        device = self.device()
        ports = {p["port_idx"]: p for p in device.get("port_table", [])}
        overrides = {o["port_idx"]: o for o in device.get("port_overrides", [])}
        cameras = {}
        for name, mapping in self.settings["cameras"].items():
            port = ports.get(mapping["port"])
            if not port or not port.get("port_poe"):
                raise RuntimeError("A mapped camera port is not a PoE port")
            mode = overrides.get(mapping["port"], {}).get("poe_mode", port.get("poe_mode"))
            watts = float(port["poe_power"])
            off = (mode == "off" and port.get("poe_mode") == "off"
                   and watts == 0 and port.get("poe_good") is False)
            reachable = False
            try:
                with socket.create_connection((mapping["ip"], 554), timeout=1):
                    reachable = True
            except OSError:
                pass
            cameras[name] = {"port": mapping["port"], "mode": mode, "watts": watts,
                             "off_confirmed": off and not reachable,
                             "rtsp_ready": reachable and mode == "auto"}
        return {"status": "ok", "cameras": cameras, "checked_at": time.time(),
                "privacy_verified": all(c["off_confirmed"] for c in cameras.values()),
                "ready": all(c["rtsp_ready"] for c in cameras.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("status", "on", "off"))
    args = parser.parse_args()
    settings = json.loads(CONFIG.read_text())
    controller = Controller(settings)
    if args.operation == "status":
        print(json.dumps(controller.status()))
    else:
        with Path("/config/.camera-power.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            print(json.dumps(controller.power(args.operation == "on")))


def execute():
    try:
        main()
        return 0
    except Exception as exc:
        # Request bodies, headers and credential values must never reach logs.
        print(json.dumps({"status": "error", "error": str(exc), "cameras": {},
                          "privacy_verified": False, "ready": False, "checked_at": time.time()}))
        # A parseable error state clears HA's previous successful telemetry.
        # Power commands still fail nonzero so their calling script stops.
        return 0 if sys.argv[1:] == ["status"] else 1


if __name__ == "__main__":
    sys.exit(execute())
