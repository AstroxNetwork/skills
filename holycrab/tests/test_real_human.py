from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import stat
import tempfile
import time
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock, patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "holycrab_cli.py"
SPEC = importlib.util.spec_from_file_location("holycrab_real_human_test", SCRIPT)
assert SPEC and SPEC.loader
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)
AUTH_ID = "a" * 32
GROUP_ID = "b" * 32
ASSET_ID = "c" * 32
LINK = "https://www.byteplus.com/en/liveness-face-manage/authorization?pl=private-session&token=private-link"


def ok(data):
    return 200, {"code": 200, "data": data}


def session():
    return ok({"authorizationId": AUTH_ID, "h5Link": LINK,
               "expiresAt": "2026-08-31T20:30:00", "bytedToken": "private-byted",
               "arkAccountId": 77})


class RealHumanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"HOLYCRAB_CONFIG_DIR": self.temp.name,
                                          "HOLYCRAB_API_KEY": "test-api-secret"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.no_network = patch.object(cli, "send", side_effect=AssertionError("Unexpected API request"))
        self.send = self.no_network.start()
        self.addCleanup(self.no_network.stop)

    def call(self, tool_name, **arguments):
        return cli.mcp_dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": tool_name, "arguments": arguments}})["result"]

    def start(self):
        self.send.side_effect = None
        self.send.return_value = session()
        result = self.call("real_human_authorization_start", name="小林")
        self.assertFalse(result["isError"], result)
        return result

    def test_start_returns_working_link_and_png_but_no_private_fields(self):
        result = self.start()
        data = result["structuredContent"]
        self.assertEqual(data["authorizationId"], AUTH_ID)
        self.assertEqual(data["h5Link"], LINK)
        self.assertEqual(set(data), {"authorizationId", "h5Link", "expiresAt", "qrPath"})
        self.send.assert_called_once_with("POST", "/api/real-human-authorizations/sessions",
                                         payload={"name": "小林", "callbackUrl":
                                                  "https://generate.holycrab.ai/real-human-authorization/callback"})
        qr = Path(data["qrPath"])
        self.assertTrue(qr.is_absolute())
        self.assertTrue(qr.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(qr.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(qr.parent.stat().st_mode), 0o700)
        picture = next(item for item in result["content"] if item["type"] == "image")
        self.assertEqual(base64.b64decode(picture["data"]), qr.read_bytes())
        self.assertNotIn("private-byted", json.dumps(result))
        for path in Path(self.temp.name).rglob("*.json"):
            self.assertNotIn("private-byted", path.read_text())
            self.assertNotIn(LINK, path.read_text())

    def test_cli_start_preserves_the_authorization_link(self):
        self.send.side_effect = None
        self.send.return_value = session()
        parser = cli.build_parser()
        with redirect_stderr(io.StringIO()):
            args = parser.parse_args(["real-human", "start", "--name", "小林"])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(args.func(args), 0)
        self.assertEqual(json.loads(output.getvalue())["h5Link"], LINK)

    def test_invalid_verification_urls_never_escape_or_trigger_another_session(self):
        self.send.side_effect = None
        for link in ("http://www.byteplus.com/en/liveness-face-manage/authorization?pl=x",
                     "https://user:secret@www.byteplus.com/en/liveness-face-manage/authorization?pl=x",
                     "https://evil.example/en/liveness-face-manage/authorization?pl=x",
                     "https://www.byteplus.com.evil.example/en/liveness-face-manage/authorization?pl=x",
                     "https://www.byteplus.com:444/en/liveness-face-manage/authorization?pl=x",
                     "https://www.byteplus.com/en/another-path?pl=x",
                     "https://www.byteplus.com/en/liveness-face-manage/authorization?pl=x#fragment",
                     "https://www.byteplus.com/en/liveness-face-manage/authorization\nunsafe",
                     "file:///tmp/private"):
            self.send.reset_mock()
            response = session()
            response[1]["data"]["h5Link"] = link
            self.send.return_value = response
            result = self.call("real_human_authorization_start", name="小林")
            self.assertTrue(result["isError"])
            self.assertNotIn(link, json.dumps(result))
            self.assertIn(AUTH_ID, result["content"][0]["text"])
            self.send.assert_called_once()

    def test_qr_failure_preserves_created_session_without_retry(self):
        self.send.side_effect = None
        self.send.return_value = session()
        with patch.object(cli, "authorization_qr", side_effect=OSError("disk failed"), create=True):
            result = self.call("real_human_authorization_start", name="小林")
        self.assertFalse(result["isError"], result)
        self.assertEqual(result["structuredContent"]["h5Link"], LINK)
        self.assertIn("warning", result["structuredContent"])
        self.send.assert_called_once()

    def test_invalid_name_and_unknown_arguments_do_not_create_a_session(self):
        for arguments in ({"name": " "}, {"name": "x" * 256},
                          {"name": "小林", "callbackUrl": "https://evil.example"}):
            with self.subTest(arguments=arguments):
                self.assertTrue(self.call("real_human_authorization_start", **arguments)["isError"])
        self.send.assert_not_called()

    def test_start_network_uncertainty_is_not_retried(self):
        self.send.side_effect = urllib.error.URLError("connection lost")
        result = self.call("real_human_authorization_start", name="小林")
        self.assertTrue(result["isError"])
        self.assertIn("not retry", result["content"][0]["text"])
        self.send.assert_called_once()

    def test_start_gateway_or_malformed_reply_is_uncertain(self):
        for response in ((502, {"message": "gateway error"}), (504, {}), (200, "not JSON")):
            self.send.reset_mock()
            self.send.side_effect = None
            self.send.return_value = response
            result = self.call("real_human_authorization_start", name="小林")
            self.assertTrue(result["isError"])
            self.assertIn("not retry", result["content"][0]["text"])
            self.send.assert_called_once()

    def test_cache_failure_does_not_block_remote_authorization_query(self):
        self.send.side_effect = None
        self.send.return_value = ok({"authorizationId": AUTH_ID, "status": "CREATED"})
        with patch.object(cli, "authorization_cache_dir", side_effect=PermissionError("denied")):
            result = self.call("real_human_authorization_get", authorizationId=AUTH_ID)
        self.assertFalse(result["isError"], result)
        self.assertIn("warning", result["structuredContent"])
        self.send.assert_called_once()

    def test_terminal_status_removes_qr_and_omits_internal_group(self):
        data = self.start()["structuredContent"]
        self.send.return_value = ok({"authorizationId": AUTH_ID, "status": "SUCCEEDED",
                                     "groupUniqId": GROUP_ID, "arkGroupId": "private-group",
                                     "group": {"uniqId": GROUP_ID, "name": "小林", "arkAccountId": 77}})
        result = self.call("real_human_authorization_get", authorizationId=AUTH_ID)
        self.assertFalse(result["isError"], result)
        self.assertEqual(result["structuredContent"]["group"], {"uniqId": GROUP_ID, "name": "小林"})
        self.assertNotIn("private-group", json.dumps(result))
        self.assertFalse(Path(data["qrPath"]).exists())

    def test_next_authorization_command_cleans_expired_qr(self):
        with patch.object(cli.time, "time", return_value=time.time() - 3600):
            data = self.start()["structuredContent"]
        self.send.return_value = ok({"records": [], "total": 0})
        result = self.call("real_human_groups_list")
        self.assertFalse(result["isError"], result)
        self.assertFalse(Path(data["qrPath"]).exists())

    def test_ordinary_mcp_call_also_cleans_expired_qr(self):
        with patch.object(cli.time, "time", return_value=time.time() - 3600):
            data = self.start()["structuredContent"]
        result = self.call("capabilities_list")
        self.assertFalse(result["isError"], result)
        self.assertFalse(Path(data["qrPath"]).exists())

    def test_cleanup_does_not_follow_a_symlink_outside_cache(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        marker = outside / "qr.png"
        marker.write_bytes(b"keep")
        root = Path(self.temp.name) / "real-human"
        root.mkdir()
        (root / AUTH_ID).symlink_to(outside, target_is_directory=True)
        self.send.side_effect = None
        self.send.return_value = ok({"authorizationId": AUTH_ID, "status": "FAILED"})
        self.assertFalse(self.call("real_human_authorization_get", authorizationId=AUTH_ID)["isError"])
        self.assertEqual(marker.read_bytes(), b"keep")

    def test_failed_and_expired_authorizations_stop_waiting_and_clean_qr(self):
        for status in ("FAILED", "EXPIRED"):
            data = self.start()["structuredContent"]
            self.send.reset_mock()
            self.send.return_value = ok({"authorizationId": AUTH_ID, "status": status})
            args = cli.build_parser().parse_args(["real-human", "wait", AUTH_ID])
            with redirect_stdout(io.StringIO()):
                self.assertEqual(args.func(args), 1)
            self.assertFalse(Path(data["qrPath"]).exists())
            self.send.assert_called_once()

    def test_status_rejects_bad_id_before_api(self):
        result = self.call("real_human_authorization_get", authorizationId="../another")
        self.assertTrue(result["isError"])
        self.send.assert_not_called()

    def test_group_pagination_and_public_projection(self):
        self.send.side_effect = None
        self.send.return_value = ok({"records": [{"uniqId": GROUP_ID, "name": "小林", "assetCount": 2,
                                                  "arkGroupId": "private-group", "userId": 8}],
                                     "total": 21, "current": 2, "pages": 2, "size": 20})
        result = self.call("real_human_groups_list", page=2, pageSize=20)
        self.assertFalse(result["isError"], result)
        self.send.assert_called_once_with("GET", "/api/real-human-groups",
                                         query=[("page", "2"), ("pageSize", "20")])
        self.assertEqual(result["structuredContent"]["total"], 21)
        self.assertNotIn("private-group", json.dumps(result))
        self.assertNotIn("userId", json.dumps(result))

    def test_invalid_pagination_rejected_before_api(self):
        for page, size in ((0, 20), (1, 101), (True, 20), (1, "20")):
            self.assertTrue(self.call("real_human_groups_list", page=page, pageSize=size)["isError"])
        self.send.assert_not_called()

    def test_api_error_redacts_new_sensitive_fields_and_links(self):
        self.send.side_effect = None
        self.send.return_value = (400, {"message": "failed bytedToken=private-byted " + LINK,
                                       "bytedToken": "private-byted"})
        result = self.call("real_human_authorization_get", authorizationId=AUTH_ID)
        self.assertTrue(result["isError"])
        text = json.dumps(result)
        self.assertNotIn("private-byted", text)
        self.assertNotIn("private-session", text)

    def test_untrusted_nested_values_cannot_bypass_projection(self):
        self.send.side_effect = None
        self.send.return_value = ok({"records": [{"uniqId": GROUP_ID,
                                                  "name": {"secret": "private-hidden"}}], "total": 1})
        result = self.call("real_human_groups_list")
        self.assertNotIn("private-hidden", json.dumps(result))

    def test_wait_timeout_keeps_id_and_never_creates_a_new_session(self):
        self.send.side_effect = None
        self.send.return_value = ok({"authorizationId": AUTH_ID, "status": "CREATED"})
        with redirect_stderr(io.StringIO()):
            args = cli.build_parser().parse_args(["real-human", "wait", AUTH_ID, "--timeout", "0"])
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            self.assertEqual(args.func(args), 2)
        self.assertIn(AUTH_ID, output.getvalue())
        self.send.assert_called_once_with("GET", "/api/real-human-authorizations/" + AUTH_ID)

    def test_invalid_wait_intervals_fail_before_any_query(self):
        for interval, timeout in ((0, 1), (-1, 1), (float("nan"), 1), (1, float("inf")), (1, -1)):
            with self.assertRaises(ValueError):
                cli.poll_resource(AUTH_ID, timeout, interval, authorization=True)
        self.send.assert_not_called()


class RealHumanManagementTests(unittest.TestCase):
    setUp = RealHumanTests.setUp
    call = RealHumanTests.call

    @staticmethod
    def group(name="小林", asset_count=8):
        return {"uniqId": GROUP_ID, "name": name, "assetCount": asset_count,
                "arkGroupId": "internal-group", "userId": 7}

    def test_group_rename_uses_patch_and_returns_only_public_fields(self):
        self.send.side_effect = None
        self.send.return_value = ok({**self.group(name="新名字"), "secret": "hidden"})

        result = self.call("real_human_group_rename", groupUniqId=GROUP_ID, name=" 新名字 ")

        self.assertFalse(result["isError"], result)
        self.assertEqual(result["structuredContent"], {"uniqId": GROUP_ID, "name": "新名字"})
        self.assertNotIn("internal-group", json.dumps(result))
        self.assertNotIn("hidden", json.dumps(result))
        self.send.assert_called_once_with("PATCH", "/api/real-human-groups/" + GROUP_ID,
                                         payload={"name": "新名字"})

    def test_group_rename_rejects_invalid_name_before_api(self):
        for name in (" ", "x" * 256):
            with self.subTest(name=name):
                self.assertTrue(self.call("real_human_group_rename", groupUniqId=GROUP_ID,
                                          name=name)["isError"])
        self.send.assert_not_called()

    def test_group_rename_reports_not_found_and_forbidden_without_retry(self):
        for status in (403, 404):
            with self.subTest(status=status):
                self.send.reset_mock()
                self.send.side_effect = None
                self.send.return_value = (status, {"message": "Not available", "internalId": 42})
                result = self.call("real_human_group_rename", groupUniqId=GROUP_ID, name="新名字")
                self.assertTrue(result["isError"])
                self.assertEqual(self.send.call_count, 1)
                self.assertNotIn("internalId", json.dumps(result))

    def test_group_rename_uncertainty_queries_state_without_retry(self):
        self.send.side_effect = [(502, {"message": "gateway error"}),
                                 ok({"records": [self.group(name="新名字")], "total": 1,
                                     "current": 1, "pages": 1, "size": 100})]
        result = self.call("real_human_group_rename", groupUniqId=GROUP_ID, name="新名字")
        self.assertTrue(result["isError"])
        self.assertIn("still contains", result["content"][0]["text"])
        self.assertEqual(sum(call.args[0] == "PATCH" for call in self.send.call_args_list), 1)

    def test_group_delete_requires_true_confirmation_before_any_api_call(self):
        for arguments in ({"groupUniqId": GROUP_ID},
                          {"groupUniqId": GROUP_ID, "confirmed": False}):
            with self.subTest(arguments=arguments):
                self.assertTrue(self.call("real_human_group_delete", **arguments)["isError"])
        self.send.assert_not_called()

    def test_group_delete_queries_target_once_then_deletes_once(self):
        self.send.side_effect = [ok({"records": [self.group()], "total": 1,
                                     "current": 1, "pages": 1, "size": 100}),
                                 ok(None)]

        result = self.call("real_human_group_delete", groupUniqId=GROUP_ID, confirmed=True)

        self.assertFalse(result["isError"], result)
        self.assertEqual(result["structuredContent"], {"deleted": True, "groupUniqId": GROUP_ID})
        self.assertEqual(self.send.call_count, 2)
        self.send.assert_any_call("DELETE", "/api/real-human-groups/" + GROUP_ID)

    def test_cli_group_delete_decline_never_sends_delete(self):
        self.send.side_effect = None
        self.send.return_value = ok({"records": [self.group()], "total": 1,
                                     "current": 1, "pages": 1, "size": 100})
        args = cli.build_parser().parse_args(["real-human", "groups", "delete", GROUP_ID])
        stdout = io.StringIO()
        with patch.object(cli.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="no"), \
                redirect_stdout(stdout):
            self.assertEqual(args.func(args), 2)
        self.assertIn("8", stdout.getvalue())
        self.assertFalse(any(call.args[0] == "DELETE" for call in self.send.call_args_list))

    def test_cli_group_delete_yes_queries_and_deletes_once(self):
        self.send.side_effect = [ok({"records": [self.group()], "total": 1,
                                     "current": 1, "pages": 1, "size": 100}), ok(None)]
        args = cli.build_parser().parse_args(["real-human", "groups", "delete", GROUP_ID, "--yes"])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(args.func(args), 0)
        self.assertEqual(self.send.call_count, 2)
        self.send.assert_any_call("DELETE", "/api/real-human-groups/" + GROUP_ID)

    def test_cli_asset_delete_decline_never_sends_delete(self):
        self.send.side_effect = [ok({"records": [self.group()], "total": 1,
                                     "current": 1, "pages": 1, "size": 100}),
                                 ok({"uniqId": ASSET_ID, "name": "正面照", "step": "UPLOADED_TO_ARK"})]
        args = cli.build_parser().parse_args(["real-human", "assets", "delete", ASSET_ID,
                                              "--group", GROUP_ID])
        with patch.object(cli.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="no"), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(args.func(args), 2)
        self.assertFalse(any(call.args[0] == "DELETE" for call in self.send.call_args_list))

    def test_asset_delete_requires_confirmation_and_returns_public_ids(self):
        self.send.side_effect = [ok({"records": [self.group()], "total": 1,
                                     "current": 1, "pages": 1, "size": 100}),
                                 ok({"uniqId": ASSET_ID, "name": "正面照", "step": "UPLOADED_TO_ARK",
                                     "arkAssetId": "internal-asset"}),
                                 ok(None)]
        rejected = self.call("real_human_asset_delete", groupUniqId=GROUP_ID,
                             assetId=ASSET_ID, confirmed=False)
        self.assertTrue(rejected["isError"])
        self.send.assert_not_called()

        result = self.call("real_human_asset_delete", groupUniqId=GROUP_ID,
                           assetId=ASSET_ID, confirmed=True)
        self.assertFalse(result["isError"], result)
        self.assertEqual(result["structuredContent"], {"deleted": True, "groupUniqId": GROUP_ID,
                                                        "assetUniqId": ASSET_ID})
        self.assertNotIn("internal-asset", json.dumps(result))
        self.send.assert_any_call("DELETE", "/api/real-human-groups/" + GROUP_ID + "/assets/" + ASSET_ID)

    def test_delete_uncertainty_is_not_retried(self):
        for outcome in ((502, {"message": "gateway error"}),
                        urllib.error.URLError("connection lost"), (200, "unexpected HTML")):
            self.send.reset_mock()
            self.send.side_effect = [ok({"records": [self.group()], "total": 1,
                                         "current": 1, "pages": 1, "size": 100}), outcome,
                                     ok({"records": [self.group()], "total": 1,
                                         "current": 1, "pages": 1, "size": 100})]
            result = self.call("real_human_group_delete", groupUniqId=GROUP_ID, confirmed=True)
            self.assertTrue(result["isError"])
            self.assertIn("not retry", result["content"][0]["text"])
            self.assertIn("still contains", result["content"][0]["text"])
            self.assertEqual(self.send.call_count, 3)
            self.assertEqual(sum(call.args[0] == "DELETE" for call in self.send.call_args_list), 1)

    def test_asset_delete_uncertainty_queries_once_and_never_retries(self):
        self.send.side_effect = [ok({"records": [self.group()], "total": 1,
                                     "current": 1, "pages": 1, "size": 100}),
                                 ok({"uniqId": ASSET_ID, "name": "正面照"}),
                                 (502, {"message": "gateway error"}),
                                 ok({"uniqId": ASSET_ID, "name": "正面照"})]
        result = self.call("real_human_asset_delete", groupUniqId=GROUP_ID,
                           assetId=ASSET_ID, confirmed=True)
        self.assertTrue(result["isError"])
        self.assertIn("still contains", result["content"][0]["text"])
        self.assertEqual(sum(call.args[0] == "DELETE" for call in self.send.call_args_list), 1)

    def test_missing_group_stops_before_mutation(self):
        self.send.side_effect = None
        self.send.return_value = ok({"records": [], "total": 0, "current": 1,
                                     "pages": 0, "size": 100})
        result = self.call("real_human_group_delete", groupUniqId=GROUP_ID, confirmed=True)
        self.assertTrue(result["isError"])
        self.assertFalse(any(call.args[0] == "DELETE" for call in self.send.call_args_list))


class AssetWorkflowTests(unittest.TestCase):
    setUp = RealHumanTests.setUp
    call = RealHumanTests.call

    def test_real_human_upload_uses_selected_group_and_never_returns_registration_payload(self):
        path = Path(self.temp.name) / "reference.jpg"
        path.write_bytes(b"image")
        self.send.side_effect = [ok({"records": [], "total": 0}),
                                ok({"preSignedUrl": "https://storage.example/x?sig=private",
                                    "objectKey": "7/" + ASSET_ID + ".jpg", "uniqId": ASSET_ID}),
                                ok({"arkAccountId": 77})]
        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch.object(cli, "open_presigned_upload", return_value=response) as upload:
            result = self.call("asset_upload", file=str(path), groupUniqId=GROUP_ID)
        self.assertFalse(result["isError"], result)
        self.assertEqual(result["structuredContent"]["assetUniqId"], ASSET_ID)
        self.assertEqual(result["structuredContent"]["groupUniqId"], GROUP_ID)
        self.assertFalse(result["structuredContent"]["ready"])
        self.assertNotIn("arkAccountId", json.dumps(result))
        self.send.assert_any_call("POST", "/api/real-human-groups/" + GROUP_ID + "/assets/upload",
                                  form={"name": "reference.jpg", "object_key": "7/" + ASSET_ID + ".jpg",
                                        "content_type": "image/jpeg"})
        self.assertFalse(any("token" in key.lower() for key in upload.call_args.args[0].headers))

    def test_group_access_failure_happens_before_upload(self):
        path = Path(self.temp.name) / "reference.jpg"
        path.write_bytes(b"image")
        self.send.side_effect = [ (404, {"message": "Group not found"}) ]
        with patch.object(cli, "open_presigned_upload") as upload:
            self.assertTrue(self.call("asset_upload", file=str(path), groupUniqId=GROUP_ID)["isError"])
        upload.assert_not_called()
        self.send.assert_called_once()

    def test_upload_redirect_handler_refuses_every_redirect(self):
        handler_type = getattr(cli, "RejectRedirectHandler", None)
        self.assertIsNotNone(handler_type, "uploads need an explicit no-redirect handler")
        request = urllib.request.Request("https://storage.example/object", method="PUT")
        with self.assertRaisesRegex(urllib.error.HTTPError, "redirect blocked") as caught:
            handler_type().redirect_request(request, None, 307, "Temporary Redirect", {},
                                            "https://other.example/object")
        caught.exception.close()

    def test_registration_uncertainty_keeps_asset_id_and_never_retries(self):
        path = Path(self.temp.name) / "reference.jpg"
        path.write_bytes(b"image")
        for outcome in ((502, {"message": "gateway error"}), urllib.error.URLError("connection lost"),
                        (200, "unexpected HTML")):
            self.send.reset_mock()
            self.send.side_effect = [ok({"records": [], "total": 0}),
                                    ok({"preSignedUrl": "https://storage.example/x?sig=private",
                                        "objectKey": "7/" + ASSET_ID + ".jpg", "uniqId": ASSET_ID}), outcome]
            response = MagicMock()
            response.__enter__.return_value.status = 200
            with patch.object(cli, "open_presigned_upload", return_value=response) as upload:
                result = self.call("asset_upload", file=str(path), groupUniqId=GROUP_ID)
            self.assertTrue(result["isError"])
            text = result["content"][0]["text"]
            self.assertIn(ASSET_ID, text)
            self.assertIn(GROUP_ID, text)
            self.assertIn("not retry", text)
            self.assertEqual(self.send.call_count, 3)
            upload.assert_called_once()

    def test_group_asset_list_omits_upstream_asset_identifier(self):
        self.send.side_effect = None
        self.send.return_value = ok({"records": [{"uniqId": ASSET_ID, "name": "reference",
                                                  "step": "UPLOADED", "status": "Processing",
                                                  "arkAssetId": "private-ark-asset"}], "total": 1})
        result = self.call("real_human_assets_list", groupUniqId=GROUP_ID)
        self.assertFalse(result["isError"], result)
        self.assertFalse(result["structuredContent"]["records"][0]["ready"])
        self.assertNotIn("private-ark-asset", json.dumps(result))

    def test_asset_ready_only_when_processing_complete(self):
        self.send.side_effect = None
        for step in ("UPLOADED", "FAILED", "UPLOADED_TO_ARK"):
            self.send.return_value = ok({"uniqId": ASSET_ID, "step": step, "status": "Success",
                                         "error": "failure" if step == "FAILED" else None})
            result = self.call("asset_get", assetId=ASSET_ID)
            self.assertFalse(result["isError"], result)
            self.assertEqual(result["structuredContent"]["ready"], step == "UPLOADED_TO_ARK")

    def test_asset_wait_failure_stops_with_error(self):
        self.send.side_effect = None
        self.send.return_value = ok({"uniqId": ASSET_ID, "step": "FAILED", "error": "face mismatch"})
        with redirect_stderr(io.StringIO()):
            args = cli.build_parser().parse_args(["assets", "wait", ASSET_ID])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(args.func(args), 1)
        self.assertIn("face mismatch", output.getvalue())
        self.send.assert_called_once()

    def test_offline_authorization_to_generation_preserves_confirmation(self):
        # This exercises the real MCP handlers with an in-memory API fixture.
        # It does not emulate human verification or claim a live end-to-end test.
        state = {"verified": False, "registered": False, "ready": False, "created": 0}
        task_id = "d" * 32

        def api(method, path, **kwargs):
            if path == "/api/real-human-authorizations/sessions":
                return session()
            if path == "/api/real-human-authorizations/" + AUTH_ID:
                if state["verified"]:
                    return ok({"authorizationId": AUTH_ID, "status": "SUCCEEDED",
                               "group": {"uniqId": GROUP_ID, "name": "小林"}})
                return ok({"authorizationId": AUTH_ID, "status": "CREATED"})
            if path == "/api/real-human-groups/" + GROUP_ID + "/assets":
                return ok({"records": [], "total": 0})
            if path == "/api/user-assets/pre-signed-download-url":
                return ok({"preSignedUrl": "https://storage.example/x?sig=offline-upload",
                           "objectKey": "7/" + ASSET_ID + ".jpg", "uniqId": ASSET_ID})
            if path == "/api/real-human-groups/" + GROUP_ID + "/assets/upload":
                state["registered"] = True
                return ok(None)
            if path == "/api/user-assets/" + ASSET_ID:
                return ok({"uniqId": ASSET_ID, "step": "UPLOADED_TO_ARK" if state["ready"] else "UPLOADED"})
            if path == "/api/tasks/generation/freeze-credit":
                if not state["ready"]:
                    return 400, {"message": "Asset is not ready"}
                self.assertEqual(kwargs["payload"]["imageAssetIds"], [ASSET_ID])
                return ok({"frozenCredit": 14})
            if path == "/api/tasks/generation":
                self.assertTrue(state["ready"])
                state["created"] += 1
                return ok({"uniqId": task_id, "step": 0})
            if path == "/api/tasks/" + task_id:
                return ok({"uniqId": task_id, "step": 2, "videoUrl": "https://cdn.example/offline.mp4"})
            self.fail(f"Unexpected fixture route: {method} {path}")

        self.send.side_effect = api
        started = self.call("real_human_authorization_start", name="小林")
        self.assertFalse(started["isError"])
        pending = self.call("real_human_authorization_get", authorizationId=AUTH_ID)
        self.assertEqual(pending["structuredContent"]["status"], "CREATED")
        state["verified"] = True  # Fixture state change, never an Agent callback submission.
        authorized = self.call("real_human_authorization_get", authorizationId=AUTH_ID)
        self.assertEqual(authorized["structuredContent"]["group"]["uniqId"], GROUP_ID)
        path = Path(self.temp.name) / "reference.jpg"
        path.write_bytes(b"offline-fixture")
        upload_response = MagicMock()
        upload_response.__enter__.return_value.status = 200
        with patch.object(cli, "open_presigned_upload", return_value=upload_response):
            uploaded = self.call("asset_upload", file=str(path), groupUniqId=GROUP_ID)
        self.assertFalse(uploaded["isError"])
        self.assertTrue(state["registered"])
        self.assertFalse(self.call("asset_get", assetId=ASSET_ID)["structuredContent"]["ready"])
        request = {"model": "dreamina-seedance-2-5-260628", "prompt": "Person waves",
                   "duration": 8, "resolution": "720p", "imageAssetIds": [ASSET_ID]}
        rejected = self.call("generation_create", kind="video", request=request,
                             confirmed=True, attemptId="offline-draw")
        self.assertTrue(rejected["isError"])
        self.assertEqual(state["created"], 0)
        state["ready"] = True
        self.assertTrue(self.call("asset_get", assetId=ASSET_ID)["structuredContent"]["ready"])
        unconfirmed = self.call("generation_create", kind="video", request=request,
                                confirmed=False, attemptId="offline-draw")
        self.assertTrue(unconfirmed["structuredContent"]["confirmationRequired"])
        self.assertEqual(state["created"], 0)
        confirmed = self.call("generation_create", kind="video", request=request,
                              confirmed=True, attemptId="offline-draw")
        self.assertEqual(confirmed["structuredContent"]["taskId"], task_id)
        duplicate = self.call("generation_create", kind="video", request=request,
                              confirmed=True, attemptId="offline-draw")
        self.assertTrue(duplicate["isError"])
        self.assertEqual(state["created"], 1)
        result = self.call("generation_get", taskId=task_id)
        self.assertEqual(result["structuredContent"]["step"], 2)
        self.assertEqual(sum(call.args[1].endswith("/sessions") for call in self.send.call_args_list), 1)
        self.assertEqual(sum(call.args[1].endswith("/assets/upload") for call in self.send.call_args_list), 1)


if __name__ == "__main__":
    unittest.main()
