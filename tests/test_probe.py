"""Ensure diagnostic output does not contain response values."""

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "tools/probe_login.py"
if not path.exists():
    path = Path(__file__).resolve().parent / "probe_login.py"
spec = importlib.util.spec_from_file_location("simyo_probe_test", path)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ProbeTests(unittest.TestCase):
    def test_nested_account_and_token_values_are_redacted(self):
        raw = {
            "result": {
                "sessionToken": "session-secret",
                "password": "password-secret",
                "phoneNumber": "0612345678",
                "code": 123456,
                "challenge": ["otp-secret", {"token": "nested-secret"}],
                "mfaStatus": "PENDING_VERIFICATION",
            }
        }
        output = json.dumps(probe.response_shape(raw))
        for secret in (
            "session-secret",
            "password-secret",
            "0612345678",
            "123456",
            "otp-secret",
            "nested-secret",
        ):
            self.assertNotIn(secret, output)
        self.assertIn("PENDING_VERIFICATION", output)

    def test_unrecognized_status_is_not_echoed(self):
        output = json.dumps(probe.response_shape({"mfaStatus": "private-response"}))
        self.assertNotIn("private-response", output)


if __name__ == "__main__":
    unittest.main()
