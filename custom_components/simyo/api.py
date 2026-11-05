"""Account-isolated client for the Simyo Android app API."""

from __future__ import annotations

from math import isfinite
from uuid import uuid4

import aiohttp

from .const import API_BASE, CLIENT_PLATFORM, CLIENT_TOKEN, CLIENT_VERSION, USER_AGENT


class SimyoAuthError(Exception):
    """Credentials/session rejected; never include provider response values."""


class SimyoDataAuthError(SimyoAuthError):
    def __init__(self, endpoint: str, status: int | None = None):
        self.endpoint, self.status = endpoint, status
        super().__init__("App data session rejected")


class SimyoMfaRequired(SimyoAuthError):
    """A code must be submitted against the in-memory challenge token."""


class SimyoInvalidCode(SimyoAuthError):
    """Code rejected; never automatically resubmit it."""


class SimyoChallengeExpired(SimyoAuthError):
    """Start a new login instead of reusing an expired challenge."""


class SimyoRateLimited(Exception):
    """Do not retry automatically."""


class SimyoApiError(Exception):
    def __init__(self, reason: str, *, status: int | None = None):
        self.reason, self.status = reason, status
        super().__init__(f"{reason} (HTTP {status})" if status else reason)


def _result(data):
    return data.get("result", data) if isinstance(data, dict) else None


class SimyoClient:
    """Persist app tokens and device identity; website cookies are never used."""

    def __init__(
        self,
        session,
        phone_number,
        password,
        device_id=None,
        session_token=None,
        auto_reauth_blocked=False,
    ):
        self._phone_number = phone_number
        self._password = password
        self._device_id = device_id or str(uuid4())
        self._session_token = session_token
        self._challenge_token = None
        self._pending_mfa = False
        self._auto_reauth_blocked = auto_reauth_blocked

    @property
    def phone_number(self):
        return self._phone_number

    @property
    def device_id(self):
        return self._device_id

    @property
    def session_token(self):
        return self._session_token if not self._pending_mfa else None

    @property
    def auto_reauth_blocked(self):
        return self._auto_reauth_blocked

    async def _request(self, method, path, payload=None, *, token=None, stage="data"):
        headers = {
            "Accept": "application/json",
            "X-Client-Token": CLIENT_TOKEN,
            "X-Client-Platform": CLIENT_PLATFORM,
            "X-Client-Version": CLIENT_VERSION,
            "X-Device-ID": self._device_id,
            "User-Agent": USER_AGENT,
        }
        if token:
            headers["X-Session-Token"] = token
        try:
            async with (
                aiohttp.ClientSession(
                    cookie_jar=aiohttp.DummyCookieJar(),
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as session,
                session.request(
                    method,
                    API_BASE + path,
                    json=payload,
                    headers=headers,
                    allow_redirects=False,
                ) as response,
            ):
                status = response.status
                if status == 429:
                    raise SimyoRateLimited("Simyo request limit reached")
                if status == 426:
                    raise SimyoApiError("client_update_required", status=status)
                if status >= 500 or 300 <= status < 400:
                    raise SimyoApiError("app_http_error", status=status)
                try:
                    data = await response.json(content_type=None)
                except (ValueError, UnicodeError):
                    if status in (401, 403):
                        data = {}
                    else:
                        raise SimyoApiError(
                            "invalid_login_response"
                            if stage != "data"
                            else "invalid_data_response",
                            status=status,
                        ) from None
        except (aiohttp.ClientError, TimeoutError):
            raise SimyoApiError("app_transport_error") from None
        if not isinstance(data, dict):
            raise SimyoApiError(
                "invalid_login_response"
                if stage != "data"
                else "invalid_data_response",
                status=status,
            )
        error = data.get("error")
        # Both direct app errors and website-style envelopes occur in versions
        # of this API. Never expose the provider's message (may contain PII).
        detail = error if isinstance(error, dict) else data
        code = detail.get("code")
        message = detail.get("message")
        rejected = (
            status in (401, 403)
            or code
            in (
                "caller:badSession",
                "caller:missingSessionToken",
                "caller:invalidCredentials",
            )
            or message in ("bad session", "missing sessionToken", "invalid credentials")
        )
        if stage == "verify" and (rejected or status == 400 or error):
            if status in (401, 403) or code in (
                "caller:badSession",
                "caller:missingSessionToken",
            ):
                self._challenge_token = None
                self._pending_mfa = False
                raise SimyoChallengeExpired("App verification session expired")
            raise SimyoInvalidCode("Verification code rejected")
        if rejected:
            if stage == "data":
                self._session_token = None
                raise SimyoDataAuthError(path, status)
            raise SimyoAuthError("App credentials rejected")
        if status >= 400 or error:
            if stage == "login" and status == 400:
                raise SimyoAuthError("App credentials rejected")
            raise SimyoApiError("app_api_error", status=status)
        return data

    @staticmethod
    def _extract_session_token(data):
        result = _result(data)
        if not isinstance(result, dict):
            return None
        for key in ("sessionToken", "token"):
            token = result.get(key)
            if isinstance(token, str) and token.strip():
                return token
        return None

    def _accept_session(self, data):
        result = _result(data)
        if not isinstance(result, dict):
            raise SimyoApiError("invalid_login_response")
        token = self._extract_session_token(data)
        if not token:
            raise SimyoApiError("missing_session_token")
        if result.get("mfaStatus") == "PENDING_VERIFICATION":
            self._session_token = None
            self._challenge_token = token
            self._pending_mfa = True
            raise SimyoMfaRequired("Second-factor verification required")
        # Unknown MFA statuses must never promote a restricted token.
        if result.get("mfaStatus") not in (
            None,
            "ACTIVE",
            "DISABLED_BY_CUSTOMER",
            "VERIFIED",
        ):
            raise SimyoApiError("invalid_login_response")
        self._session_token = token
        self._challenge_token = None
        self._pending_mfa = False

    async def login(self):
        self._session_token = self._challenge_token = None
        self._pending_mfa = False
        data = await self._request(
            "POST",
            "/v1/sessions",
            {"phoneNumber": self._phone_number, "password": self._password},
            stage="login",
        )
        self._accept_session(data)

    async def verify_code(self, code):
        if not self._pending_mfa or not self._challenge_token:
            raise SimyoChallengeExpired("No pending app verification")
        if len(code) != 6 or not code.isascii() or not code.isdigit():
            raise SimyoInvalidCode("Enter a six-digit verification code")
        try:
            data = await self._request(
                "POST",
                "/v2/security.verifyOTP",
                {"rememberMe": True, "token": code},
                token=self._challenge_token,
                stage="verify",
            )
        except SimyoApiError:
            # The OTP may have been accepted before a timeout or malformed
            # response. Do not allow the same challenge to be replayed.
            self._challenge_token = None
            self._pending_mfa = False
            raise SimyoChallengeExpired(
                "App verification outcome unknown; restart login"
            ) from None
        self._accept_session(data)
        self._auto_reauth_blocked = False

    async def _get(self, path, *, method="GET", payload=None):
        if self._pending_mfa:
            raise SimyoMfaRequired("Complete app verification first")
        if not self._session_token:
            raise SimyoDataAuthError(path)
        data = await self._request(method, path, payload, token=self._session_token)
        # Persist explicit replacement tokens, never arbitrary response headers.
        replacement = self._extract_session_token(data)
        if replacement:
            result = _result(data)
            if result.get("mfaStatus") == "PENDING_VERIFICATION":
                self._session_token = None
                raise SimyoDataAuthError(path)
            self._session_token = replacement
        return data if "result" in data else {"result": data}

    async def get_balance(self):
        data = await self._get("/v1/overview/prepaid")
        result = data.get("result")
        if not isinstance(result, dict):
            raise SimyoApiError("invalid_data_response")
        candidates = [result.get("balance")]
        if isinstance(result.get("creditBalance"), dict):
            candidates.append(result["creditBalance"].get("amount"))
        amount = next(
            (
                value
                for value in candidates
                if isinstance(value, (int, float))
                and not isinstance(value, bool)
                and isfinite(value)
            ),
            None,
        )
        if amount is None:
            raise SimyoApiError("prepaid_balance_value_missing")
        # Keep the existing entity's numeric contract for the app's credit DTO.
        return {"result": {**result, "balance": amount}}

    async def get_usage(self):
        return await self._get("/v2/usage.aggregated", method="POST", payload={})

    async def _fetch_all_once(self):
        return {"balance": await self.get_balance(), "usage": await self.get_usage()}

    async def fetch_all(self):
        try:
            return await self._fetch_all_once()
        except SimyoMfaRequired:
            raise
        except SimyoAuthError:
            if self._auto_reauth_blocked or not self._password:
                raise
        # One recovery attempt, using the same persisted device ID. Failed login,
        # MFA, rate limit or transport keeps this blocked across HA restarts.
        self._auto_reauth_blocked = True
        await self.login()
        result = await self._fetch_all_once()
        self._auto_reauth_blocked = False
        return result
