"""Failure-path tests for the staged HA YAML deployment (no network or HA needed)."""

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("ha_config", Path(__file__).with_name("ha-config.py"))
ha = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ha)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.live = Path(self.temporary.name)
        self.files = {name: "old\n" for name in ha.MANAGED}
        self.files["tools/camera_power.py"] = "# helper\n"
        for name, content in self.files.items():
            (self.live / name).parent.mkdir(parents=True, exist_ok=True)
            (self.live / name).write_text(content)
        (self.live / ".storage").mkdir()
        (self.live / ".storage/auth").write_text("private runtime")
        (self.live / "secrets.yaml").write_text("private: secret\n")

    def request(self, operation="apply"):
        return {"operation": operation,
                "files": {**self.files, "automations.yaml": "new\n"}}

    def validator(self, args, **kwargs):
        stage = Path(args[-1])
        self.assertNotEqual(stage, self.live)
        self.assertEqual((stage / "automations.yaml").read_text(), "new\n")
        self.assertEqual((stage / "secrets.yaml").read_text(), "private: secret\n")
        # A migration/write by the checker must affect only the temporary copy.
        (stage / ".storage/auth").write_text("checker wrote here")
        return subprocess.CompletedProcess(args, 0, "valid\n")

    def test_check_does_not_change_live_yaml_or_runtime(self):
        with patch("subprocess.run", side_effect=self.validator):
            result = ha.remote(self.request("check"), self.live)
        self.assertTrue(result["valid"])
        self.assertFalse(result["applied"])
        self.assertEqual((self.live / "automations.yaml").read_text(), "old\n")
        self.assertEqual((self.live / ".storage/auth").read_text(), "private runtime")
        self.assertFalse((self.live / ".infra-backups").exists())

    def test_invalid_config_never_reaches_live_files(self):
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, "invalid")):
            result = ha.remote(self.request(), self.live)
        self.assertFalse(result["valid"])
        self.assertEqual((self.live / "automations.yaml").read_text(), "old\n")
        self.assertFalse((self.live / ".infra-backups").exists())

    def test_apply_backs_up_previous_yaml_and_preserves_secrets(self):
        with patch("subprocess.run", side_effect=self.validator):
            result = ha.remote(self.request(), self.live)
        self.assertTrue(result["applied"])
        self.assertEqual((self.live / "automations.yaml").read_text(), "new\n")
        self.assertEqual((Path(result["backup"]) / "automations.yaml").read_text(), "old\n")
        self.assertEqual((self.live / "secrets.yaml").read_text(), "private: secret\n")
        self.assertEqual((self.live / ".storage/auth").read_text(), "private runtime")

    def test_concurrent_ui_edit_is_not_overwritten(self):
        def concurrent(args, **kwargs):
            (self.live / "automations.yaml").write_text("UI edit\n")
            return subprocess.CompletedProcess(args, 0, "valid")
        with patch("subprocess.run", side_effect=concurrent):
            with self.assertRaisesRegex(RuntimeError, "changed during validation"):
                ha.remote(self.request(), self.live)
        self.assertEqual((self.live / "automations.yaml").read_text(), "UI edit\n")

    def test_second_write_failure_restores_first_file(self):
        import os
        replace = os.replace
        count = 0

        def fail_second(source, target):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("disk full")
            return replace(source, target)

        request = self.request()
        request["files"]["scripts.yaml"] = "changed script\n"
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "valid")):
            with patch("os.replace", side_effect=fail_second):
                with self.assertRaisesRegex(OSError, "disk full"):
                    ha.remote(request, self.live)
        self.assertEqual((self.live / "automations.yaml").read_text(), "old\n")
        self.assertEqual((self.live / "scripts.yaml").read_text(), "old\n")
        self.assertEqual(list(self.live.glob(".infra-*")), [self.live / ".infra-backups"])

    def test_extra_file_cannot_escape_allowlist(self):
        request = self.request()
        request["files"]["../outside"] = "unsafe"
        with self.assertRaisesRegex(ValueError, "exactly the managed"):
            ha.remote(request, self.live)

    def test_first_deployment_creates_nested_files_and_records_absence(self):
        import json
        (self.live / "dashboards/casa.yaml").unlink()
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "valid")):
            result = ha.remote(self.request(), self.live)
        self.assertEqual((self.live / "dashboards/casa.yaml").read_text(), "old\n")
        manifest = json.loads((Path(result["backup"]) / "manifest.json").read_text())
        self.assertFalse(manifest["dashboards/casa.yaml"])


if __name__ == "__main__":
    unittest.main()
