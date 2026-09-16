"""Post-install guidance and account truthfulness; all API calls are fixtures."""
from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

try:
    from .test_feedback import cli, TerminalBuffer, ok
except ImportError:
    from test_feedback import cli, TerminalBuffer, ok


class OnboardingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {"HOLYCRAB_CONFIG_DIR": self.temp.name,
                                      "HOLYCRAB_NO_UPDATE_CHECK": "1"}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def setup_key(self, *, no_verify: bool = False, terminal: bool = False) -> tuple[dict, str]:
        with patch.object(cli.getpass, "getpass", return_value="hc_test_onboarding_only"), \
                redirect_stdout(io.StringIO()) as output, \
                redirect_stderr(TerminalBuffer() if terminal else io.StringIO()) as errors:
            cli.command_set_key(argparse.Namespace(stdin=False, no_verify=no_verify))
        return json.loads(output.getvalue()), errors.getvalue()

    def test_verified_setup_preserves_account_fields_and_guides_business_use(self) -> None:
        with patch.object(cli, "send", return_value=ok({"username": "Fixture", "credit": 20})) as transport:
            result, errors = self.setup_key(terminal=True)
        self.assertEqual(result["credit"], 20)
        self.assertEqual(result["onboarding"]["state"], "READY")
        self.assertIn("connected", errors)
        self.assertEqual(transport.call_args.args[:2], ("GET", "/api/user/me"))
        self.assertNotIn("hc_test_onboarding_only", json.dumps(result) + errors)

    def test_no_verify_does_not_claim_connection_or_make_requests(self) -> None:
        with patch.object(cli, "send") as transport:
            result, errors = self.setup_key(no_verify=True, terminal=True)
        self.assertEqual(result["onboarding"]["state"], "VERIFY_ACCOUNT")
        self.assertFalse(result["valid"])
        self.assertNotIn("account connected", errors.lower())
        transport.assert_not_called()

    def test_environment_override_prevents_saved_key_becoming_active_ready(self) -> None:
        with patch.dict(os.environ, {"HOLYCRAB_API_KEY": "hc_test_other_environment"}), \
                patch.object(cli, "send", return_value=ok({"username": "Fixture"})):
            result, errors = self.setup_key(terminal=True)
        self.assertEqual(result["onboarding"]["state"], "VERIFY_ACCOUNT")
        self.assertNotIn("account connected", errors.lower())
        self.assertIn("overrides", errors)
        self.assertNotIn("hc_test_other_environment", json.dumps(result) + errors)

    def test_failed_setup_does_not_save_or_show_ready(self) -> None:
        for reply in ((401, {"code": 401}), ok({}), ok([]), ok({"privateOnly": True})):
            with self.subTest(reply=reply), patch.object(cli, "send", return_value=reply), \
                    patch.object(cli.getpass, "getpass", return_value="hc_test_bad_key"), \
                    redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()), \
                    self.assertRaises((SystemExit, ValueError)):
                cli.command_set_key(argparse.Namespace(stdin=False, no_verify=False))
            self.assertNotIn("READY", output.getvalue())
            self.assertFalse(cli.load_config().get("apiKey"))

    def test_stdin_setup_has_one_json_result_without_progress_noise(self) -> None:
        with patch.object(cli.sys, "stdin", io.StringIO("hc_test_stdin_only\n")), \
                redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors:
            cli.command_set_key(argparse.Namespace(stdin=True, no_verify=True))
        self.assertEqual(json.loads(output.getvalue())["onboarding"]["state"], "VERIFY_ACCOUNT")
        self.assertEqual(errors.getvalue(), "")

    def test_local_doctor_distinguishes_missing_and_unverified_credentials(self) -> None:
        with patch.object(cli, "send") as transport:
            self.assertEqual(cli.local_health_report()["onboarding"]["state"], "CONNECT_ACCOUNT")
            cli.save_config({"apiKey": "hc_test_saved_only"})
            self.assertEqual(cli.local_health_report()["onboarding"]["state"], "VERIFY_ACCOUNT")
        transport.assert_not_called()

    def test_online_doctor_requires_a_valid_account_payload(self) -> None:
        cli.save_config({"apiKey": "hc_test_saved_only"})
        for payload, ready in (({"username": "Fixture"}, True), ({}, False), ([], False), ({"privateOnly": True}, False)):
            with self.subTest(payload=payload), patch.object(cli, "send", return_value=ok(payload)), \
                    patch.object(cli, "check_for_update", return_value={"updateAvailable": False}):
                result = cli.local_health_report(online=True)
            self.assertEqual(result["checks"]["apiKey"]["ok"], ready)
            self.assertEqual(result["onboarding"]["state"], "READY" if ready else "VERIFY_ACCOUNT")

    def test_auth_status_reports_malformed_success_as_invalid(self) -> None:
        cli.save_config({"apiKey": "hc_test_saved_only"})
        for payload in ({}, [], {"privateOnly": True}, None):
            with self.subTest(payload=payload), patch.object(cli, "send", return_value=ok(payload)), \
                    redirect_stdout(io.StringIO()) as output:
                code = cli.command_auth_status(argparse.Namespace())
            result = json.loads(output.getvalue())
            self.assertEqual(code, 1)
            self.assertFalse(result["valid"])
            self.assertNotEqual(result["onboarding"]["state"], "READY")

    def test_read_only_status_does_not_repeat_full_welcome(self) -> None:
        cli.save_config({"apiKey": "hc_test_saved_only"})
        with patch.object(cli, "send", return_value=ok({"username": "Fixture"})), \
                redirect_stdout(io.StringIO()) as output, redirect_stderr(TerminalBuffer()) as errors:
            cli.command_auth_status(argparse.Namespace())
            cli.command_auth_status(argparse.Namespace())
        self.assertNotIn("What would you like", errors.getvalue())
        self.assertNotIn("Product images", errors.getvalue())
        self.assertNotIn("welcomeSeen", cli.load_config())

    def test_reconfiguring_a_saved_login_does_not_repeat_terminal_introduction(self) -> None:
        cli.save_config({"apiKey": "hc_test_saved_only"})
        with patch.object(cli, "send", return_value=ok({"username": "Fixture"})):
            result, errors = self.setup_key(terminal=True)
        self.assertEqual(result["onboarding"]["state"], "READY")
        self.assertNotIn("What would you like", errors)
        self.assertNotIn("Business uses:", errors)

    def test_matching_current_release_is_not_an_update(self) -> None:
        result = cli.release_update_info({"tag_name": "v" + cli.VERSION, "draft": False, "prerelease": False,
                                          "html_url": cli.RELEASE_PAGE_PREFIX + "v" + cli.VERSION})
        self.assertFalse(result["updateAvailable"])

    def test_business_examples_are_non_executing_and_not_estimate_prompts(self) -> None:
        with patch.object(cli, "send") as transport:
            result = cli.onboarding_guidance(configured=True, verified=True)
        self.assertEqual([item["code"] for item in result["examples"]],
                         ["PRODUCT_IMAGES", "SOCIAL_AD_VIDEO", "PRODUCT_VOICEOVER"])
        self.assertEqual(len(result["examples"]), 3)
        for example in result["examples"]:
            self.assertNotIn("estimate", example["prompt"].lower())
            self.assertNotIn("--yes", example["prompt"])
        self.assertIsNone(result["command"])
        transport.assert_not_called()

    def test_mcp_account_preserves_public_fields_and_returns_guidance(self) -> None:
        with patch.object(cli, "send", return_value=ok({"username": "Fixture", "credit": 20})):
            result = cli.mcp_tool_call("account_get", {})
        self.assertEqual(result["credit"], 20)
        self.assertEqual(result["onboarding"]["state"], "READY")

    def test_mcp_rejects_malformed_account_without_protocol_noise(self) -> None:
        with patch.object(cli, "send", return_value=ok({})), redirect_stdout(io.StringIO()) as output:
            with self.assertRaises((SystemExit, ValueError)):
                cli.mcp_tool_call("account_get", {})
        self.assertEqual(output.getvalue(), "")

    def test_mcp_initialize_contains_conditional_agent_onboarding_not_a_popup(self) -> None:
        reply = cli.mcp_dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        instructions = reply["result"]["instructions"]
        self.assertIn("installation", instructions)
        self.assertIn("business", instructions)
        self.assertIn("once", instructions)


if __name__ == "__main__":
    unittest.main()
