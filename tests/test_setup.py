"""Migration must never send website credentials as app-session tokens."""

import importlib.util
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_api import PACKAGE

stubs = {
    name: types.ModuleType(name)
    for name in (
        "homeassistant",
        "homeassistant.config_entries",
        "homeassistant.const",
        "homeassistant.core",
        "homeassistant.helpers",
        "homeassistant.helpers.aiohttp_client",
    )
}
stubs["homeassistant.config_entries"].ConfigEntry = object
stubs["homeassistant.const"].CONF_PASSWORD = "password"
stubs["homeassistant.const"].Platform = types.SimpleNamespace(SENSOR="sensor")
stubs["homeassistant.core"].HomeAssistant = object
stubs["homeassistant.helpers.aiohttp_client"].async_get_clientsession = lambda hass: (
    Mock()
)
spec = importlib.util.spec_from_file_location(
    "simyo_client_test.setup_module", PACKAGE / "__init__.py"
)
module = importlib.util.module_from_spec(spec)
module.__package__ = "simyo_client_test"
stubs["simyo_client_test.coordinator"] = types.ModuleType(
    "simyo_client_test.coordinator"
)
stubs["simyo_client_test.coordinator"].SimyoCoordinator = Mock
with patch.dict(sys.modules, stubs):
    spec.loader.exec_module(module)


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    async def setup(self, data):
        self.entry = types.SimpleNamespace(data=dict(data), entry_id="existing-entry")
        self.hass = Mock(data={})
        self.hass.config_entries.async_update_entry.side_effect = lambda entry, **kw: (
            setattr(entry, "data", kw["data"])
        )
        self.hass.config_entries.async_forward_entry_setups = AsyncMock()
        coordinator = Mock()
        coordinator.async_config_entry_first_refresh = AsyncMock()
        with (
            patch.object(module, "SimyoCoordinator", return_value=coordinator),
            patch.object(module, "SimyoClient") as factory,
        ):
            await module.async_setup_entry(self.hass, self.entry)
            return factory.call_args

    async def test_old_website_session_is_invalidated_once_device_kept(self):
        args = await self.setup(
            {
                "phone_number": "0600000000",
                "password": "fake",
                "device_id": "same-device",
                "session_token": "website-token",
                "web_session": {"__Host-sessionKey": "old-cookie"},
                "auto_reauth_blocked": True,
            }
        )
        self.assertEqual(args.args[3], "same-device")
        self.assertIsNone(args.args[4])
        self.assertFalse(args.kwargs["auto_reauth_blocked"])
        self.assertEqual(self.entry.data["auth_transport"], "app_v1")
        self.assertNotIn("web_session", self.entry.data)
        self.assertEqual(self.entry.entry_id, "existing-entry")

    async def test_app_token_and_block_survive_restart_without_forced_login(self):
        args = await self.setup(
            {
                "phone_number": "0600000000",
                "password": "fake",
                "device_id": "same-device",
                "session_token": "app-token",
                "auth_transport": "app_v1",
                "auto_reauth_blocked": True,
            }
        )
        self.assertEqual(args.args[4], "app-token")
        self.assertTrue(args.kwargs["auto_reauth_blocked"])
        self.hass.config_entries.async_update_entry.assert_not_called()

    async def test_missing_device_id_is_persisted_before_network(self):
        args = await self.setup({"phone_number": "0600000000", "password": "fake"})
        self.assertEqual(args.args[3], self.entry.data["device_id"])
        self.assertTrue(args.args[3])
