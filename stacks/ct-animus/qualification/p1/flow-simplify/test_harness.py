#!/usr/bin/env python3
"""Offline checks of the isolation harness; starts nothing."""
import importlib.util
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('t4_actor', ROOT / 'actor.py')
actor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(actor)


class HarnessTests(unittest.TestCase):
    def test_run_script_stays_isolated(self):
        script = (ROOT / 'run.sh').read_text()
        self.assertIn('docker network create --internal', script)
        for forbidden in (' -p ', '--publish', 'docker.sock', '--network host', '--privileged', 'docker pull'):
            self.assertNotIn(forbidden, script)
        runs = re.findall(r'docker run [^\n]*', script)
        self.assertEqual(len(runs), 4)
        common = re.search(r'common=\((.*?)\)', script, re.S).group(1)
        self.assertIn('--network "$n"', common)
        for line in runs:
            self.assertTrue('--network "$n"' in line or '--network "$(name)"' in line or '"${common[@]}"' in line, line)
        # Only the private state directory is mounted writable.
        writable = [m for m in re.findall(r'-v "([^"]+:[^"]+)"', script) if not m.endswith(':ro')]
        self.assertEqual(writable, ['$STATE:/state'])
        self.assertIn('stat -c %a "$STATE")" == 700', script)

    def test_up_receipt_records_provenance(self):
        script = (ROOT / 'run.sh').read_text()
        self.assertIn('{ provenance; exit 0; }', script)
        for field in ('"commit": git("rev-parse", "HEAD")', '"tree_clean"', '"blueprint_sha256"',
                      '"rollback_sha256"', '"auth_image_id"'):
            self.assertIn(field, script)

    def test_actor_reports_no_secrets_and_talks_only_to_the_fixture(self):
        source = (ROOT / 'actor.py').read_text()
        self.assertIn('http.client.HTTPConnection("auth-server", 9000', source)
        self.assertEqual(source.count('HTTPConnection('), 1)
        printed = re.findall(r'print\((.*)\)', source)
        self.assertEqual(printed, ['"T4ACTOR " + json.dumps(results, sort_keys=True)'])

    def test_totp_matches_rfc_6238(self):
        key = b'12345678901234567890'
        self.assertEqual(actor.hotp(key, 59 // 30, digits=8), 94287082)
        self.assertEqual(actor.hotp(key, 1111111109 // 30, digits=8), 7081804)

    def test_pkce_is_s256(self):
        verifier, challenge = actor.pkce()
        self.assertTrue(43 <= len(verifier) <= 128)
        self.assertRegex(challenge, r'^[A-Za-z0-9_-]{43}$')


if __name__ == '__main__':
    unittest.main(verbosity=1)
