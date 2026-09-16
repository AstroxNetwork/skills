from __future__ import annotations

import importlib.util
import io
import json
import os
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).parents[1] / "holycrab" / "scripts" / "holycrab_cli.py"
SPEC = importlib.util.spec_from_file_location("transport_cli", SCRIPT_PATH)
assert SPEC and SPEC.loader
transport_cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transport_cli)


class BaseUrlSecurityTests(unittest.TestCase):
    def test_uses_the_fixed_production_origin(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(transport_cli.base_url(), transport_cli.DEFAULT_BASE_URL)

    def test_allows_same_origin_redirect(self) -> None:
        handler = transport_cli.SameOriginRedirectHandler("https://api.holycrab.ai")
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
        handler = transport_cli.SameOriginRedirectHandler("https://api.holycrab.ai")
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
        handler = transport_cli.SameOriginRedirectHandler("https://api.holycrab.ai")
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
        self.assertEqual(transport_cli.validate_presigned_upload_url(url), url)

    def test_rejects_unsafe_upload_urls(self) -> None:
        for value in (
            "http://storage.example/object?signature=secret",
            "https://user:password@storage.example/object",
            "file:///tmp/object",
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(SystemExit, "HTTPS URL"):
                    transport_cli.validate_presigned_upload_url(value)


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
        sanitized = transport_cli.sanitize_for_output(value)
        self.assertEqual(sanitized["data"]["preSignedUrl"], "<redacted>")
        self.assertEqual(sanitized["data"]["nested"][0]["presigned_url"], "<redacted>")
        self.assertEqual(sanitized["data"]["uniqId"], "asset-1")

    def test_print_response_never_writes_presigned_query_secret(self) -> None:
        output = io.StringIO()
        response = {
            "code": 0,
            "data": {"preSignedUrl": "https://storage.example/object?signature=secret"},
        }
        with redirect_stdout(output):
            exit_code = transport_cli.print_response(200, response)
        self.assertEqual(exit_code, 0)
        self.assertNotIn("signature=secret", output.getvalue())
        self.assertIn("<redacted>", output.getvalue())

    def test_print_response_fails_for_http_error(self) -> None:
        with redirect_stdout(io.StringIO()):
            self.assertEqual(transport_cli.print_response(500, {"code": 0}), 1)

    def test_print_response_fails_for_business_error(self) -> None:
        response = {"code": 40001, "message": "invalid request"}
        with redirect_stdout(io.StringIO()):
            self.assertEqual(transport_cli.print_response(200, response), 1)


if __name__ == "__main__":
    unittest.main()
