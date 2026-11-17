"""Verify camera-only updates, telemetry and TLS boundaries without network I/O."""

import copy
import contextlib
import io
import json
import importlib.util
from pathlib import Path
import unittest
import time
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / "stacks/ct-ha/config/tools/camera_power.py"
spec = importlib.util.spec_from_file_location("camera_power", path)
power = importlib.util.module_from_spec(spec)
spec.loader.exec_module(power)


class CameraPowerTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "host": "192.0.2.1", "site": "default", "switch_id": "switch",
            "switch_mac": "switch-mac", "api_key": "test-only", "certificate_sha256": "wrong-pin",
            "cameras": {f"camera_{i}": {"port": p, "ip": f"192.0.2.{i+10}", "mac": f"cam-{i}"}
                        for i, p in enumerate([5, 2, 7], 1)},
        }
        self.controller = power.Controller(self.settings)
        self.device = {"_id": "switch", "mac": "switch-mac", "state": 1, "last_seen": time.time(), "port_overrides": [
            {"port_idx": p, "poe_mode": "auto", "native_networkconf_id": "iot", "nested": {"keep": True}}
            for p in [2, 5, 7]
        ] + [{"port_idx": 11, "name": "untouched uplink", "forward": "all"}]}

    def test_changes_only_camera_poe_fields(self):
        calls = []
        def request(method, path, data=None):
            calls.append((method, path, data))
            return [] if path == "/stat/sta" or method == "PUT" else [copy.deepcopy(self.device)]
        before = copy.deepcopy(self.device)
        with patch.object(self.controller, "request", side_effect=request):
            self.controller.power(False)
        body = next(data for method, _, data in calls if method == "PUT")
        expected = copy.deepcopy(before["port_overrides"])
        for entry in expected[:3]:
            entry["poe_mode"] = "off"
        self.assertEqual(body, {"port_overrides": expected})
        self.assertEqual(self.device, before)

    def test_refuses_reassigned_port(self):
        clients = [{"mac": "cam-1", "sw_mac": "switch-mac", "sw_port": 11}]
        with self.assertRaisesRegex(RuntimeError, "moved"):
            self.controller.verify_bindings(clients)
        with self.assertRaisesRegex(RuntimeError, "different device"):
            self.controller.verify_bindings([{"mac": "not-camera", "sw_mac": "switch-mac", "sw_port": 5}])

    def test_concurrent_configuration_edit_aborts_without_put(self):
        changed = copy.deepcopy(self.device)
        changed["port_overrides"][-1]["name"] = "changed elsewhere"
        with patch.object(self.controller, "request", side_effect=[[], [self.device], [changed]]) as request:
            with self.assertRaisesRegex(RuntimeError, "concurrently"):
                self.controller.power(False)
        self.assertFalse(any(c.args[0] == "PUT" for c in request.call_args_list))

    def test_poe_configuration_alone_is_not_verified_privacy(self):
        self.device["port_table"] = [{"port_idx": p, "port_poe": True, "poe_mode": "off", "poe_power": "2.0", "poe_good": True} for p in [2, 5, 7]]
        for entry in self.device["port_overrides"][:3]:
            entry["poe_mode"] = "off"
        with patch.object(self.controller, "device", return_value=self.device), patch("socket.create_connection", side_effect=OSError):
            self.assertFalse(self.controller.status()["privacy_verified"])
            for p in self.device["port_table"]:
                p.update(poe_power="0.00", poe_good=False)
            self.assertTrue(self.controller.status()["privacy_verified"])
            del self.device["port_table"][0]["poe_power"]
            with self.assertRaises(KeyError):
                self.controller.status()

    def test_reachable_camera_prevents_privacy_claim_even_at_zero_poe(self):
        self.device["port_table"] = [{"port_idx": p, "port_poe": True, "poe_mode": "off", "poe_power": "0", "poe_good": False} for p in [2, 5, 7]]
        for entry in self.device["port_overrides"][:3]:
            entry["poe_mode"] = "off"
        with patch.object(self.controller, "device", return_value=self.device), patch("socket.create_connection"):
            self.assertFalse(self.controller.status()["privacy_verified"])

    def test_bad_tls_pin_never_transmits_api_key(self):
        with patch("http.client.HTTPSConnection") as connection:
            connection.return_value.sock.getpeercert.return_value = b"untrusted certificate"
            with self.assertRaisesRegex(RuntimeError, "certificate changed"):
                self.controller.request("GET", "/stat/device")
            connection.return_value.request.assert_not_called()
            connection.return_value.close.assert_called_once()

    def test_invalid_mapping_is_rejected(self):
        settings = copy.deepcopy(self.settings)
        settings["cameras"]["camera_1"]["port"] = 16
        with self.assertRaises(ValueError):
            power.Controller(settings)

    def test_stale_switch_data_is_rejected(self):
        self.device['last_seen'] = 0
        with patch.object(self.controller, 'request', return_value=[self.device]):
            with self.assertRaisesRegex(RuntimeError, 'offline or stale'):
                self.controller.device()

    def test_status_failure_clears_previous_privacy_but_power_failure_stops_script(self):
        for operation, code in [('status', 0), ('off', 1)]:
            stream = io.StringIO()
            with patch.object(power.CONFIG.__class__, 'read_text', side_effect=OSError('not available')):
                with patch('sys.argv', ['camera_power.py', operation]), contextlib.redirect_stdout(stream):
                    self.assertEqual(power.execute(), code)
            result = json.loads(stream.getvalue())
            self.assertEqual(result['status'], 'error')
            self.assertFalse(result['privacy_verified'])
            self.assertFalse(result['ready'])


if __name__ == "__main__":
    unittest.main()
