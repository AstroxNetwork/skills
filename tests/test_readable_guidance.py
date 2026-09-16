"""Readable terminal results; every network response is a local fixture."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

try:
    from .test_feedback import cli, TerminalBuffer, ok
except ImportError:
    from test_feedback import cli, TerminalBuffer, ok


class ReadableGuidanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = patch.dict(os.environ, {"HOLYCRAB_CONFIG_DIR": self.temp.name,
                                               "HOLYCRAB_NO_UPDATE_CHECK": "1"}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        secrets = patch.object(cli, "SENSITIVE_OUTPUT_VALUES", set())
        secrets.start()
        self.addCleanup(secrets.stop)

    def invoke(self, argv, *, terminal=True):
        output = TerminalBuffer() if terminal else io.StringIO()
        errors = TerminalBuffer() if terminal else io.StringIO()
        with patch.object(cli.sys, "argv", ["holycrab", *argv]), \
                patch.object(cli, "startup_maintenance"), \
                redirect_stdout(output), redirect_stderr(errors):
            code = cli.main()
        return code, output.getvalue(), errors.getvalue()

    def test_setup_terminal_has_one_short_welcome_and_no_internal_json(self):
        with patch.object(cli.getpass, "getpass", return_value="hc_fixture_hidden_key"), \
                patch.object(cli, "send", return_value=ok({"username": "Fixture", "credit": 10})):
            code, output, errors = self.invoke(["setup"])
        self.assertEqual(code, 0)
        self.assertIn("account connected", output)
        self.assertIn("  - Create product listing images", output)
        self.assertIn("  - Make advertising videos", output)
        self.assertIn("  - Produce product voiceovers", output)
        self.assertEqual((output + errors).count("account connected"), 1)
        self.assertLessEqual(len(output.splitlines()), 18)
        for fragment in ('"onboarding"', "agentInstruction", "Business uses:", "hc_fixture_hidden_key"):
            self.assertNotIn(fragment, output + errors)

    def test_piped_setup_keeps_complete_existing_json(self):
        with patch.object(cli.getpass, "getpass", return_value="hc_fixture_hidden_key"), \
                patch.object(cli, "send", return_value=ok({"username": "Fixture", "credit": 10})):
            code, output, errors = self.invoke(["setup"], terminal=False)
        result = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(result["credit"], 10)
        self.assertEqual(result["onboarding"]["state"], "READY")
        self.assertEqual(len(result["onboarding"]["businessUses"]), 6)
        self.assertIn("agentInstruction", result["onboarding"])
        self.assertEqual(errors, "")

    def test_reconnection_and_status_do_not_repeat_business_welcome(self):
        cli.save_config({"apiKey": "hc_fixture_existing"})
        with patch.object(cli.getpass, "getpass", return_value="hc_fixture_hidden_key"), \
                patch.object(cli, "send", return_value=ok({"username": "Fixture"})):
            for argv in (["setup"], ["auth", "status"]):
                with self.subTest(argv=argv):
                    code, output, errors = self.invoke(argv)
                    self.assertEqual(code, 0)
                    self.assertNotIn("Create product listing images", output + errors)
                    self.assertNotIn("What would you like", output + errors)

    def test_unverified_and_environment_override_do_not_claim_connected(self):
        cases = (({}, ["setup", "--no-verify"]),
                 ({"HOLYCRAB_API_KEY": "hc_fixture_environment"}, ["setup"]))
        for environment, argv in cases:
            with self.subTest(argv=argv), patch.dict(os.environ, environment), \
                    patch.object(cli.getpass, "getpass", return_value="hc_fixture_hidden_key"), \
                    patch.object(cli, "send", return_value=ok({"username": "Fixture"})):
                code, output, errors = self.invoke(argv)
            self.assertEqual(code, 0)
            self.assertNotIn("account connected", output + errors)
            self.assertIn("holycrab auth status", output + errors)

    def test_stdin_setup_keeps_json_even_if_output_is_a_terminal(self):
        with patch.object(cli.sys, "stdin", io.StringIO("hc_fixture_stdin\n")):
            code, output, errors = self.invoke(["setup", "--stdin", "--no-verify"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["onboarding"]["state"], "VERIFY_ACCOUNT")
        self.assertNotIn("hc_fixture_stdin", output + errors)

    def test_explicit_doctor_json_is_complete_in_terminal(self):
        code, output, _ = self.invoke(["doctor", "--json"])
        self.assertEqual(code, 0)
        self.assertIn("agentInstruction", json.loads(output)["onboarding"])

    def test_doctor_without_json_has_compact_checks_not_internal_guidance(self):
        code, output, _ = self.invoke(["doctor"])
        self.assertEqual(code, 0)
        self.assertIn("Local checks passed", output)
        self.assertNotIn('"checks"', output)
        self.assertNotIn("agentInstruction", output)

    def test_local_doctor_after_auth_success_does_not_request_reverification(self):
        cli.save_config({"apiKey": "hc_fixture_existing"})
        with patch.object(cli, "send", return_value=ok({"username": "Fixture"})) as transport:
            auth_code, auth_output, _ = self.invoke(["auth", "status"])
            code, output, errors = self.invoke(["doctor"])
            json_code, json_output, _ = self.invoke(["doctor", "--json"])
            status = cli.mcp_tool_call("cli_status", {})
        self.assertEqual(auth_code, 0)
        self.assertIn("account connected", auth_output)
        self.assertEqual(code, 0)
        self.assertIn("API Key: configured", output)
        for fragment in ("Account not verified", "holycrab auth status", "account connected"):
            self.assertNotIn(fragment, output + errors)
        self.assertEqual(json_code, 0)
        for guidance in (json.loads(json_output)["onboarding"], status["onboarding"]):
            self.assertEqual(guidance["state"], "CONFIGURED")
            self.assertIsNone(guidance["command"])
        self.assertEqual(transport.call_count, 1)

    def test_local_doctor_environment_key_is_configured_not_verified(self):
        with patch.dict(os.environ, {"HOLYCRAB_API_KEY": "hc_fixture_environment"}), \
                patch.object(cli, "send") as transport:
            code, output, errors = self.invoke(["doctor"])
        self.assertEqual(code, 0)
        self.assertIn("API Key: configured", output)
        for fragment in ("Account not verified", "holycrab auth status", "account connected", "hc_fixture_environment"):
            self.assertNotIn(fragment, output + errors)
        transport.assert_not_called()

    def test_installer_still_guides_configured_account_verification(self):
        cli.save_config({"apiKey": "hc_fixture_existing"})
        with patch.object(cli, "send") as transport:
            guidance = cli.local_health_report()["onboarding"]
        text = cli.format_onboarding(guidance, installed=True)
        self.assertIn("HolyCrab 0.4.4 installed", text)
        self.assertIn("Next: verify your account", text)
        self.assertIn("holycrab auth status", text)
        self.assertNotIn("account connected", text)
        self.assertNotIn("Account not verified", text)
        transport.assert_not_called()

    def test_online_doctor_keeps_actual_account_verification_feedback(self):
        cli.save_config({"apiKey": "hc_fixture_existing"})
        for payload, valid in (({"username": "Fixture"}, True), ({}, False)):
            with self.subTest(valid=valid), patch.object(cli, "send", return_value=ok(payload)) as transport, \
                    patch.object(cli, "check_for_update", return_value={"updateAvailable": False}):
                code, output, errors = self.invoke(["doctor", "--online"])
            self.assertEqual(code, 0 if valid else 1)
            self.assertIn("account connected" if valid else "Account not verified", output)
            self.assertNotIn("hc_fixture_existing", output + errors)
            self.assertEqual(transport.call_count, 1)

    def test_authorization_success_keeps_person_and_explicit_upload_next_step(self):
        reply = {"authorizationId": "auth1", "status": "SUCCEEDED",
                 "group": {"uniqId": "group1", "name": "Fixture person"}}
        with patch.object(cli, "send", return_value=ok(reply)):
            code, output, _ = self.invoke(["real-human", "get", "auth1"])
        self.assertEqual(code, 0)
        for fragment in ("Fixture person", "group1", "No materials", "upload", "Next:"):
            self.assertIn(fragment, output)
        self.assertNotIn('"nextAction"', output)
        self.assertTrue(all(len(line) <= 80 for line in output.splitlines()))

    def test_empty_person_assets_explains_upload_not_just_an_empty_list(self):
        with patch.object(cli, "send", return_value=ok({"total": 0, "records": [], "current": 1, "pages": 0})):
            code, output, _ = self.invoke(["real-human", "assets", "list", "--group", "group1"])
        self.assertEqual(code, 0)
        self.assertIn("No materials", output)
        self.assertIn("upload", output)
        self.assertNotIn("asset_upload", output)

    def test_processing_asset_and_timeout_keep_same_recovery_command(self):
        reply = {"uniqId": "asset1", "step": "UPLOADED", "name": "Fixture image"}
        with patch.object(cli, "send", return_value=ok(reply)):
            code, output, _ = self.invoke(["assets", "get", "asset1"])
        self.assertEqual(code, 0)
        self.assertIn("holycrab assets wait asset1 --timeout 600", output)
        self.assertIn("Do not upload", output)
        with patch.object(cli, "send", return_value=ok(reply)):
            code, output, errors = self.invoke(["assets", "wait", "asset1", "--timeout", "0"])
        self.assertEqual(code, 2)
        self.assertIn("holycrab assets get asset1", output)
        self.assertEqual((output + errors).count("Wait timed out"), 1)

    def test_task_query_and_timeout_are_readable_and_do_not_resubmit(self):
        with patch.object(cli, "send", return_value=ok({"uniqId": "task1", "step": 1})) as transport:
            code, output, _ = self.invoke(["tasks", "wait", "task1", "--timeout", "0"])
        self.assertEqual(code, 2)
        self.assertIn("Status: Processing", output)
        self.assertIn("holycrab tasks get task1", output)
        self.assertTrue(all(call.args[0] == "GET" for call in transport.call_args_list))
        self.assertNotIn('"nextAction"', output)

    def test_unknown_attempt_keeps_no_retry_and_no_result_is_not_proof(self):
        cli.save_attempts({"attempt1": {"state": "unknown", "attemptId": "attempt1"}})
        code, output, _ = self.invoke(["generate", "attempts", "get", "attempt1"])
        self.assertEqual(code, 0)
        self.assertIn("Do not submit", output)
        self.assertIn("does not prove", output)
        self.assertIn("holycrab tasks list --page 1 --page-size 20", output)

    def test_upload_preview_keeps_entire_inventory_and_only_one_confirmation(self):
        paths = []
        for name in ("one.png", "two.png"):
            path = Path(self.temp.name) / name
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fixture")
            paths.append(str(path))
        with patch.object(cli.sys, "stdin", TerminalBuffer()), \
                patch.object(cli, "find_real_human_group", return_value={"uniqId": "group1", "name": "Fixture person"}), \
                patch.object(cli, "read_confirmation", return_value="n") as confirm, \
                patch.object(cli, "send") as transport:
            code, output, _ = self.invoke(["assets", "upload", *paths, "--real-human-group", "group1"])
        self.assertEqual(code, 2)
        self.assertIn("Upload preview", output)
        # Windows TEMP can use an 8.3 alias (RUNNER~1). Preview deliberately
        # shows the resolved long path, not the originally supplied alias.
        resolved_paths = [str(Path(path).resolve(strict=True)) for path in paths]
        for fragment in (*resolved_paths, "Fixture person", "group1", "image/png", "Size:", "checked online"):
            self.assertIn(fragment, output)
        self.assertNotIn('"uploadPlanId"', output)
        self.assertEqual(confirm.call_count, 1)
        transport.assert_not_called()

    def test_installer_summary_is_shared_short_and_has_no_internal_rules(self):
        for configured in (False, True):
            with self.subTest(configured=configured):
                guidance = cli.onboarding_guidance(configured=configured)
                text = cli.format_onboarding(guidance, installed=True)
                self.assertIn("HolyCrab 0.4.4 installed", text)
                self.assertIn(guidance["command"], text)
                self.assertIn("Create product listing images", text)
                self.assertNotIn("For Agents", text)
                self.assertNotIn(guidance["agentInstruction"], text)
                self.assertLessEqual(len(text.splitlines()), 16)

    def test_progress_wraps_explanation_but_does_not_wrap_a_command(self):
        command = "holycrab assets wait " + " ".join("asset" + str(number) for number in range(10)) + " --timeout 600"
        with redirect_stderr(TerminalBuffer()) as errors:
            cli.command_progress("Explanation " * 20)
            cli.command_progress("Run: " + command)
        lines = errors.getvalue().splitlines()
        self.assertTrue(all(len(line) <= 80 for line in lines[:-1]))
        self.assertEqual(lines[-1], "Run: " + command)

    def test_models_list_is_a_compact_table_not_repeated_field_blocks(self):
        code, output, errors = self.invoke(["models", "list"])
        self.assertEqual(code, 0)
        for model in cli.all_models():
            self.assertIn(model["id"], output)
        self.assertIn("Models", output)
        self.assertLessEqual(len(output.splitlines()), len(cli.all_models()) + 5)
        self.assertNotIn("Next:", output + errors)

    def test_task_list_shows_one_row_per_task_without_duplicate_next_steps(self):
        tasks = [{"uniqId": f"task{i}", "step": 1, "taskType": "VIDEO",
                  "model": "A lengthy fixture model label " * 30} for i in range(20)]
        reply = {"records": tasks, "total": 40, "current": 1, "size": 20, "pages": 2}
        with patch.object(cli, "send", return_value=ok(reply)):
            code, output, _ = self.invoke(["tasks", "list"])
            _, wire, _ = self.invoke(["tasks", "list"], terminal=False)
        self.assertEqual(code, 0)
        self.assertIn("Page 1 of 2", output)
        self.assertIn("Processing", output)
        self.assertLessEqual(len(output.splitlines()), 26)
        self.assertNotIn("Next:", output)
        self.assertNotIn("lengthy fixture", output)
        self.assertEqual(json.loads(wire)["response"]["data"]["records"][0]["model"], tasks[0]["model"])

    def test_empty_lists_and_mutations_have_specific_result_headings(self):
        fixtures = [(["tasks", "list"], {"records": [], "total": 0}, "No tasks"),
                    (["real-human", "groups", "list"], {"records": [], "total": 0}, "No authorized people"),
                    (["real-human", "groups", "rename", "group1", "--name", "Fixture"],
                     {"uniqId": "group1", "name": "Fixture"}, "Person renamed")]
        for argv, reply, heading in fixtures:
            with self.subTest(argv=argv), patch.object(cli, "send", return_value=ok(reply)):
                code, output, _ = self.invoke(argv)
            self.assertEqual(code, 0)
            self.assertIn(heading, output)
        _, output, _ = self.invoke(["generate", "attempts", "list"])
        self.assertIn("No submission attempts", output)

    def test_completed_task_has_output_count_not_raw_urls_and_duplicate_audio_ids(self):
        reply = {"uniqId": "task1", "step": 2, "taskType": "AUDIO",
                 "audioIds": '["https://cdn.example/one.mp3", "https://cdn.example/two.mp3"]'}
        with patch.object(cli, "send", return_value=ok(reply)):
            code, output, _ = self.invoke(["tasks", "get", "task1"])
            _, wire, _ = self.invoke(["tasks", "get", "task1"], terminal=False)
        self.assertEqual(code, 0)
        self.assertIn("Completed", output)
        self.assertIn("Outputs: 2", output)
        self.assertNotIn("https://cdn.example", output)
        self.assertNotIn("Audio ids", output)
        self.assertEqual(len(json.loads(wire)["response"]["data"]["audioUrls"]), 2)

    def test_doctor_failure_only_expands_failed_checks(self):
        reply = {"ok": False, "version": cli.VERSION, "checks": {"coreFiles": {
            "intact.py": {"exists": True, "hashMatches": True, "sha256": "a" * 64},
            "broken.py": {"exists": True, "hashMatches": False, "error": "Hash mismatch"}}},
                 "repairs": ["holycrab update"]}
        with patch.object(cli, "local_health_report", return_value=reply):
            code, output, _ = self.invoke(["doctor"])
        self.assertEqual(code, 1)
        self.assertIn("broken.py", output)
        self.assertIn("Hash mismatch", output)
        self.assertNotIn("intact.py", output)
        self.assertNotIn("a" * 64, output)

    def test_partial_upload_is_complete_inventory_without_raw_response_trees(self):
        reply = {"uploaded": [{"file": "/fixture/one.png", "assetUniqId": "asset1", "state": "uploaded",
                                "nextAction": cli.asset_next_action("asset1", "UPLOADED")}],
                 "failedOrUnknown": {"file": "/fixture/two.png", "assetUniqId": "asset2", "state": "unknown",
                                     "phase": "registration", "error": "Connection lost"},
                 "notAttempted": [{"file": "/fixture/three.png"}],
                 "nextAction": cli.asset_next_action("asset2", None)}
        output = cli.format_terminal_result(reply)
        for item in ("Uploaded: 1", "Failed or unknown: 1", "Not attempted: 1", "asset1", "asset2",
                     "/fixture/one.png", "/fixture/two.png", "/fixture/three.png", "Connection lost"):
            self.assertIn(item, output)
        self.assertEqual(output.count("Next:"), 1)
        self.assertIn("Do not upload", output)
        self.assertLessEqual(len(output.splitlines()), 27)

    def test_paths_preserve_repeated_spaces_and_commands_are_not_wrapped(self):
        path = "/fixture/用户  文件夹/" + "verylongfilename" * 8 + ".png"
        preview = {"uploadPlanId": "plan1", "files": [{"path": path, "name": "file.png",
                   "size": 25, "mediaType": "image", "contentType": "image/png"}]}
        output = cli.format_terminal_result(preview)
        self.assertIn("   Path: " + path, output.splitlines())
        download = cli.format_terminal_result({"taskId": "task1", "output": path, "bytes": 25})
        self.assertIn("Download completed", download)
        self.assertIn("Path: " + path, download.splitlines())

    def test_authorization_failure_expiry_and_unknown_upload_keep_honest_recovery(self):
        for status in ("FAILED", "EXPIRED"):
            with self.subTest(status=status), patch.object(cli, "send", return_value=ok({
                    "authorizationId": "auth1", "status": status})):
                code, output, _ = self.invoke(["real-human", "get", "auth1"])
            self.assertEqual(code, 0)
            self.assertIn("auth1", output)
            self.assertIn("Ask before starting a new authorization", output)
            self.assertNotIn("holycrab real-human start", output)

    def test_update_check_is_not_an_installation_or_unverified_latest_claim(self):
        for reply, heading in (({"updateAvailable": False}, "Version status unavailable"),
                               ({"updateAvailable": False, "latestVersion": "0.4.0"}, "No newer stable release"),
                               ({"updateAvailable": True, "latestVersion": "0.4.5",
                                 "releasePage": "https://github.com/AstroxNetwork/skills/releases/tag/v0.4.5"},
                                "Update available")):
            with self.subTest(reply=reply), patch.object(cli, "check_for_update", return_value=reply), \
                    patch.object(cli, "run_update") as install:
                code, output, _ = self.invoke(["update", "--check"])
            self.assertEqual(code, 0)
            self.assertIn(heading, output)
            self.assertNotIn("Update completed", output)
            install.assert_not_called()

    def test_generation_confirmation_shows_exact_request_but_input_json_does_not_force_json_output(self):
        payload = {"model": "seedream-5-0-lite-260128", "prompt": "A blue fixture product", "size": "2K"}
        with patch.object(cli.sys, "stdin", TerminalBuffer()), \
                patch.object(cli, "send", return_value=ok({"frozenCredit": 5})) as transport, \
                patch.object(cli, "read_confirmation", return_value="n") as confirm:
            code, output, errors = self.invoke(["generate", "create", "--kind", "image", "--json", json.dumps(payload)])
        self.assertEqual(code, 2)
        for fragment in (payload["model"], payload["prompt"], payload["size"], "Credits: 5"):
            self.assertIn(fragment, output)
        self.assertNotIn('"estimate"', output)
        self.assertIn("Cancelled", errors)
        self.assertEqual(confirm.call_count, 1)
        self.assertTrue(all("freeze-credit" in call.args[1] for call in transport.call_args_list))

    def test_progress_cannot_execute_terminal_controls(self):
        with redirect_stderr(TerminalBuffer()) as errors:
            cli.command_progress("File: fixture\x1b[2J\x07.png")
        self.assertNotIn("\x1b", errors.getvalue())
        self.assertNotIn("\x07", errors.getvalue())

    def test_model_details_omit_schema_boilerplate_but_keep_capability_limits(self):
        code, output, _ = self.invoke(["models", "show", "seedance-2-0"])
        self.assertEqual(code, 0)
        for value in ("seedance-2-0", "480p", "1080p", "15", "Reference limits"):
            self.assertIn(value, output)
        for key in ("Additional properties", "One of", "Properties:", "Type: integer"):
            self.assertNotIn(key, output)
        self.assertLessEqual(len(output.splitlines()), 35)

    def test_upgrade_summary_does_not_repeat_first_use_introduction(self):
        output = cli.format_onboarding(cli.onboarding_guidance(configured=True), installed=True, upgrading=True)
        self.assertIn("HolyCrab 0.4.4 installed", output)
        self.assertIn("holycrab auth status", output)
        self.assertNotIn("Create product listing images", output)
        self.assertNotIn("What would you like", output)

    def test_malformed_list_records_do_not_crash_or_claim_no_tasks(self):
        output = cli.format_terminal_result({"records": [None, 1, "bad"], "total": 3}, view="tasks")
        self.assertIn("Unreadable records: 3", output)
        self.assertNotIn("No tasks", output)
        output = cli.format_terminal_result({"records": [None, {"uniqId": "task1", "step": True}], "total": 2}, view="tasks")
        self.assertIn("task1", output)
        self.assertIn("Unreadable records: 1", output)
        self.assertNotIn("Processing", output)

    def test_upload_only_reports_confirmed_deletion_and_never_calls_registered_files_ready(self):
        reply = {"uploaded": [{"file": "/fixture/one.png", "assetUniqId": "asset1", "state": "uploaded", "ready": False}],
                 "failedOrUnknown": None, "notAttempted": [],
                 "nextAction": cli.next_action("WAIT_FOR_ASSETS", "Wait until ready", "holycrab assets wait asset1 --timeout 600")}
        output = cli.format_terminal_result(reply)
        self.assertIn("wait for processing", output)
        self.assertNotIn("Materials ready", output)


if __name__ == "__main__":
    unittest.main()
