"""Flow state-machine tests using a small HA interface stub, not a running HA."""

import importlib.util
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_api import PACKAGE, api


class FlowBase:
    def __init_subclass__(cls, **kwargs):
        pass

    def __init__(self):
        self.context = {}
        self.hass = Mock()

    async def async_set_unique_id(self, value):
        self.unique_id = value

    def _abort_if_unique_id_configured(self):
        pass

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}

    def _get_reauth_entry(self):
        return self.entry

    def async_update_reload_and_abort(self, entry, **kwargs):
        return {"type": "reauth_complete", **kwargs}


stubs = {
    name: types.ModuleType(name)
    for name in (
        "homeassistant",
        "homeassistant.config_entries",
        "homeassistant.const",
        "homeassistant.helpers",
        "homeassistant.helpers.aiohttp_client",
    )
}
stubs["homeassistant.config_entries"].ConfigFlow = FlowBase
stubs["homeassistant.config_entries"].ConfigFlowResult = dict
stubs["homeassistant.const"].CONF_PASSWORD = "password"
stubs["homeassistant.helpers.aiohttp_client"].async_get_clientsession = lambda hass: (
    Mock()
)
spec = importlib.util.spec_from_file_location(
    "simyo_client_test.config_flow", PACKAGE / "config_flow.py"
)
flow_module = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, stubs):
    spec.loader.exec_module(flow_module)


class FlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = Mock(
            session_token=None,
        )
        self.client.login = AsyncMock(side_effect=api.SimyoMfaRequired("required"))
        self.client.verify_code = AsyncMock(side_effect=self.verify_success)
        self.client_factory = patch.object(
            flow_module, "SimyoClient", return_value=self.client
        )
        self.factory = self.client_factory.start()
        self.addCleanup(self.client_factory.stop)
        self.flow = flow_module.SimyoConfigFlow()

    async def verify_success(self, code):
        self.client.session_token = "verified-session"

    async def begin(self):
        return await self.flow.async_step_user(
            {"phone_number": "0698765478", "password": "fake-password"}
        )

    async def test_new_entry_requires_code_then_stores_only_final_token(self):
        result = await self.begin()
        self.assertEqual(result["step_id"], "mfa")
        result = await self.flow.async_step_mfa({"verification_code": "987654"})
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["session_token"], "verified-session")
        self.assertNotIn("verification_code", result["data"])
        self.assertNotIn("987654", result["data"].values())
        self.assertIsNone(self.flow._client)
        self.assertIsNone(self.flow._pending_data)
        self.client.login.assert_awaited_once()
        self.client.verify_code.assert_awaited_once_with("987654")

    async def test_invalid_code_retains_same_client_and_credentials(self):
        await self.begin()
        client = self.flow._client
        self.client.verify_code.side_effect = api.SimyoInvalidCode("rejected")
        result = await self.flow.async_step_mfa({"verification_code": "987654"})
        self.assertEqual(result["errors"]["base"], "invalid_code")
        self.assertIs(self.flow._client, client)
        self.assertIsNotNone(self.flow._pending_data)
        self.factory.assert_called_once()

    async def test_expired_challenge_aborts_and_forgets_credentials(self):
        await self.begin()
        self.client.verify_code.side_effect = api.SimyoChallengeExpired("expired")
        result = await self.flow.async_step_mfa({"verification_code": "987654"})
        self.assertEqual(result, {"type": "abort", "reason": "mfa_expired"})
        self.assertIsNone(self.flow._pending_data)
        self.assertIsNone(self.flow._client)

    async def test_rate_limit_displays_error_without_retry(self):
        await self.begin()
        self.client.verify_code.side_effect = api.SimyoRateLimited("rate limited")
        result = await self.flow.async_step_mfa({"verification_code": "987654"})
        self.assertEqual(result["errors"]["base"], "rate_limited")
        self.assertEqual(self.client.verify_code.await_count, 1)

    async def test_transport_failure_keeps_pending_form(self):
        await self.begin()
        self.client.verify_code.side_effect = api.SimyoApiError("login_transport_error")
        with self.assertLogs(flow_module._LOGGER, level="WARNING") as logs:
            result = await self.flow.async_step_mfa({"verification_code": "987654"})
        self.assertEqual(result["errors"]["base"], "cannot_connect")
        self.assertNotIn("987654", str(logs.output))
        self.assertNotIn("fake-password", str(logs.output))

    async def test_reauth_reuses_device_and_replaces_session(self):
        self.flow.context = {"source": "reauth"}
        self.flow.entry = Mock(
            data={
                "phone_number": "0698765478",
                "password": "old-password",
                "device_id": "saved-device",
                "session_token": "expired-session",
            }
        )
        result = await self.flow.async_step_reauth_confirm({"password": "new-password"})
        self.assertEqual(result["step_id"], "mfa")
        self.assertEqual(self.factory.call_args.args[3], "saved-device")
        result = await self.flow.async_step_mfa({"verification_code": "987654"})
        self.assertEqual(result["type"], "reauth_complete")
        self.assertEqual(result["data"]["session_token"], "verified-session")
        self.assertEqual(result["data"]["password"], "new-password")
        self.assertEqual(result["data"]["device_id"], "saved-device")
        self.assertFalse(result["data"]["auto_reauth_blocked"])
        self.assertNotIn("verification_code", result["data"])

    async def test_non_mfa_login_still_completes(self):
        self.client.login.side_effect = None
        self.client.session_token = "full-session"
        result = await self.begin()
        self.assertEqual(result["type"], "create_entry")
        self.client.verify_code.assert_not_awaited()

    async def test_rejected_password_does_not_leave_pending_credentials(self):
        self.client.login.side_effect = api.SimyoAuthError("invalid")
        result = await self.begin()
        self.assertEqual(result["errors"]["base"], "invalid_auth")
        self.assertIsNone(self.flow._pending_data)
        self.assertIsNone(self.flow._client)


if __name__ == "__main__":
    unittest.main()
