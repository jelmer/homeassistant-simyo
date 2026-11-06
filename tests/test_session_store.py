"""A data failure must retain a completed local session without prompting again."""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
store = importlib.import_module("session_store")
checker = importlib.import_module("verify_2fa")


class StoreTests(unittest.TestCase):
    def test_cache_is_account_bound_and_excludes_password_and_code(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            client = Mock(device_id="stable-device", session_token="fake-token")
            store.save_session(path, "0600000000", client)
            self.assertEqual(
                store.load_session(path, "0600000000")["session_token"], "fake-token"
            )
            self.assertIsNone(store.load_session(path, "0611111111"))
            self.assertNotIn("0600000000", path.read_text())
            self.assertNotIn("password", path.read_text())
            client.session_token = None
            store.save_session(path, "0600000000", client)
            self.assertEqual(
                store.load_session(path, "0600000000")["session_token"], "fake-token"
            )


class ReuseTests(unittest.IsolatedAsyncioTestCase):
    async def test_data_error_keeps_login_and_next_run_does_not_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            client = Mock(device_id="stable-device", session_token="verified")
            client.login = AsyncMock()
            client.fetch_all = AsyncMock(
                side_effect=checker.api.SimyoApiError("app_http_error", status=405)
            )
            with (
                patch.object(checker, "SESSION_PATH", path),
                patch.object(checker.api, "SimyoClient", return_value=client),
                patch.object(
                    checker.getpass, "getpass", return_value="fake-password"
                ) as prompt,
                patch("builtins.print"),
            ):
                self.assertEqual(await checker.check("0600000000"), 1)
                self.assertTrue(path.exists())
                prompt.assert_called_once()
                prompt.reset_mock()
                self.assertEqual(await checker.check("0600000000"), 1)
                prompt.assert_not_called()
                client.login.assert_awaited_once()
