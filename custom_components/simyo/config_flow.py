"""Simyo setup and reauthentication, including the observed OTP challenge."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    SimyoApiError,
    SimyoAuthError,
    SimyoClient,
    SimyoInvalidCode,
    SimyoMfaRequired,
    SimyoRateLimited,
)
from .const import (
    CONF_AUTO_REAUTH_BLOCKED,
    CONF_DEVICE_ID,
    CONF_PHONE_NUMBER,
    CONF_SESSION_TOKEN,
    CONF_VERIFICATION_CODE,
    CONF_WEB_SESSION,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)
STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PHONE_NUMBER): str,
        vol.Required(CONF_PASSWORD): str,
    }
)
STEP_MFA_SCHEMA = vol.Schema({vol.Required(CONF_VERIFICATION_CODE): str})


def _connection_error(err: SimyoApiError) -> str:
    _LOGGER.warning("Simyo login failed: reason=%s status=%s", err.reason, err.status)
    if err.reason == "client_update_required":
        return "client_update_required"
    if err.reason in ("missing_session_token", "invalid_login_response"):
        return "unexpected_response"
    return "cannot_connect"


class SimyoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Never persist a challenge cookie, pending token or verification code."""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._device_id = str(uuid4())
        self._client: SimyoClient | None = None
        self._pending_data: dict[str, Any] | None = None

    async def _begin_login(self, data: dict[str, Any]) -> str | None:
        self._pending_data = dict(data)
        self._client = SimyoClient(
            async_get_clientsession(self.hass),
            data[CONF_PHONE_NUMBER],
            data[CONF_PASSWORD],
            self._device_id,
        )
        try:
            await self._client.login()
        except SimyoMfaRequired:
            return "mfa"
        except SimyoRateLimited:
            error = "rate_limited"
        except SimyoAuthError:
            error = "invalid_auth"
        except SimyoApiError as err:
            error = _connection_error(err)
        else:
            return None
        self._client = None
        self._pending_data = None
        return error

    async def _finish(self) -> ConfigFlowResult:
        if (
            not self._client
            or not self._client.session_token
            or not self._pending_data
            or not self._client.web_session.get("__Host-sessionKey")
        ):
            return self.async_abort(reason="mfa_expired")
        data = {
            **self._pending_data,
            CONF_DEVICE_ID: self._device_id,
            CONF_SESSION_TOKEN: self._client.session_token,
            CONF_WEB_SESSION: self._client.web_session,
            CONF_AUTO_REAUTH_BLOCKED: False,
        }
        self._pending_data = None
        self._client = None
        if self.context.get("source") == "reauth":
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data=data
            )
        return self.async_create_entry(
            title=f"Simyo {data[CONF_PHONE_NUMBER]}", data=data
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            phone = user_input[CONF_PHONE_NUMBER].strip()
            await self.async_set_unique_id(phone)
            self._abort_if_unique_id_configured()
            error = await self._begin_login(
                {CONF_PHONE_NUMBER: phone, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            )
            if error == "mfa":
                return await self.async_step_mfa()
            if error is None:
                return await self._finish()
            errors["base"] = error
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_mfa(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._client or not self._pending_data:
            return self.async_abort(reason="mfa_expired")
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._client.verify_code(
                    user_input[CONF_VERIFICATION_CODE].strip()
                )
            except SimyoInvalidCode:
                errors["base"] = "invalid_code"
            except SimyoRateLimited:
                errors["base"] = "rate_limited"
            except SimyoAuthError:
                self._client = None
                self._pending_data = None
                return self.async_abort(reason="mfa_expired")
            except SimyoApiError as err:
                errors["base"] = _connection_error(err)
            else:
                return await self._finish()
        return self.async_show_form(
            step_id="mfa", data_schema=STEP_MFA_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        self._device_id = entry.data.get(CONF_DEVICE_ID, self._device_id)
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._begin_login(
                {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            )
            if error == "mfa":
                return await self.async_step_mfa()
            if error is None:
                return await self._finish()
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"phone_number": entry.data[CONF_PHONE_NUMBER]},
            errors=errors,
        )
