"""Parser-derived feedback inventory. All remote operations use fixtures."""
from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

try:
    from .test_feedback import cli, TerminalBuffer, json_documents, ok
except ImportError:
    from test_feedback import cli, TerminalBuffer, json_documents, ok


COMMANDS = {
    "setup": [], "auth set-key": [], "auth status": [], "auth clear-key": [],
    "models list": ["--json"], "models show": ["seedream-5-0-lite-260128"],
    "credits balance": [], "credits estimate": [], "generate estimate": [], "generate create": [],
    "generate attempts list": [], "generate attempts get": ["fixture"],
    "tasks get": ["task1"], "tasks list": [], "tasks wait": ["task1", "--timeout", "0"],
    "download": ["task1"], "real-human start": ["--name", "Fixture"],
    "real-human get": ["auth1"], "real-human wait": ["auth1", "--timeout", "0"],
    "real-human groups list": [], "real-human groups rename": ["group1", "--name", "Fixture"],
    "real-human groups delete": ["group1", "--yes"], "real-human assets list": ["--group", "group1"],
    "real-human assets delete": ["asset1", "--group", "group1", "--yes"],
    "assets upload": [], "assets get": ["asset1"], "assets wait": ["asset1", "--timeout", "0"],
    "mcp serve": [], "update": ["--check"], "doctor": ["--json"], "uninstall": ["--yes"],
}


def leaf_commands(parser: argparse.ArgumentParser, path: tuple[str, ...] = ()) -> list[str]:
    children = [action for action in parser._actions if isinstance(action, argparse._SubParsersAction)]
    if not children:
        return [" ".join(path)]
    return [value for action in children for name, child in action.choices.items()
            for value in leaf_commands(child, (*path, name))]


class Remote(io.BytesIO):
    status = 200
    headers = {"Content-Length": "7"}


class CommandMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.file = self.root / "fixture.jpg"
        self.file.write_bytes(b"\xff\xd8\xff\xe0fixture")
        self.environment = patch.dict(os.environ, {"HOLYCRAB_CONFIG_DIR": str(self.root / "config"),
            "HOLYCRAB_API_KEY": "hc_test_matrix_only", "HOLYCRAB_NO_UPDATE_CHECK": "1"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        cli.reserve_attempt("fixture", {"attemptId": "fixture", "state": "unknown"})

    @staticmethod
    def payload() -> dict:
        return {"model": "seedream-5-0-lite-260128", "prompt": "fixture", "size": "2K"}

    def transport(self, method: str, path: str, **kwargs: object) -> tuple:
        if "freeze-credit" in path:
            return ok({"frozenCredit": 5})
        if path in {route[1] for route in cli.GENERATION_ROUTES.values()}:
            return ok({"uniqId": "task1"})
        if path == "/api/user/me":
            return ok({"credit": 20, "username": "Fixture"})
        if path == "/api/tasks":
            return ok({"records": [{"uniqId": "task1", "step": 2}], "total": 1, "pages": 1})
        if path == "/api/tasks/task1":
            return ok({"uniqId": "task1", "step": 2, "imageUrls": ["https://cdn.example/fixture.jpg"]})
        if path == "/api/real-human-authorizations/sessions":
            return ok({"authorizationId": "auth1", "h5Link": "https://verify.example/fixture", "expiresAt": "2099-01-01T00:00:00Z"})
        if path == "/api/real-human-authorizations/auth1":
            return ok({"authorizationId": "auth1", "status": "SUCCEEDED", "group": {"uniqId": "group1", "name": "Fixture"}})
        if path == "/api/real-human-groups":
            return ok({"records": [{"uniqId": "group1", "name": "Fixture", "assetCount": 1}], "total": 1, "pages": 1})
        if method == "DELETE":
            return ok(True)
        if path == "/api/real-human-groups/group1" and method == "PATCH":
            return ok({"uniqId": "group1", "name": "Fixture"})
        if path == "/api/real-human-groups/group1/assets":
            return ok({"records": [{"uniqId": "asset1", "step": "UPLOADED_TO_ARK"}], "total": 1, "pages": 1})
        if path == "/api/user-assets/asset1":
            return ok({"uniqId": "asset1", "step": "UPLOADED_TO_ARK", "name": "Fixture", "assetType": "IMAGE"})
        if path.endswith("pre-signed-download-url"):
            return ok({"uniqId": "asset1", "preSignedUrl": "https://storage.example/fixture", "objectKey": "fixture"})
        if path.endswith("/upload"):
            return ok(True)
        raise AssertionError(f"Unexpected fixture request: {method} {path}")

    def arguments(self, command: str) -> list[str]:
        argv = command.split() + COMMANDS[command]
        if command in {"credits estimate", "generate estimate", "generate create"}:
            argv += ["--kind", "image", "--json", json.dumps(self.payload())]
        if command == "generate create":
            argv += ["--yes", "--attempt-id", "matrix-create"]
        if command == "download":
            argv += ["--output", str(self.root / "result.jpg")]
        if command == "assets upload":
            argv += [str(self.file), "--yes"]
        return argv

    def context(self, stack: ExitStack, *, failure: bool = False) -> None:
        for name, kwargs in {
            "startup_maintenance": {}, "cleanup_authorization_qrs": {"return_value": None},
            "send": {"side_effect": (lambda *a, **k: (503, {"code": 503})) if failure else self.transport},
            "open_download": {"side_effect": lambda url: Remote(b"fixture")},
            "open_presigned_upload": {"side_effect": lambda request: Remote()},
            "validate_verification_link": {"side_effect": lambda url: url},
            "authorization_qr": {"return_value": (str(self.root / "qr.png"), None)},
            "local_health_report": {"return_value": {"ok": not failure, "version": cli.VERSION, "checks": {}, "repairs": []}},
            "check_for_update": {"return_value": {"updateAvailable": False, **({"error": "fixture failure"} if failure else {})}},
            "validated_uninstall_manifest": {"return_value": ({"agents": [], "mcp": False}, self.root / "prefix", self.root / "prefix/lib/holycrab", self.root / "prefix/bin/holycrab")},
            # Unit feedback tests do not launch a detached native cleaner. Its
            # actual self-delete and timing remain covered by Windows PS 5.1 CI.
            "schedule_windows_program_cleanup": {},
        }.items():
            stack.enter_context(patch.object(cli, name, **kwargs))
        stack.enter_context(patch.object(cli.getpass, "getpass", return_value="hc_test_input_only"))
        stack.enter_context(patch.object(cli.sys, "stdin", io.StringIO('{"jsonrpc":"2.0","id":1,"method":"initialize"}\n')))

    def test_inventory_is_exactly_31_commands(self) -> None:
        self.assertEqual(set(leaf_commands(cli.build_parser())), set(COMMANDS))
        self.assertEqual(len(COMMANDS), 31)

    def test_every_command_help_bypasses_all_housekeeping(self) -> None:
        for command in leaf_commands(cli.build_parser()):
            with self.subTest(command=command), ExitStack() as stack:
                housekeeping = [stack.enter_context(patch.object(cli, name)) for name in
                                ("startup_maintenance", "cleanup_authorization_qrs", "cleanup_upload_plans", "send")]
                stack.enter_context(patch.object(cli.sys, "argv", ["holycrab", *command.split(), "--help"]))
                stack.enter_context(redirect_stdout(io.StringIO()))
                with self.assertRaises(SystemExit) as stopped:
                    cli.main()
                self.assertEqual(stopped.exception.code, 0)
                for method in housekeeping:
                    method.assert_not_called()

    def test_all_command_success_results_noninteractive_and_tty(self) -> None:
        for terminal in (False, True):
            for command in COMMANDS:
                with self.subTest(command=command, terminal=terminal), ExitStack() as stack:
                    # Each matrix row gets isolated one-shot records and outputs.
                    cli.save_attempts({"fixture": {"attemptId": "fixture", "state": "unknown"}})
                    (self.root / "result.jpg").unlink(missing_ok=True)
                    self.context(stack)
                    output, errors = (TerminalBuffer(), TerminalBuffer()) if terminal else (io.StringIO(), io.StringIO())
                    stack.enter_context(redirect_stdout(output)); stack.enter_context(redirect_stderr(errors))
                    stack.enter_context(patch.object(cli.sys, "argv", ["holycrab", *self.arguments(command)]))
                    self.assertEqual(cli.main(), 0, output.getvalue() + errors.getvalue())
                    json_output = not terminal or command in {"models list", "doctor", "mcp serve"}
                    if json_output and command not in {"auth clear-key", "uninstall"}:
                        values = json_documents(output.getvalue())
                        self.assertTrue(values)
                        self.check_actions(values)
                    elif terminal:
                        self.assertTrue(output.getvalue())
                        self.assertNotIn('"nextAction"', output.getvalue())
                        self.assertNotIn("agentInstruction", output.getvalue())
                    if not terminal:
                        self.assertEqual(errors.getvalue(), "")
                    self.assertNotIn("hc_test_input_only", output.getvalue() + errors.getvalue())

    def check_actions(self, value: object) -> None:
        if isinstance(value, list):
            for item in value: self.check_actions(item)
        elif isinstance(value, dict):
            if "nextAction" in value:
                action = value["nextAction"]
                if action["command"] is not None:
                    self.assertTrue(action["command"].startswith("holycrab "))
                    self.assertNotRegex(action["command"], r"FILE|PERSON_NAME|GROUP_ID|@request|--yes")
                    cli.build_parser().parse_args(action["command"].split()[1:])
            for item in value.values(): self.check_actions(item)

    def test_all_command_failure_feedback(self) -> None:
        local_faults = {"models list": "load_capabilities", "models show": "load_capabilities",
            "generate attempts list": "load_attempts", "generate attempts get": "load_attempts",
            "auth clear-key": "save_config"}
        for terminal, command in ((terminal, command) for terminal in (False, True) for command in COMMANDS):
            with self.subTest(command=command, terminal=terminal), ExitStack() as stack:
                self.context(stack, failure=True)
                if command in local_faults:
                    stack.enter_context(patch.object(cli, local_faults[command], side_effect=OSError("fixture failure")))
                if command == "uninstall":
                    stack.enter_context(patch.object(cli, "remove_managed_mcp_registrations", side_effect=RuntimeError("MCP cleanup failed; program preserved")))
                if command == "mcp serve":
                    stack.enter_context(patch.object(cli.sys, "stdin", io.StringIO('{broken}\n')))
                output, errors = (TerminalBuffer(), TerminalBuffer()) if terminal else (io.StringIO(), io.StringIO())
                stack.enter_context(redirect_stdout(output)); stack.enter_context(redirect_stderr(errors))
                stack.enter_context(patch.object(cli.sys, "argv", ["holycrab", *self.arguments(command)]))
                code = cli.main()
                if command == "mcp serve":
                    self.assertIn("error", json.loads(output.getvalue()))
                else:
                    self.assertEqual(code, 1, output.getvalue() + errors.getvalue())
                self.assertTrue(output.getvalue() or errors.getvalue())
                self.assertNotIn("Traceback", output.getvalue() + errors.getvalue())
                self.assertNotIn("Task created.", errors.getvalue())

    def test_all_20_mcp_tools_schema_errors_are_protocol_only(self) -> None:
        self.assertEqual(len(cli.MCP_TOOLS), 20)
        for tool in cli.MCP_TOOLS:
            with self.subTest(tool=tool["name"]), redirect_stdout(io.StringIO()) as output, redirect_stderr(TerminalBuffer()) as errors:
                response = cli.mcp_dispatch({"id": 1, "method": "tools/call", "params": {
                    "name": tool["name"], "arguments": {"unknownArgument": True}}})
                self.assertTrue(response["result"]["isError"])
                self.assertEqual(output.getvalue() + errors.getvalue(), "")

    def test_all_20_mcp_tools_valid_results_are_protocol_only(self) -> None:
        arguments = {"capability_get": {"model": "seedream-5-0-lite-260128"},
            "generation_estimate": {"kind": "image", "request": self.payload()},
            "generation_create": {"kind": "image", "request": self.payload(), "confirmed": True, "attemptId": "mcp-create"},
            "generation_get": {"taskId": "task1"}, "generation_attempt_get": {"attemptId": "fixture"},
            "real_human_authorization_start": {"name": "Fixture"}, "real_human_authorization_get": {"authorizationId": "auth1"},
            "real_human_group_rename": {"groupUniqId": "group1", "name": "Fixture"},
            "real_human_group_delete": {"groupUniqId": "group1", "confirmed": True},
            "real_human_assets_list": {"groupUniqId": "group1"},
            "real_human_asset_delete": {"groupUniqId": "group1", "assetId": "asset1", "confirmed": True},
            "asset_get": {"assetId": "asset1"}, "asset_upload_prepare": {"files": [str(self.file)]}}
        for tool in cli.MCP_TOOLS:
            with self.subTest(tool=tool["name"]), ExitStack() as stack:
                self.context(stack)
                if tool["name"] == "asset_upload_execute":
                    preview = cli.prepare_upload_plan([str(self.file)])
                    args = {"uploadPlanId": preview["uploadPlanId"], "confirmed": True}
                else: args = arguments.get(tool["name"], {})
                output, errors = io.StringIO(), TerminalBuffer()
                stack.enter_context(redirect_stdout(output)); stack.enter_context(redirect_stderr(errors))
                response = cli.mcp_dispatch({"id": 1, "method": "tools/call", "params": {"name": tool["name"], "arguments": args}})
                self.assertFalse(response["result"]["isError"], response)
                self.check_actions(response["result"].get("structuredContent"))
                self.assertEqual(output.getvalue() + errors.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
