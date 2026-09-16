"""Real Unix PTY checks, using local transport fixtures only."""
import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts/holycrab_cli.py"


@unittest.skipIf(os.name == "nt", "Unix PTY; native Windows hidden input is manual acceptance")
class TerminalFeedbackTests(unittest.TestCase):
    def test_installer_in_real_terminal_groups_phases_and_has_one_short_summary(self):
        import pty
        with tempfile.TemporaryDirectory() as root:
            environment = {**os.environ, "HOME": root, "SHELL": "/bin/zsh",
                           "HOLYCRAB_CONFIG_DIR": str(Path(root) / "config"), "HOLYCRAB_INSTALL_MCP": "0",
                           "HOLYCRAB_INSTALL_AGENTS": "codex", "HOLYCRAB_INSTALL_PREFIX": str(Path(root) / ".local"),
                           "HOLYCRAB_INSTALL_SOURCE_DIR": str(SCRIPT.parents[2]), "HOLYCRAB_NO_UPDATE_CHECK": "1"}
            environment.pop("HOLYCRAB_API_KEY", None)
            master, slave = pty.openpty()
            process = subprocess.Popen(["sh", str(SCRIPT.parents[2] / "install.sh")],
                                       stdin=slave, stdout=slave, stderr=slave, env=environment)
            os.close(slave)
            self.addCleanup(os.close, master)
            self.addCleanup(lambda: process.poll() is None and process.kill())
            output = self.read_until(master, b"If holycrab is not found", timeout=10)
            self.assertEqual(process.wait(timeout=5), 0)
            for number in range(1, 6):
                self.assertEqual(output.count(f"[{number}/5]".encode()), 1)
            self.assertEqual(output.count(b"HolyCrab 0.4.3 installed"), 1)
            self.assertIn(b"Next: connect your account", output)
            self.assertNotIn(b"For Agents:", output)
            self.assertNotIn(b"Verifying SHA-256 for", output)
            self.assertNotIn(b"agentInstruction", output)

    def child(self, root, argv, body):
        import pty
        master, slave = pty.openpty()
        source = ("import importlib.util,sys,time\n"
                  f"spec=importlib.util.spec_from_file_location('fixture_cli',{str(SCRIPT)!r})\n"
                  "cli=importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)\n"
                  + body + f"\nsys.argv=['holycrab']+{argv!r}\nraise SystemExit(cli.main())\n")
        environment = {**os.environ, "HOLYCRAB_CONFIG_DIR": root, "HOLYCRAB_NO_UPDATE_CHECK": "1"}
        environment.pop("HOLYCRAB_API_KEY", None)
        process = subprocess.Popen([sys.executable, "-c", source], stdin=slave, stdout=slave, stderr=slave, env=environment)
        os.close(slave)
        self.addCleanup(os.close, master)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        return process, master

    def read_until(self, master, marker, timeout=5):
        end, output = time.monotonic() + timeout, b""
        while marker not in output and time.monotonic() < end:
            if select.select([master], [], [], max(0, end - time.monotonic()))[0]:
                try: output += os.read(master, 65536)
                except OSError: break
        self.assertIn(marker, output)
        return output

    def test_hidden_input_immediately_acknowledges_and_guides_to_agent(self):
        with tempfile.TemporaryDirectory() as root:
            body = "def fixture_send(*a,**k):\n time.sleep(0.3)\n return 200,{'code':200,'data':{'credit':10,'username':'Fixture'}}\ncli.send=fixture_send"
            process, master = self.child(root, ["setup"], body)
            output = self.read_until(master, b"Paste API Key (input hidden):")
            os.write(master, b"hc_test_pty_hidden_input\n")
            output += self.read_until(master, b"Verifying", timeout=1)
            output += self.read_until(master, b"\r\nWhat would you like to work on first?")
            self.assertEqual(process.wait(timeout=5), 0)
            self.assertNotIn(b"hc_test_pty_hidden_input", output)
            self.assertNotIn(b'"onboarding"', output)
            self.assertNotIn(b"agentInstruction", output)
            self.assertEqual(output.count(b"HolyCrab account connected"), 1)
            self.assertIn(b"  - Create product listing images", output)
            self.assertLess(output.index(b"Verifying"), output.index(b"saved locally"))

    def test_real_sigint_wait_exits_130_without_traceback(self):
        with tempfile.TemporaryDirectory() as root:
            body = "cli.send=lambda *a,**k:(200,{'code':200,'data':{'uniqId':'task1','step':1}})"
            process, master = self.child(root, ["tasks", "wait", "task1", "--interval", "10"], body)
            output = self.read_until(master, b'Status: Processing')
            os.kill(process.pid, signal.SIGINT)
            output += self.read_until(master, b"Stopped.")
            self.assertEqual(process.wait(timeout=5), 130)
            self.assertNotIn(b"Traceback", output)

    def test_real_sigint_generation_preserves_unknown_attempt(self):
        with tempfile.TemporaryDirectory() as root:
            body = ("def fixture_send(method,path,**k):\n"
                    " if 'freeze-credit' in path: return 200,{'code':200,'data':{'credit':5}}\n"
                    " cli.command_progress('Fixture submission started')\n time.sleep(30)\n"
                    " return 200,{'code':200,'data':{'uniqId':'task1'}}\ncli.send=fixture_send")
            request = json.dumps({"model": "seedream-5-0-lite-260128", "prompt": "fixture", "size": "2K"})
            argv = ["generate", "create", "--kind", "image", "--json", request, "--yes", "--attempt-id", "pty-interrupt"]
            process, master = self.child(root, argv, body)
            output = self.read_until(master, b"Fixture submission started")
            os.kill(process.pid, signal.SIGINT)
            output += self.read_until(master, b"do not retry automatically")
            self.assertEqual(process.wait(timeout=5), 130)
            self.assertNotIn(b"Traceback", output)
            self.assertEqual(json.loads((Path(root) / "attempts.json").read_text())["pty-interrupt"]["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
