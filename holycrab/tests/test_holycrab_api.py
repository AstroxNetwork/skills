from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
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

    def test_redacts_signed_result_urls_without_relying_on_field_names(self) -> None:
        signed_url = "https://cdn.example/result.mp4?X-Tos-Credential=temporary&X-Tos-Signature=secret"
        stable_url = "https://cdn.example/public.mp4?version=2"
        sanitized = holycrab_api.sanitize_for_output(
            {"data": {"videoUrl": signed_url, "imageUrls": [signed_url], "stableUrl": stable_url}}
        )
        self.assertEqual(sanitized["data"]["videoUrl"], "<redacted-signed-url>")
        self.assertEqual(sanitized["data"]["imageUrls"], ["<redacted-signed-url>"])
        self.assertEqual(sanitized["data"]["stableUrl"], stable_url)

    def test_redacts_embedded_signed_urls_and_sensitive_field_aliases(self) -> None:
        value = {
            "apiKey": "example-api-secret",
            "raw": "failed: https://cdn.example/object?X-Tos-Signature=embedded-secret",
        }
        sanitized = holycrab_api.sanitize_for_output(value)
        self.assertNotIn("example-api-secret", json.dumps(sanitized))
        self.assertNotIn("embedded-secret", sanitized["raw"])

    def test_redacts_html_escaped_signed_url_query(self) -> None:
        value = "https://cdn.example/object?download=1&amp;X-Amz-Signature=html-secret"
        self.assertEqual(holycrab_api.sanitize_for_output(value), "<redacted-signed-url>")

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


class AssetUploadTests(unittest.TestCase):
    def test_legacy_multipart_encoder_preserves_utf8_values(self) -> None:
        encoder = getattr(holycrab_api, "encode_multipart_form", None)
        self.assertIsNotNone(encoder)
        body, content_type = encoder(
            {"name": "懵懵.jpeg", "object_key": "user/asset.jpeg"},
            boundary="HolyCrabBoundary",
        )
        self.assertEqual(content_type, "multipart/form-data; boundary=HolyCrabBoundary")
        self.assertIn("懵懵.jpeg".encode("utf-8"), body)
        self.assertTrue(body.endswith(b"--HolyCrabBoundary--\r\n"))

    def test_legacy_upload_registers_with_multipart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "懵懵.jpeg"
            image.write_bytes(b"jpeg-bytes")
            args = argparse.Namespace(
                file=str(image), content_type=None, duration_seconds=None, name=None
            )
            replies = [
                (
                    200,
                    {
                        "code": 200,
                        "data": {
                            "preSignedUrl": "https://storage.example/object?signature=secret",
                            "objectKey": "user/asset.jpeg",
                            "uniqId": "asset-1",
                        },
                    },
                ),
                (200, {"code": 200, "data": None}),
            ]
            upload_response = unittest.mock.MagicMock()
            upload_response.__enter__.return_value.status = 200
            with patch.object(holycrab_api, "send", side_effect=replies) as send, patch.object(
                holycrab_api.urllib.request, "urlopen", return_value=upload_response
            ), redirect_stdout(io.StringIO()):
                self.assertEqual(holycrab_api.command_upload_asset(args), 0)

        send.assert_any_call(
            "POST",
            "/api/user-assets/upload",
            form={
                "name": "懵懵.jpeg",
                "object_key": "user/asset.jpeg",
                "content_type": "image/jpeg",
            },
        )

    def test_legacy_registration_400_does_not_print_asset_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "yuna.jpeg"
            image.write_bytes(b"jpeg-bytes")
            args = argparse.Namespace(
                file=str(image), content_type=None, duration_seconds=None, name=None
            )
            replies = [
                (
                    200,
                    {
                        "code": 0,
                        "data": {
                            "preSignedUrl": "https://storage.example/object?signature=secret",
                            "objectKey": "user/asset.jpeg",
                            "uniqId": "asset-not-registered",
                        },
                    },
                ),
                (400, {"code": 400, "message": "missing multipart field"}),
            ]
            upload_response = unittest.mock.MagicMock()
            upload_response.__enter__.return_value.status = 200
            output = io.StringIO()
            with patch.object(holycrab_api, "send", side_effect=replies), patch.object(
                holycrab_api.urllib.request, "urlopen", return_value=upload_response
            ), redirect_stdout(output):
                self.assertEqual(holycrab_api.command_upload_asset(args), 1)

        self.assertNotIn("asset-not-registered", output.getvalue())
        self.assertIn("missing multipart field", output.getvalue())


class LegacySurfaceTests(unittest.TestCase):
    def test_public_legacy_parser_has_no_generic_request_command(self) -> None:
        parser = holycrab_api.build_parser()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["request", "POST", "/api/tasks/generation", "--json", "{}"])


if __name__ == "__main__":
    unittest.main()
