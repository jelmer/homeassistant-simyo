"""Authentication regression tests; no real Simyo account is used."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, call, patch

from aiohttp import web

PACKAGE = Path(__file__).resolve().parents[1] / "custom_components/simyo"
package = types.ModuleType("simyo_client_test")
package.__path__ = [str(PACKAGE)]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location(
    "simyo_client_test.api", PACKAGE / "api.py"
)
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)


class Response:
    def __init__(self, status=200, body=None):
        self.status, self.body = status, body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, **kwargs):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class ClientTests(unittest.IsolatedAsyncioTestCase):
    def client(self, token=None):
        return api.SimyoClient(
            Mock(), "0698765478", "fake-password", "stable-device", token
        )

    async def pending_client(self):
        client = self.client()
        client._web_post = AsyncMock(
            return_value={
                "user": {
                    "sessionToken": "pending-secret",
                    "mfaStatus": "PENDING_VERIFICATION",
                }
            }
        )
        with self.assertRaises(api.SimyoMfaRequired):
            await client.login()
        return client

    async def test_pending_token_is_not_promoted(self):
        client = await self.pending_client()
        self.assertIsNone(client.session_token)
        with self.assertRaises(api.SimyoMfaRequired):
            await client.get_balance()
        client._session.get.assert_not_called()
        client._web_post.assert_awaited_once_with(
            "/auth/login",
            {
                "phoneNumber": "0698765478",
                "password": "fake-password",
            },
            bootstrap=True,
        )

    async def test_verify_code_sends_observed_payload_and_stores_only_final_token(self):
        client = await self.pending_client()
        cookies = client._auth_cookies
        client._web_post = AsyncMock(
            return_value={"result": {"token": "verified-secret"}}
        )
        await client.verify_code("987654")
        client._web_post.assert_has_awaits(
            [
                call(
                    "/api/post?endpoint=securityVerifyOtp",
                    {"rememberMe": True, "token": "987654"},
                ),
                call(
                    "/auth/update-token",
                    {"rememberMe": True, "token": "verified-secret"},
                ),
            ]
        )
        self.assertIs(client._auth_cookies, cookies)
        self.assertEqual(client.session_token, "verified-secret")
        self.assertFalse(client._pending_mfa)
        self.assertNotIn(
            "987654", [v for v in client.__dict__.values() if isinstance(v, str)]
        )

    async def test_invalid_local_code_never_reaches_server(self):
        client = await self.pending_client()
        client._web_post.reset_mock()
        for code in ("", "12345", "9876547", "abcdef", "１２３４５６"):
            with self.assertRaises(api.SimyoInvalidCode):
                await client.verify_code(code)
        client._web_post.assert_not_awaited()

    async def test_cannot_verify_without_a_challenge(self):
        client = self.client()
        with self.assertRaises(api.SimyoChallengeExpired):
            await client.verify_code("987654")

    async def test_no_mfa_login_retains_full_token(self):
        client = self.client()
        client._web_post = AsyncMock(
            return_value={
                "user": {
                    "sessionToken": "full-session",
                    "mfaStatus": "DISABLED_BY_CUSTOMER",
                }
            }
        )
        await client.login()
        self.assertEqual(client.session_token, "full-session")

    async def test_missing_or_pending_verification_response_is_not_success(self):
        for body in (
            {"result": {}},
            {"result": {"token": "restricted", "mfaStatus": "PENDING_VERIFICATION"}},
        ):
            client = await self.pending_client()
            client._web_post = AsyncMock(return_value=body)
            with self.assertRaises(api.SimyoApiError):
                await client.verify_code("987654")
            self.assertIsNone(client.session_token)

    async def test_restored_website_session_is_used_without_login(self):
        client, session, manager = await self.web_response(
            Response(body={"result": {"balance": 7.5}})
        )
        client._website_ready = True
        client.login = AsyncMock()
        with patch.object(api.aiohttp, "ClientSession", return_value=manager):
            await client.get_balance()
        client.login.assert_not_awaited()
        self.assertEqual(
            session.get.call_args.kwargs["params"], {"endpoint": "prepaidOverview"}
        )
        self.assertEqual(session.get.call_args.args[0], api.WEB_BASE + "/api/get")
        client._session.get.assert_not_called()

    async def test_rejected_data_session_does_not_send_another_otp(self):
        client, session, manager = await self.web_response(Response(401))
        client._website_ready = True
        client.login = AsyncMock()
        with (
            patch.object(api.aiohttp, "ClientSession", return_value=manager),
            self.assertRaises(api.SimyoAuthError),
        ):
            await client.get_usage()
        client.login.assert_not_awaited()
        session.post.assert_not_called()
        self.assertIsNone(client.session_token)
        self.assertFalse(client._website_ready)

    async def test_old_app_token_requires_explicit_reauthentication(self):
        client = self.client("old-app-only-token")
        client.login = AsyncMock()
        with self.assertRaises(api.SimyoAuthError):
            await client.get_balance()
        client.login.assert_not_awaited()

    async def test_cookie_sessions_are_not_shared_between_accounts(self):
        self.assertIsNot(self.client()._auth_cookies, self.client()._auth_cookies)

    async def web_response(self, response, pending=False):
        client = self.client()
        client._pending_mfa = pending
        session = Mock()
        session.post.return_value = response
        session.get.return_value = response
        manager = Mock()
        manager.__aenter__ = AsyncMock(return_value=session)
        manager.__aexit__ = AsyncMock(return_value=False)
        return client, session, manager

    async def test_proxy_error_envelope_invalid_code_retains_challenge(self):
        client, session, manager = await self.web_response(
            Response(
                body={
                    "error": {
                        "statusCode": 400,
                        "data": {"attemptsLeft": 2},
                        "message": "private-value",
                    }
                }
            ),
            pending=True,
        )
        with (
            patch.object(api.aiohttp, "ClientSession", return_value=manager),
            self.assertRaises(api.SimyoInvalidCode) as error,
        ):
            await client._web_post("/api/post?endpoint=securityVerifyOtp", {})
        self.assertTrue(client._pending_mfa)
        self.assertNotIn("private-value", str(error.exception))
        self.assertEqual(session.post.call_count, 1)

    async def test_expired_proxy_cookie_clears_challenge(self):
        client, _, manager = await self.web_response(
            Response(body={"error": {"code": "caller:badSession"}}), True
        )
        with (
            patch.object(api.aiohttp, "ClientSession", return_value=manager),
            self.assertRaises(api.SimyoChallengeExpired),
        ):
            await client._web_post("/api/post?endpoint=securityVerifyOtp", {})
        self.assertFalse(client._pending_mfa)

    async def test_rate_limit_does_not_resubmit_otp(self):
        for body, status in (({}, 429), ({"error": {"statusCode": 429}}, 200)):
            client, session, manager = await self.web_response(
                Response(status, body), True
            )
            with (
                patch.object(api.aiohttp, "ClientSession", return_value=manager),
                self.assertRaises(api.SimyoRateLimited),
            ):
                await client._web_post("/api/post?endpoint=securityVerifyOtp", {})
            self.assertEqual(session.post.call_count, 1)

    async def test_login_http_and_json_failures_are_redacted(self):
        for response, exception in (
            (Response(426, {"secret": "private-value"}), api.SimyoApiError),
            (Response(401, {"secret": "private-value"}), api.SimyoAuthError),
            (Response(503, {"secret": "private-value"}), api.SimyoApiError),
            (Response(200, ValueError("private-value")), api.SimyoApiError),
        ):
            client, _, manager = await self.web_response(response)
            with (
                patch.object(api.aiohttp, "ClientSession", return_value=manager),
                self.assertRaises(exception) as error,
            ):
                await client._web_post("/auth/login", {})
            self.assertNotIn("private-value", str(error.exception))

    async def test_fetch_failure_does_not_leak_body(self):
        client, _, manager = await self.web_response(
            Response(500, {"message": "private-usage"})
        )
        client._website_ready = True
        with (
            patch.object(api.aiohttp, "ClientSession", return_value=manager),
            self.assertRaises(api.SimyoApiError) as error,
        ):
            await client.get_balance()
        self.assertNotIn("private-usage", str(error.exception))
        self.assertEqual(error.exception.reason, "prepaidOverview_http_error")
        self.assertEqual(error.exception.status, 500)

    async def test_real_http_cookie_continuity(self):
        """Exercise real aiohttp cookies/connectors against a local fake Simyo."""
        seen = []

        async def page(request):
            response = web.Response(text="login")
            response.set_cookie("bootstrap", "test-bootstrap")
            return response

        async def login(request):
            self.assertEqual(request.cookies.get("bootstrap"), "test-bootstrap")
            response = web.json_response(
                {
                    "user": {
                        "mfaStatus": "PENDING_VERIFICATION",
                        "sessionToken": "restricted",
                    }
                }
            )
            response.set_cookie("test-session", "pending-cookie")
            return response

        async def verify(request):
            self.assertEqual(request.cookies.get("test-session"), "pending-cookie")
            seen.append(await request.json())
            return web.json_response({"result": {"token": "verified"}})

        async def update_token(request):
            self.assertEqual(request.cookies.get("test-session"), "pending-cookie")
            self.assertEqual(
                await request.json(), {"token": "verified", "rememberMe": True}
            )
            response = web.Response(status=204)
            response.set_cookie("__Host-sessionKey", "completed-cookie")
            return response

        async def data(request):
            self.assertEqual(
                request.cookies.get("__Host-sessionKey"), "completed-cookie"
            )
            endpoint = request.query["endpoint"]
            self.assertIn(endpoint, ("prepaidOverview", "getAggregated"))
            return web.json_response({"result": {"balance": 7.5}})

        app = web.Application()
        app.router.add_get("/inloggen", page)
        app.router.add_post("/auth/login", login)
        app.router.add_post("/api/post", verify)
        app.router.add_post("/auth/update-token", update_token)
        app.router.add_get("/api/get", data)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            client = self.client()
            with patch.object(api, "WEB_BASE", f"http://localhost:{port}"):
                with self.assertRaises(api.SimyoMfaRequired):
                    await client.login()
                await client.verify_code("987654")
                data_result = await client.fetch_all()
                self.assertEqual(data_result["balance"]["result"]["balance"], 7.5)
                restored = api.SimyoClient(
                    Mock(),
                    "0600000000",
                    "fake",
                    session_token=client.session_token,
                    web_session=client.web_session,
                )
                restored.login = AsyncMock()
                await restored.fetch_all()
                restored.login.assert_not_awaited()
            self.assertEqual(client.session_token, "verified")
            self.assertEqual(seen, [{"rememberMe": True, "token": "987654"}])
        finally:
            await runner.cleanup()


if __name__ == "__main__":
    unittest.main()
