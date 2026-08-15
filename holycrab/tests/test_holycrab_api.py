from __future__ import annotations

import argparse
import importlib.util
import io
import os
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "holycrab_api.py"
SPEC = importlib.util.spec_from_file_location("holycrab_api", SCRIPT_PATH)
assert SPEC and SPEC.loader
holycrab_api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(holycrab_api)


class BaseUrlSecurityTests(unittest.TestCase):
    def test_accepts_credential_free_https_origin(self) -> None:
        with patch.dict(os.environ, {"HOLYCRAB_BASE_URL": "https://staging.holycrab.ai:8443/"}):
            self.assertEqual(holycrab_api.base_url(), "https://staging.holycrab.ai:8443")

    def test_rejects_unsafe_base_urls(self) -> None:
        unsafe_urls = (
            "http://holycrab.example",
            "https://user:password@holycrab.example",
            "https://holycrab.example/api",
            "https://holycrab.example?token=secret",
            "https://holycrab.example#fragment",
        )
        for value in unsafe_urls:
            with self.subTest(value=value), patch.dict(os.environ, {"HOLYCRAB_BASE_URL": value}):
                with self.assertRaisesRegex(SystemExit, "HTTPS origin"):
                    holycrab_api.base_url()

    def test_allows_same_origin_redirect(self) -> None:
        handler = holycrab_api.SameOriginRedirectHandler("https://api.holycrab.ai")
        request = urllib.request.Request(
            "https://api.holycrab.ai/api/user/me",
            headers={"X-User-Token": "secret"},
        )
        redirected = handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://api.holycrab.ai/api/v2/user/me",
        )
        self.assertEqual(redirected.get_header("X-user-token"), "secret")

    def test_rejects_cross_origin_redirect_before_forwarding_token(self) -> None:
        handler = holycrab_api.SameOriginRedirectHandler("https://api.holycrab.ai")
        request = urllib.request.Request(
            "https://api.holycrab.ai/api/user/me",
            headers={"X-User-Token": "secret"},
        )
        with self.assertRaisesRegex(urllib.error.HTTPError, "Cross-origin redirect blocked") as caught:
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://attacker.example/collect",
            )
        caught.exception.close()

    def test_rejects_redirect_with_embedded_credentials(self) -> None:
        handler = holycrab_api.SameOriginRedirectHandler("https://api.holycrab.ai")
        request = urllib.request.Request(
            "https://api.holycrab.ai/api/user/me",
            headers={"X-User-Token": "secret"},
        )
        with self.assertRaisesRegex(urllib.error.HTTPError, "redirect blocked") as caught:
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://user:password@api.holycrab.ai/collect",
            )
        caught.exception.close()


class PresignedUrlSecurityTests(unittest.TestCase):
    def test_accepts_credential_free_https_upload_url(self) -> None:
        url = "https://storage.example/object?X-Amz-Signature=secret"
        self.assertEqual(holycrab_api.validate_presigned_upload_url(url), url)

    def test_rejects_unsafe_upload_urls(self) -> None:
        for value in (
            "http://storage.example/object?signature=secret",
            "https://user:password@storage.example/object",
            "file:///tmp/object",
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(SystemExit, "HTTPS URL"):
                    holycrab_api.validate_presigned_upload_url(value)


class OutputRedactionTests(unittest.TestCase):
    def test_recursively_redacts_presigned_urls(self) -> None:
        value = {
            "code": 0,
            "data": {
                "preSignedUrl": "https://storage.example/object?signature=secret",
                "nested": [{"presigned_url": "https://storage.example/second?token=secret"}],
                "uniqId": "asset-1",
            },
        }
        sanitized = holycrab_api.sanitize_for_output(value)
        self.assertEqual(sanitized["data"]["preSignedUrl"], "<redacted-presigned-url>")
        self.assertEqual(sanitized["data"]["nested"][0]["presigned_url"], "<redacted-presigned-url>")
        self.assertEqual(sanitized["data"]["uniqId"], "asset-1")

    def test_print_response_never_writes_presigned_query_secret(self) -> None:
        output = io.StringIO()
        response = {
            "code": 0,
            "data": {"preSignedUrl": "https://storage.example/object?signature=secret"},
        }
        with redirect_stdout(output):
            exit_code = holycrab_api.print_response(200, response)
        self.assertEqual(exit_code, 0)
        self.assertNotIn("signature=secret", output.getvalue())
        self.assertIn("<redacted-presigned-url>", output.getvalue())

    def test_print_response_fails_for_http_error(self) -> None:
        with redirect_stdout(io.StringIO()):
            self.assertEqual(holycrab_api.print_response(500, {"code": 0}), 1)

    def test_print_response_fails_for_business_error(self) -> None:
        response = {"code": 40001, "message": "invalid request"}
        with redirect_stdout(io.StringIO()):
            self.assertEqual(holycrab_api.print_response(200, response), 1)


class PollingBehaviorTests(unittest.TestCase):
    def test_poll_returns_success_for_completed_task(self) -> None:
        args = argparse.Namespace(uniq_id="task-1", timeout=10.0, interval=0.0)
        with patch.object(holycrab_api, "send", return_value=(200, {"code": 0, "data": {"step": 2}})):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(holycrab_api.command_poll_task(args), 0)

    def test_poll_returns_failure_for_failed_task(self) -> None:
        args = argparse.Namespace(uniq_id="task-1", timeout=10.0, interval=0.0)
        with patch.object(holycrab_api, "send", return_value=(200, {"code": 0, "data": {"step": 3}})):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(holycrab_api.command_poll_task(args), 1)

    def test_poll_returns_timeout_without_resubmitting(self) -> None:
        args = argparse.Namespace(uniq_id="task-1", timeout=0.0, interval=0.0)
        with patch.object(holycrab_api, "send", return_value=(200, {"code": 0, "data": {"step": 1}})) as send:
            with redirect_stdout(io.StringIO()), patch("sys.stderr", new=io.StringIO()):
                self.assertEqual(holycrab_api.command_poll_task(args), 2)
        send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
