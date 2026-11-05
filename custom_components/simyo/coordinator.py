"""DataUpdateCoordinator for the Simyo integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SimyoApiError, SimyoAuthError, SimyoClient, SimyoRateLimited
from .const import (
    CONF_AUTO_REAUTH_BLOCKED,
    CONF_SESSION_TOKEN,
    CONF_WEB_SESSION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class SimyoCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinates polling of the Simyo API."""

    def __init__(
        self, hass: HomeAssistant, client: SimyoClient, entry: ConfigEntry
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{client.phone_number}",
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client
        self.entry = entry

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self.client.fetch_all()
            if self.client.session_token and (
                self.client.session_token != self.entry.data.get(CONF_SESSION_TOKEN)
                or self.client.web_session != self.entry.data.get(CONF_WEB_SESSION)
            ):
                self.hass.config_entries.async_update_entry(
                    self.entry,
                    data={
                        **self.entry.data,
                        CONF_SESSION_TOKEN: self.client.session_token,
                        CONF_WEB_SESSION: self.client.web_session,
                    },
                )
            return data
        except SimyoAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SimyoApiError as err:
            raise UpdateFailed(str(err)) from err
        except SimyoRateLimited as err:
            raise UpdateFailed(str(err)) from err
        finally:
            if self.client.auto_reauth_blocked != bool(
                self.entry.data.get(CONF_AUTO_REAUTH_BLOCKED)
            ):
                self.hass.config_entries.async_update_entry(
                    self.entry,
                    data={
                        **self.entry.data,
                        CONF_AUTO_REAUTH_BLOCKED: self.client.auto_reauth_blocked,
                    },
                )
