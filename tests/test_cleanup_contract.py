"""Cleanup invariants, exercised against the runtime and private local fixtures."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

try:
    from .test_feedback import cli
except ImportError:
    from test_feedback import cli


class CleanupContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {"HOLYCRAB_CONFIG_DIR": str(self.root / "config"),
                                     "HOLYCRAB_NO_UPDATE_CHECK": "1"}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_command_reads_one_manifest_and_releases_snapshot(self):
        with patch.object(cli, "read_json_file", wraps=cli.read_json_file) as read, \
                patch.object(cli, "startup_maintenance"), redirect_stdout(io.StringIO()):
            for _ in range(2):
                with patch.object(cli.sys, "argv", ["holycrab", "models", "list", "--json"]):
                    self.assertEqual(cli.main(), 0)
            reads = [c for c in read.call_args_list if c.args[0].name == "capabilities.json"]
            self.assertEqual(len(reads), 2)

    def test_mcp_reads_one_manifest_per_call(self):
        with patch.object(cli, "read_json_file", wraps=cli.read_json_file) as read:
            for identifier in (1, 2):
                response = cli.mcp_dispatch({"jsonrpc": "2.0", "id": identifier, "method": "tools/call",
                    "params": {"name": "capabilities_list", "arguments": {}}})
                self.assertFalse(response["result"].get("isError"))
            reads = [c for c in read.call_args_list if c.args[0].name == "capabilities.json"]
            self.assertEqual(len(reads), 2)

    def test_mcp_multi_validation_shares_snapshot_without_submitting(self):
        with patch.object(cli, "read_json_file", wraps=cli.read_json_file) as read, \
                patch.object(cli, "send", return_value=(200, {"code": 200, "data": {"frozenCredit": 5}})) as send:
            for _ in range(2):
                result = cli.mcp_tool_call("generation_create", {"kind": "image", "request": {
                    "model": "seedream-5-0-lite-260128", "prompt": "fixture", "size": "2K"},
                    "attemptId": "fixture", "confirmed": False})
                self.assertTrue(result["confirmationRequired"])
            reads = [c for c in read.call_args_list if c.args[0].name == "capabilities.json"]
            self.assertEqual(len(reads), 2)
            self.assertTrue(all(c.args[1].endswith("freeze-credit") for c in send.call_args_list))

    def test_nested_model_mutation_does_not_escape_operation(self):
        with cli.capability_operation():
            original = cli.find_model("seedream-5-0-lite-260128")
            changed = cli.find_model(original["id"])
            changed["requestSchema"]["required"].append("inventedField")
            changed["id"] = "mutated"
            manifest = cli.load_capabilities()
            manifest["imageModels"].clear()
            self.assertEqual(cli.find_model(original["id"]), original)
        self.assertEqual(cli.find_model(original["id"]), original)

    def test_scope_releases_after_exception(self):
        with patch.object(cli, "read_json_file", wraps=cli.read_json_file) as read:
            with self.assertRaises(ValueError), cli.capability_operation():
                cli.all_models()
                raise ValueError("fixture")
            with cli.capability_operation():
                cli.all_models()
            self.assertEqual(read.call_count, 2)

    def test_qr_scan_throttled_by_config_directory(self):
        with patch.object(cli.time, "monotonic", side_effect=[10, 12, 14, 15]), \
                patch.object(cli, "_cleanup_authorization_qrs") as scan:
            cli.cleanup_authorization_qrs()
            cli.cleanup_authorization_qrs()
            with patch.dict(os.environ, {"HOLYCRAB_CONFIG_DIR": str(self.root / "other")}):
                cli.cleanup_authorization_qrs()
            cli.cleanup_authorization_qrs()
            self.assertEqual(scan.call_count, 3)

    def test_qr_cleanup_failure_is_nonblocking_and_throttled(self):
        with patch.object(cli.time, "monotonic", side_effect=[10, 11]), \
                patch.object(cli, "_cleanup_authorization_qrs", side_effect=OSError("fixture")) as scan:
            self.assertIn("Could not clean", cli.cleanup_authorization_qrs())
            self.assertIsNone(cli.cleanup_authorization_qrs())
            self.assertEqual(scan.call_count, 1)

    def test_terminal_qr_removal_bypasses_scan_throttle(self):
        directory = cli.authorization_cache_dir() / "auth1"
        directory.mkdir()
        (directory / "qr.png").write_bytes(b"fixture")
        (directory / "metadata.json").write_text(json.dumps({"deleteAfter": 9999999999}))
        cli.cleanup_authorization_qrs()
        with patch.object(cli, "send", return_value=(200, {"code": 200, "data": {
            "authorizationId": "auth1", "status": "SUCCEEDED", "group": {"uniqId": "group1", "name": "Fixture"}}})):
            result = cli.get_authorization("auth1")
        self.assertFalse(directory.exists())
        self.assertIn("UPLOAD", result["nextAction"]["code"])

    def test_auth_compatibility_hidden_but_keeps_same_handler(self):
        parser = cli.build_parser()
        for command in (["auth", "--help"], ["credits", "--help"]):
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaises(SystemExit):
                parser.parse_args(command)
            self.assertNotIn("set-key", output.getvalue())
            self.assertNotIn("estimate", output.getvalue())
        self.assertIs(parser.parse_args(["auth", "set-key"]).func, parser.parse_args(["setup"]).func)
        legacy = parser.parse_args(["credits", "estimate", "--kind", "image", "--json", "{}"])
        current = parser.parse_args(["generate", "estimate", "--kind", "image", "--json", "{}"])
        self.assertIs(legacy.func, current.func)

    def test_skill_configured_state_is_documented(self):
        skill = (Path(__file__).parents[1] / "holycrab/SKILL.md").read_text()
        self.assertIn("`CONFIGURED`", skill)
        self.assertIn("not a failed", skill)


if __name__ == "__main__":
    unittest.main()
