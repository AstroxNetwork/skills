"""Execute documentation and dynamic recovery commands using isolated fixtures."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shlex
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

try:
    from . import test_command_matrix as matrix
    from .test_feedback import cli, ok
except ImportError:
    import test_command_matrix as matrix
    from test_feedback import cli, ok

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("public_interface", ROOT / "tools/public_interface.py")
interface = importlib.util.module_from_spec(spec)
spec.loader.exec_module(interface)


class PublicGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = matrix.CommandMatrixTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.movie = self.fixture.root / "fixture.mp4"
        self.movie.write_bytes(b"\x00\x00\x00\x18ftypisomfixture")

    def substitute(self, command):
        replacements = {"REPLACE_WITH_ASSET_ID": "asset1", "AUTHORIZATION_ID": "auth1", "TASK_ID": "task1",
                        "ATTEMPT_ID": "fixture", "GROUP_ID": "group1", "ASSET_ID": "asset1"}
        for old, new in replacements.items():
            command = command.replace(old, new)
        argv = shlex.split(command)[1:]
        for index, token in enumerate(argv):
            if token.startswith("@/absolute/path/"):
                request = self.fixture.root / "request.json"
                request.write_text(json.dumps(self.fixture.payload()))
                argv[index] = "@" + str(request)
            elif token.startswith("/absolute/path/") or re.match(r"^[A-Za-z]:\\", token):
                argv[index] = str(self.movie if token.endswith(".mp4") else self.fixture.file)
            elif index > 0 and argv[index - 1] == "--output":
                argv[index] = str(self.fixture.root / Path(token).name)
        return argv

    def execute(self, argv):
        # Reinitialize one-shot records per example. No real HTTP or Agent process.
        cli.save_attempts({"fixture": {"attemptId": "fixture", "state": "unknown"}})
        for name in ("result.jpg", "result.mp4"):
            (self.fixture.root / name).unlink(missing_ok=True)
        if "--output" in argv:
            Path(argv[argv.index("--output") + 1]).unlink(missing_ok=True)
        with ExitStack() as stack:
            self.fixture.context(stack)
            stack.enter_context(patch.object(cli.sys, "stdin", io.StringIO("fixture-input\n")))
            stack.enter_context(patch("builtins.input", return_value="n"))
            stack.enter_context(redirect_stdout(io.StringIO()))
            stack.enter_context(redirect_stderr(io.StringIO()))
            stack.enter_context(patch.object(cli.sys, "argv", ["holycrab", *argv]))
            try:
                code = cli.main()
            except SystemExit as error:
                code = error.code
        self.assertIn(code, (0, 2), f"Guidance failed with code {code}: {argv}")

    def test_inventory_matches_real_parser_and_schema(self):
        inventory = interface.public_inventory(cli)
        self.assertEqual(len(inventory["commands"]), 31)
        self.assertEqual(len(inventory["mcpTools"]), 20)
        self.assertEqual({r["command"] for r in inventory["commands"] if r["compatibilityOnly"]},
                         {"auth set-key", "credits estimate"})
        self.assertNotIn("asset_upload", {r["name"] for r in inventory["mcpTools"]})
        self.assertNotIn("uninstall", {r["name"] for r in inventory["mcpTools"]})

    def test_all_repository_executable_cli_examples(self):
        total = 0
        for name in ("README.md", "HolyCrab CLI 使用指南.md", "holycrab/SKILL.md", "holycrab/references/api.md"):
            for example in interface.command_examples((ROOT / name).read_text(encoding="utf-8")):
                with self.subTest(source=name, command=example["command"]):
                    self.assertNotRegex(example["command"], r"\.\.\.|\[(?:FILE|ASSET)|\||auth set-key|credits estimate")
                    argv = self.substitute(example["command"])
                    cli.build_parser().parse_args(argv)
                    self.execute(argv)
                    total += 1
        self.assertGreaterEqual(total, 40)

    def test_dynamic_next_commands_execute_same_read_only_record(self):
        actions = []
        for step in (0, 1, 2, 3, None):
            actions.append(cli.task_next_action("task1", step, {}))
        for state in cli.ATTEMPT_STATES:
            actions.append(cli.attempt_next_action({"state": state, "taskId": "task1"}))
        for status in ("CREATED", "SUCCEEDED", "FAILED", "EXPIRED"):
            actions.append(cli.authorization_next_action(status, "auth1", "group1"))
        for step in ("UPLOADED_TO_ARK", "FAILED", "UPLOADED", "PROCESSING", None):
            actions.append(cli.asset_next_action("asset1", step))
        for action in actions:
            command = action["command"]
            if command is None:
                continue
            with self.subTest(action=action["code"], command=command):
                self.assertNotRegex(command, r"--yes|\b(create|start|upload|delete|rename)\b|[A-Z_]{3,}")
                self.execute(self.substitute(command))

    def test_missing_choice_keeps_command_null(self):
        self.assertIsNone(cli.authorization_next_action("SUCCEEDED", "auth1", "group1")["command"])
        self.assertIsNone(cli.asset_next_action("asset1", "UPLOADED_TO_ARK")["command"])
        self.assertIsNone(cli.task_next_action("task1", 2, {"imageUrls": ["https://cdn.example/a.jpg"]})["command"])
        with patch.object(cli, "send", return_value=ok({"frozenCredit": 5})):
            self.assertIsNone(cli.estimate_generation("image", self.fixture.payload())["nextAction"]["command"])

    def test_no_retired_entrypoints_or_runtime_mock_switch(self):
        self.assertFalse((ROOT / "holycrab/scripts/holycrab_api.py").exists())
        runtime = (ROOT / "holycrab/scripts/holycrab_cli.py").read_text(encoding="utf-8")
        self.assertNotRegex(runtime, r"(^|\n)(?:from|import)\s+(?:tests|tools|unittest|pytest)\b")
        self.assertNotRegex(runtime, r"HOLYCRAB_(?:MOCK|TEST|FIXTURE)")
        for name in ("README.md", "HolyCrab CLI 使用指南.md", "holycrab/SKILL.md", "holycrab/references/api.md"):
            self.assertNotRegex((ROOT / name).read_text(encoding="utf-8"), r"holycrab_api\.py|holycrab (?:poll-task|upload-asset)\b|`asset_upload`")

    def test_mcp_names_in_current_guidance_are_actual_tools(self):
        names = {tool["name"] for tool in cli.MCP_TOOLS}
        for path in (ROOT / "README.md", ROOT / "holycrab/SKILL.md", ROOT / "holycrab/references/api.md"):
            for code in re.findall(r"`([^`\n]+)`", path.read_text(encoding="utf-8")):
                if re.fullmatch(r"(?:real_human|generation|asset|capability|capabilities|account|cli)_[a-z_]+(?:\|[a-z_]+)*", code):
                    prefix = code.split("|")[0].rsplit("_", 1)[0] + "_"
                    tools = [code.split("|")[0]] + [prefix + part for part in code.split("|")[1:]]
                    for tool in tools:
                        self.assertIn(tool, names, f"Unknown MCP tool in {path.name}: {tool}")

    @unittest.skipUnless(os.environ.get("HOLYCRAB_GUIDE_SOURCE_DIR"), "Website integration requires an isolated website checkout")
    def test_all_five_language_website_executable_cli_examples(self):
        website = Path(os.environ["HOLYCRAB_GUIDE_SOURCE_DIR"])
        paths = sorted(website.glob("docs-site/*/guide/cli/**/index.mdx"))
        self.assertEqual(len(paths), 50)
        for path in paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"holycrab (?:auth set-key|credits estimate|poll-task|upload-asset)\b|`asset_upload`")
            for example in interface.command_examples(text):
                with self.subTest(page=path.relative_to(website), command=example["command"]):
                    self.assertNotRegex(example["command"], r"\.\.\.|\[|\|")
                    self.execute(self.substitute(example["command"]))


if __name__ == "__main__":
    unittest.main()
