"""App transport regressions using a local HTTP server; no Simyo account."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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


class AppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.answers = []

        async def handler(request):
            body = await request.json() if request.can_read_body else None
            self.requests.append(
                (request.method, request.path, dict(request.headers), body)
            )
            status, data = self.answers.pop(0)
            if isinstance(data, str):
                return web.Response(status=status, text=data)
            return web.json_response(data, status=status)

        app = web.Application()
        app.router.add_route("*", "/{path:.*}", handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.patch = patch.object(api, "API_BASE", f"http://127.0.0.1:{port}")
        self.patch.start()
        self.client = api.SimyoClient(
            Mock(), "0600000000", "fake-password", device_id="stable-device"
        )

    async def asyncTearDown(self):
        self.patch.stop()
        await self.runner.cleanup()

    async def test_mfa_full_lifecycle_and_recreated_client_use_only_app_route(self):
        self.answers = [
            (
                200,
                {
                    "result": {
                        "sessionToken": "restricted",
                        "mfaStatus": "PENDING_VERIFICATION",
                    }
                },
            ),
            (200, {"result": {"token": "verified", "mfaStatus": "ACTIVE"}}),
            (200, {"balance": 7.5}),
            (200, {"data": {"used": 123}}),
        ]
        with self.assertRaises(api.SimyoMfaRequired):
            await self.client.login()
        self.assertIsNone(self.client.session_token)
        await self.client.verify_code("123456")
        self.assertEqual(self.client.session_token, "verified")
        restored = api.SimyoClient(
            Mock(),
            "0600000000",
            "fake-password",
            device_id=self.client.device_id,
            session_token=self.client.session_token,
        )
        data = await restored.fetch_all()
        self.assertEqual(data["balance"]["result"]["balance"], 7.5)
        self.assertEqual(
            [r[1] for r in self.requests],
            [
                "/v1/sessions",
                "/v2/security.verifyOTP",
                "/v1/overview/prepaid",
                "/v2/usage.aggregated",
            ],
        )
        self.assertEqual(self.requests[1][3], {"token": "123456", "rememberMe": True})
        self.assertEqual(self.requests[1][2]["X-Session-Token"], "restricted")
        for method, path, headers, body in self.requests:
            self.assertEqual(headers["X-Device-ID"], "stable-device")
            self.assertEqual(headers["X-Client-Platform"], "android")
            self.assertEqual(headers["X-Client-Version"], "4.31.2")
            self.assertIn("X-Client-Token", headers)
            self.assertNotIn("Cookie", headers)
        self.assertNotIn("password", str(self.requests[2][3]))
        self.assertEqual(self.requests[2][0], "GET")
        self.assertEqual(self.requests[3][0], "POST")
        self.assertEqual(self.requests[3][3], {})

    async def test_wrong_code_can_retry_same_challenge_without_new_login(self):
        self.client._pending_mfa = True
        self.client._challenge_token = "restricted"
        self.answers = [
            (400, {"message": "invalid code"}),
            (200, {"token": "verified"}),
        ]
        with self.assertRaises(api.SimyoInvalidCode):
            await self.client.verify_code("123456")
        self.assertIsNone(self.client.session_token)
        await self.client.verify_code("654321")
        self.assertEqual(
            [r[2]["X-Session-Token"] for r in self.requests],
            ["restricted", "restricted"],
        )

    async def test_pending_cannot_fetch_data(self):
        self.client._pending_mfa = True
        with self.assertRaises(api.SimyoMfaRequired):
            await self.client.fetch_all()
        self.assertFalse(self.requests)

    async def test_bad_code_format_never_reaches_network(self):
        self.client._pending_mfa = True
        self.client._challenge_token = "restricted"
        for code in ("abc123", "12345", "１２３４５６"):
            with self.assertRaises(api.SimyoInvalidCode):
                await self.client.verify_code(code)
        self.assertFalse(self.requests)

    async def test_automatic_recovery_has_same_device_and_retries_data_once(self):
        self.client._session_token = "expired"
        self.answers = [
            (401, {}),
            (200, {"sessionToken": "new"}),
            (200, {"balance": 7.5}),
            (200, {}),
        ]
        await self.client.fetch_all()
        self.assertEqual(self.client.session_token, "new")
        self.assertFalse(self.client.auto_reauth_blocked)
        self.assertEqual(sum(r[1] == "/v1/sessions" for r in self.requests), 1)

    async def test_mfa_recovery_block_survives_new_client(self):
        self.client._session_token = "expired"
        self.answers = [
            (401, {}),
            (200, {"sessionToken": "restricted", "mfaStatus": "PENDING_VERIFICATION"}),
        ]
        with self.assertRaises(api.SimyoMfaRequired):
            await self.client.fetch_all()
        self.assertTrue(self.client.auto_reauth_blocked)
        restored = api.SimyoClient(
            Mock(),
            "0600000000",
            "fake",
            device_id="stable-device",
            auto_reauth_blocked=True,
        )
        with self.assertRaises(api.SimyoDataAuthError):
            await restored.fetch_all()
        self.assertEqual(len(self.requests), 2)

    async def test_failed_recovery_does_not_loop(self):
        self.client._session_token = "expired"
        self.answers = [(401, {}), (401, {"message": "private password"})]
        with self.assertRaises(api.SimyoAuthError) as error:
            await self.client.fetch_all()
        self.assertNotIn("private password", str(error.exception))
        with self.assertRaises(api.SimyoAuthError):
            await self.client.fetch_all()
        self.assertEqual(len(self.requests), 2)

    async def test_rate_limit_does_not_start_login_or_echo_response(self):
        self.client._session_token = "valid"
        self.answers = [(429, {"message": "secret"})]
        with self.assertRaises(api.SimyoRateLimited) as error:
            await self.client.fetch_all()
        self.assertNotIn("secret", str(error.exception))
        self.assertEqual(len(self.requests), 1)

    async def test_redirect_never_sends_token_to_other_host(self):
        self.client._session_token = "valid"
        self.answers = [(302, {})]
        with self.assertRaises(api.SimyoApiError):
            await self.client.get_balance()
        self.assertEqual(len(self.requests), 1)

    async def test_missing_or_unknown_status_token_not_promoted(self):
        for body in (
            {"mfaStatus": "ACTIVE"},
            {"token": "restricted", "mfaStatus": "UNRECOGNIZED"},
        ):
            self.answers = [(200, body)]
            with self.assertRaises(api.SimyoApiError):
                await self.client.login()
            self.assertIsNone(self.client.session_token)

    async def test_explicit_token_replacement_used_on_next_data_request(self):
        self.client._session_token = "old"
        self.answers = [(200, {"balance": 7.5, "sessionToken": "new"}), (200, {})]
        await self.client.fetch_all()
        self.assertEqual(self.requests[1][2]["X-Session-Token"], "new")

    async def test_no_recovery_on_server_error_or_html_response(self):
        for answer in ((503, {}), (200, "<html>private data</html>")):
            self.answers = [answer]
            self.client._session_token = "valid"
            with self.assertRaises(api.SimyoApiError) as error:
                await self.client.fetch_all()
            self.assertNotIn("private data", str(error.exception))
        self.assertEqual(len(self.requests), 2)

    async def test_uncertain_otp_result_cannot_be_replayed(self):
        self.client._pending_mfa = True
        self.client._challenge_token = "restricted"
        self.answers = [(200, "<html>unexpected</html>")]
        with self.assertRaises(api.SimyoChallengeExpired):
            await self.client.verify_code("123456")
        with self.assertRaises(api.SimyoChallengeExpired):
            await self.client.verify_code("123456")
        self.assertEqual(len(self.requests), 1)
        self.assertIsNone(self.client.session_token)

    async def test_html_auth_rejection_still_maps_to_auth_failure(self):
        self.client._session_token = "expired"
        self.answers = [(401, "<html>unauthorized</html>")]
        with self.assertRaises(api.SimyoDataAuthError):
            await self.client.get_balance()
        self.assertIsNone(self.client.session_token)

    async def test_app_credit_dto_preserves_existing_balance_sensor_contract(self):
        self.client._session_token = "valid"
        self.answers = [(200, {"creditBalance": {"amount": 0, "currencyType": "EUR"}})]
        result = await self.client.get_balance()
        self.assertEqual(result["result"]["balance"], 0)

    async def test_malformed_balance_does_not_silently_show_success(self):
        self.client._session_token = "valid"
        for value in (None, True, {"account": "private"}, "7.50 EUR"):
            self.answers = [(200, {"balance": value})]
            with self.assertRaises(api.SimyoApiError):
                await self.client.get_balance()
