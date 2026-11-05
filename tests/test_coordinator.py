"""Ensure automatic recovery cannot send repeated codes across HA reloads."""

import importlib
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_api import api


class CoordinatorBase:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, **kwargs):
        self.hass = hass


class AuthFailed(Exception):
    pass


class UpdateFailed(Exception):
    pass


stubs = {
    name: types.ModuleType(name)
    for name in (
        "homeassistant",
        "homeassistant.config_entries",
        "homeassistant.core",
        "homeassistant.exceptions",
        "homeassistant.helpers",
        "homeassistant.helpers.update_coordinator",
    )
}
stubs["homeassistant.config_entries"].ConfigEntry = object
stubs["homeassistant.core"].HomeAssistant = object
stubs["homeassistant.exceptions"].ConfigEntryAuthFailed = AuthFailed
stubs[
    "homeassistant.helpers.update_coordinator"
].DataUpdateCoordinator = CoordinatorBase
stubs["homeassistant.helpers.update_coordinator"].UpdateFailed = UpdateFailed
with patch.dict(sys.modules, stubs):
    module = importlib.import_module("simyo_client_test.coordinator")


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_recovery_block_is_persisted_before_auth_failure(self):
        entry = types.SimpleNamespace(
            data={"web_session": {"mfa-uuid": "remembered"}, "session_token": "old"}
        )
        client = Mock()
        client.phone_number = "0600000000"
        client.auto_reauth_blocked = True
        client.fetch_all = AsyncMock(
            side_effect=api.SimyoMfaRequired("verification required")
        )
        hass = Mock()
        coordinator = module.SimyoCoordinator(hass, client, entry)
        with self.assertRaises(AuthFailed):
            await coordinator._async_update_data()
        data = hass.config_entries.async_update_entry.call_args.kwargs["data"]
        self.assertTrue(data["auto_reauth_blocked"])
        self.assertEqual(data["web_session"], {"mfa-uuid": "remembered"})
        restored = api.SimyoClient(
            Mock(),
            "0600000000",
            "fake",
            web_session=data["web_session"],
            auto_reauth_blocked=data["auto_reauth_blocked"],
        )
        restored.login = AsyncMock()
        with self.assertRaises(api.SimyoAuthError):
            await restored.fetch_all()
        restored.login.assert_not_awaited()

    async def test_refreshed_session_is_saved_after_successful_recovery(self):
        entry = types.SimpleNamespace(
            data={"session_token": "old", "web_session": {"mfa-uuid": "remembered"}}
        )
        client = Mock()
        client.phone_number = "0600000000"
        client.auto_reauth_blocked = False
        client.session_token = "new"
        client.web_session = {
            "mfa-uuid": "remembered",
            "__Host-sessionKey": "new-cookie",
        }
        client.fetch_all = AsyncMock(return_value={"balance": 7.5})
        hass = Mock()
        self.assertEqual(
            await module.SimyoCoordinator(hass, client, entry)._async_update_data(),
            {"balance": 7.5},
        )
        data = hass.config_entries.async_update_entry.call_args.kwargs["data"]
        self.assertEqual(data["session_token"], "new")
        self.assertEqual(data["web_session"]["__Host-sessionKey"], "new-cookie")
