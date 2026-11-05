"""Simyo Prepaid client using one website session for login, OTP and data."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import aiohttp
from yarl import URL

from .const import USER_AGENT, WEB_BASE

SESSION_COOKIE_NAMES = {
    "__Host-sessionKey",
    "mfa-uuid",
    "traefik-sticky",
    "AWSALB",
    "AWSALBCORS",
}


class SimyoAuthError(Exception):
    """Credentials/session rejected; messages never include server data."""


class SimyoDataAuthError(SimyoAuthError):
    """Distinguish a rejected data session from an incorrect password."""

    def __init__(self, endpoint: str, status: int | None = None) -> None:
        self.endpoint = endpoint
        self.status = status
        super().__init__("Website data session rejected")


class SimyoMfaRequired(SimyoAuthError):
    """An OTP must be entered in the same, still-pending login flow."""


class SimyoInvalidCode(SimyoAuthError):
    """A code was rejected; do not automatically retry it."""


class SimyoChallengeExpired(SimyoAuthError):
    """The user must restart authentication."""


class SimyoRateLimited(Exception):
    """Stop requests until the user can try again."""


class SimyoApiError(Exception):
    """Only fixed local error categories and HTTP status are exposed."""

    def __init__(self, reason: str, *, status: int | None = None) -> None:
        self.reason = reason
        self.status = status
        super().__init__(f"{reason} (HTTP {status})" if status else reason)


class SimyoClient:
    """Keep account cookies out of Home Assistant's shared HTTP session."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        phone_number: str,
        password: str,
        device_id: str | None = None,
        session_token: str | None = None,
        web_session: dict[str, str] | None = None,
        auto_reauth_blocked: bool = False,
    ) -> None:
        self._session = session
        self._phone_number = phone_number
        self._password = password
        self._device_id = device_id or str(uuid4())
        self._session_token = session_token
        self._pending_mfa = False
        self._auto_reauth_blocked = auto_reauth_blocked
        self._automatic_login_attempted = False
        self._auth_cookies = aiohttp.CookieJar()
        if web_session:
            self._auth_cookies.update_cookies(
                {
                    key: value
                    for key, value in web_session.items()
                    if key in SESSION_COOKIE_NAMES and isinstance(value, str)
                },
                response_url=URL(WEB_BASE),
            )
        self._website_ready = bool(self.web_session.get("__Host-sessionKey"))

    @property
    def phone_number(self) -> str:
        return self._phone_number

    @property
    def session_token(self) -> str | None:
        """Only a completed session may be persisted by Home Assistant."""
        return self._session_token

    @property
    def auto_reauth_blocked(self) -> bool:
        """Persist failed recovery so restarts cannot repeatedly request an OTP."""
        return self._auto_reauth_blocked

    @property
    def web_session(self) -> dict[str, str]:
        """Only completed website auth/routing cookies, never a pending challenge."""
        if self._pending_mfa:
            return {}
        return {
            key: morsel.value
            for key, morsel in self._auth_cookies.filter_cookies(URL(WEB_BASE)).items()
            if key in SESSION_COOKIE_NAMES
        }

    async def _web_post(
        self, path: str, payload: dict[str, Any], *, bootstrap=False
    ) -> Any:
        """Keep website cookies in this client, never in HA's shared cookie jar.

        A short-lived session closes its connector on success, error or flow abort.
        Pending cookies remain in memory. Only completed, allowlisted website
        session cookies are exported for Home Assistant to restore after restart.
        """
        try:
            async with aiohttp.ClientSession(
                cookie_jar=self._auth_cookies,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={
                    "Accept": "application/json",
                    "Origin": WEB_BASE,
                    "Referer": f"{WEB_BASE}/inloggen",
                    "User-Agent": USER_AGENT,
                },
            ) as session:
                if bootstrap:
                    async with session.get(
                        f"{WEB_BASE}/inloggen", allow_redirects=False
                    ) as page:
                        if page.status >= 400:
                            raise SimyoApiError(
                                "login_bootstrap_error", status=page.status
                            )
                async with session.post(
                    f"{WEB_BASE}{path}", json=payload, allow_redirects=False
                ) as response:
                    status = response.status
                    if status == 426:
                        raise SimyoApiError("client_update_required", status=status)
                    if status == 429:
                        raise SimyoRateLimited("Too many authentication attempts")
                    if status in (401, 403):
                        if self._pending_mfa:
                            self._reset_challenge()
                            raise SimyoChallengeExpired("Verification session expired")
                        raise SimyoAuthError("Authentication rejected")
                    if status >= 500 or 300 <= status < 400:
                        raise SimyoApiError("login_http_error", status=status)
                    if (
                        path == "/auth/update-token"
                        and 200 <= status < 300
                        and (status == 204 or response.content_length == 0)
                    ):
                        return {}
                    try:
                        data = await response.json(content_type=None)
                    except (ValueError, UnicodeError):
                        raise SimyoApiError(
                            "invalid_login_response", status=status
                        ) from None
        except (TimeoutError, aiohttp.ClientError):
            raise SimyoApiError("login_transport_error") from None

        # The website proxy can return an error envelope with HTTP 200.
        error = data.get("error") if isinstance(data, dict) else None
        if error:
            if not isinstance(error, dict):
                raise SimyoApiError("invalid_login_response", status=status)
            code = error.get("code")
            error_status = error.get("statusCode", status)
            if code in (
                "caller:badSession",
                "caller:missingSessionToken",
                "caller:error",
            ):
                self._reset_challenge()
                raise SimyoChallengeExpired("Verification session expired")
            if error_status == 429:
                raise SimyoRateLimited("Too many authentication attempts")
            if self._pending_mfa and error_status == 400:
                raise SimyoInvalidCode("Verification code rejected")
            if error_status in (400, 401, 403):
                raise SimyoAuthError("Authentication rejected")
            raise SimyoApiError(
                "login_http_error",
                status=error_status if isinstance(error_status, int) else status,
            )
        if status >= 400:
            if self._pending_mfa and status == 400:
                raise SimyoInvalidCode("Verification code rejected")
            raise SimyoAuthError("Authentication rejected")
        return data

    def _reset_challenge(self, *, preserve_device: bool = False) -> None:
        self._pending_mfa = False
        if preserve_device:
            self._auth_cookies.clear(lambda cookie: cookie.key != "mfa-uuid")
        else:
            self._auth_cookies.clear()
        self._website_ready = False

    def _invalidate_data_session(self) -> None:
        self._session_token = None
        self._reset_challenge(preserve_device=True)

    async def login(self) -> None:
        """Begin website login; never promote a pending token to a data token."""
        self._session_token = None
        self._reset_challenge(preserve_device=True)
        data = await self._web_post(
            "/auth/login",
            {
                "phoneNumber": self._phone_number,
                "password": self._password,
            },
            bootstrap=True,
        )
        user = data.get("user") if isinstance(data, dict) else None
        if not isinstance(user, dict):
            raise SimyoApiError("invalid_login_response")
        if user.get("mfaStatus") == "PENDING_VERIFICATION":
            self._pending_mfa = True
            raise SimyoMfaRequired("Second-factor verification required")
        token = self._extract_session_token(user)
        if not token:
            raise SimyoApiError("missing_session_token")
        self._session_token = token
        self._pending_mfa = False
        self._website_ready = True

    async def verify_code(self, code: str) -> None:
        """Submit exactly once to the observed OTP endpoint in this cookie session."""
        if not self._pending_mfa:
            raise SimyoChallengeExpired("No pending verification session")
        # Observed website code is six decimal digits. Never persist the OTP.
        if len(code) != 6 or not code.isascii() or not code.isdigit():
            raise SimyoInvalidCode("Enter a six-digit verification code")
        data = await self._web_post(
            "/api/post?endpoint=securityVerifyOtp",
            {
                "rememberMe": True,
                "token": code,
            },
        )
        result = data.get("result") if isinstance(data, dict) else None
        if (
            not isinstance(result, dict)
            or result.get("mfaStatus") == "PENDING_VERIFICATION"
        ):
            raise SimyoApiError("invalid_login_response")
        token = self._extract_session_token(result)
        if not token:
            raise SimyoApiError("missing_session_token")
        # Exactly as the browser: install the verified token in its server-side
        # website session before fetching data. The OTP response alone does not
        # update __Host-sessionKey, and its token is not an app API credential.
        try:
            await self._web_post(
                "/auth/update-token", {"token": token, "rememberMe": True}
            )
        except (SimyoApiError, SimyoAuthError, SimyoRateLimited):
            self._reset_challenge()
            raise SimyoChallengeExpired(
                "Website session finalization failed; restart login"
            ) from None
        self._session_token = token
        self._pending_mfa = False
        self._website_ready = True

    @staticmethod
    def _extract_session_token(data: Any) -> str | None:
        if not isinstance(data, dict):
            return None
        for section in (data, data.get("result")):
            if isinstance(section, dict):
                for key in ("sessionToken", "session_token", "token"):
                    value = section.get(key)
                    if isinstance(value, str) and value.strip():
                        return value
        return None

    async def _get(self, endpoint: str) -> Any:
        if self._pending_mfa:
            raise SimyoMfaRequired("Second-factor verification required")
        if not self._website_ready:
            # An old app-only token cannot restore an encrypted website cookie.
            # Do not silently send another OTP while fetching data.
            raise SimyoAuthError(
                "Website session unavailable; reauthentication required"
            )
        try:
            async with (
                aiohttp.ClientSession(
                    cookie_jar=self._auth_cookies,
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers={
                        "Accept": "application/json",
                        "Origin": WEB_BASE,
                        "Referer": f"{WEB_BASE}/",
                        "User-Agent": USER_AGENT,
                    },
                ) as session,
                session.get(
                    f"{WEB_BASE}/api/get",
                    params={"endpoint": endpoint},
                    allow_redirects=False,
                ) as response,
            ):
                status = response.status
                if status in (401, 403) or 300 <= status < 400:
                    self._invalidate_data_session()
                    raise SimyoDataAuthError(endpoint, status)
                if status >= 400:
                    raise SimyoApiError(f"{endpoint}_http_error", status=status)
                data = await response.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError):
            raise SimyoApiError(f"{endpoint}_transport_error") from None
        except (ValueError, UnicodeError):
            raise SimyoApiError(f"{endpoint}_invalid_response") from None
        if not isinstance(data, dict):
            raise SimyoApiError(f"{endpoint}_invalid_response", status=status)
        error = data.get("error")
        if error:
            error_status = error.get("statusCode") if isinstance(error, dict) else None
            error_code = error.get("code") if isinstance(error, dict) else None
            if error_status in (401, 403) or error_code in (
                "caller:badSession",
                "caller:missingSessionToken",
            ):
                self._invalidate_data_session()
                raise SimyoDataAuthError(
                    endpoint, error_status if isinstance(error_status, int) else None
                )
            raise SimyoApiError(
                f"{endpoint}_api_error",
                status=error_status if isinstance(error_status, int) else status,
            )
        if "result" not in data:
            raise SimyoApiError(f"{endpoint}_invalid_response", status=status)
        return data

    async def get_balance(self) -> Any:
        return await self._get("prepaidOverview")

    async def get_usage(self) -> Any:
        return await self._get("getAggregated")

    async def fetch_all(self) -> dict[str, Any]:
        try:
            return await self._fetch_all_once()
        except SimyoAuthError:
            remembered_device = self._auth_cookies.filter_cookies(URL(WEB_BASE)).get(
                "mfa-uuid"
            )
            if (
                self._pending_mfa
                or self._auto_reauth_blocked
                or self._automatic_login_attempted
                or not remembered_device
            ):
                raise
        # Only recover with an already remembered device. Login may still require
        # MFA; in that case stop and let HA request user interaction, never loop.
        self._automatic_login_attempted = True
        self._auto_reauth_blocked = True
        await self.login()
        data = await self._fetch_all_once()
        self._auto_reauth_blocked = False
        self._automatic_login_attempted = False
        return data

    async def _fetch_all_once(self) -> dict[str, Any]:
        return {"balance": await self.get_balance(), "usage": await self.get_usage()}
