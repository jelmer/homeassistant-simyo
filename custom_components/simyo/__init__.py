"""The Simyo integration."""

from __future__ import annotations

from uuid import uuid4

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SimyoClient
from .const import (
    AUTH_TRANSPORT,
    CONF_AUTH_TRANSPORT,
    CONF_AUTO_REAUTH_BLOCKED,
    CONF_DEVICE_ID,
    CONF_PHONE_NUMBER,
    CONF_SESSION_TOKEN,
    DOMAIN,
)
from .coordinator import SimyoCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Simyo from a config entry."""
    session = async_get_clientsession(hass)
    data = dict(entry.data)
    data.setdefault(CONF_DEVICE_ID, str(uuid4()))
    if data.get(CONF_AUTH_TRANSPORT) != AUTH_TRANSPORT:
        data.pop(CONF_SESSION_TOKEN, None)
        data.pop("web_session", None)
        data[CONF_AUTO_REAUTH_BLOCKED] = False
        data[CONF_AUTH_TRANSPORT] = AUTH_TRANSPORT
    if data != entry.data:
        hass.config_entries.async_update_entry(entry, data=data)
    client = SimyoClient(
        session,
        entry.data[CONF_PHONE_NUMBER],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_DEVICE_ID],
        entry.data.get(CONF_SESSION_TOKEN),
        auto_reauth_blocked=bool(entry.data.get(CONF_AUTO_REAUTH_BLOCKED)),
    )
    coordinator = SimyoCoordinator(hass, client, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
