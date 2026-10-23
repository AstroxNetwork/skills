"""ConPTY acceptance of real installed CLI HTTP operations and installer interruption."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from windows_console import Console


@contextmanager
def local_api():
    entered = threading.Event()
    release = threading.Event()
    rows = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, data):
            value = json.dumps({"code": 200, "data": data}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(value))); self.end_headers()
            try:
                self.wfile.write(value)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            rows.append(("GET", self.path, None))
            if "/tasks/" in self.path:
                self.reply({"uniqId": "conptyTask", "step": 1})
            else:
                time.sleep(.2)
                self.reply({"credit": 10, "username": "Native offline test"})

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            rows.append(("POST", self.path, json.loads(raw) if raw else None))
            if "freeze-credit" in self.path:
                self.reply({"frozenCredit": 5})
            else:
                entered.set()
                release.wait(30)
                self.reply({"uniqId": "conptyTask"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", rows, entered, release
    finally:
        release.set(); server.shutdown(); server.server_close(); thread.join(timeout=5)


def driver(suite, origin, arguments):
    path = suite.root / "console-http-driver.py"
    # Only the transport origin is substituted. Real serialization, sockets,
    # response parsing, state locking and CLI exception handling run unchanged.
    path.write_text(
        "import importlib.util,socket,sys\n"
        "_connect=socket.socket.connect\n"
        "def guarded(self,address):\n"
        " if address[0] not in ('127.0.0.1','::1'): raise OSError('Offline test blocked non-loopback address')\n"
        " return _connect(self,address)\n"
        "socket.socket.connect=guarded\n"
        f"spec=importlib.util.spec_from_file_location('installed_holycrab',{str(suite.cli)!r})\n"
        "cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)\n"
        f"cli._owner('base_url')._settings.DEFAULT_BASE_URL={origin!r}\n"
        f"sys.argv=['holycrab']+{arguments!r}\n"
        "raise SystemExit(cli.main())\n", encoding="utf-8")
    return [sys.executable, "-X", "utf8", str(path)]


def hidden_verification(suite):
    with local_api() as (origin, rows, _, _):
        env = dict(suite.env, HOLYCRAB_CONFIG_DIR=str(suite.root / "hidden setup config"))
        env.pop("HOLYCRAB_API_KEY", None)
        with Console(driver(suite, origin, ["setup"]), env, str(suite.root)) as console:
            console.until("Paste API Key (input hidden):")
            key = "hc_test_native_hidden_verification_123456789"
            console.send(key + "\r")
            console.until("Verifying", timeout=5)
            assert console.finish() == 0
            output = console.output
            assert key not in output
            assert output.count("HolyCrab account connected") == 1, output
            assert "Create product listing images" in output
            assert output.index("Verifying") < output.index("saved locally")
            assert "agentInstruction" not in output
        assert len(rows) == 1, rows
    return {"ConPTY": True, "HTTPRequests": 1, "hiddenInput": True, "immediateAcknowledgement": True}


def interrupt_wait(suite):
    with local_api() as (origin, rows, _, _):
        env = dict(suite.env, HOLYCRAB_API_KEY="hc_test_conpty_wait_123456789")
        with Console(driver(suite, origin, ["tasks", "wait", "conptyTask", "--interval", "10"]), env, str(suite.root)) as console:
            console.until("Status: Processing")
            console.send("\x03")
            assert console.finish() == 130
            assert "Traceback" not in console.output
            assert "Stopped." in console.output
        assert all(row[0] == "GET" for row in rows), rows
    return {"exitCode": 130, "HTTPRequests": len(rows), "noSubmission": True}


def interrupt_generation(suite):
    with local_api() as (origin, rows, entered, release):
        env = dict(suite.env, HOLYCRAB_API_KEY="hc_test_conpty_generate_123456789")
        request = suite.root / "conpty-image.json"
        request.write_text(json.dumps({"model": "seedream-5-0-lite-260128", "prompt": "offline interruption", "size": "2K"}), encoding="utf-8")
        args = ["generate", "create", "--kind", "image", "--json", "@" + str(request), "--yes", "--attempt-id", "native-conpty-interrupt"]
        command = driver(suite, origin, args)
        with Console(command, env, str(suite.root)) as console:
            assert entered.wait(20), "Generation never reached the local HTTP acceptance barrier"
            console.send("\x03")
            timer = threading.Timer(.5, release.set); timer.start()
            try:
                assert console.finish(timeout=30) == 130
            finally:
                release.set(); timer.join()
            assert "Traceback" not in console.output
            assert "do not retry automatically" in console.output
        attempts = json.loads((suite.config / "attempts.json").read_text(encoding="utf-8"))
        assert attempts["native-conpty-interrupt"]["state"] == "unknown"
        submitted = [row for row in rows if row[0] == "POST" and "freeze-credit" not in row[1]]
        assert len(submitted) == 1
        repeated = suite.run(command, env=env, expected=None)
        assert repeated.returncode != 0
        assert len([row for row in rows if row[0] == "POST" and "freeze-credit" not in row[1]]) == 1
    return {"exitCode": 130, "submissionCount": 1, "attemptState": "unknown", "repeatRejected": True}


def installer_summary(suite):
    prefix = suite.root / "console install"
    env = dict(suite.env, HOLYCRAB_INSTALL_PREFIX=str(prefix), HOLYCRAB_CONFIG_DIR=str(suite.root / "console clean config"))
    with Console([suite.ps51, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(suite.source / "install.ps1")], env, str(suite.root)) as console:
        assert console.finish(timeout=100) == 0
        output = console.output
        for i in range(1, 6):
            assert output.count(f"[{i}/5]") == 1, output
        assert output.count("HolyCrab 0.6.0 installed") == 1
        assert "Next: connect your account" in output
        assert "For Agents:" not in output and "agentInstruction" not in output
    program = prefix / "lib/holycrab/holycrab_cli.py"
    suite.run([sys.executable, str(program), "uninstall", "--yes"], env=env)
    suite.wait_missing(program)
    return {"ConPTY": True, "progressPhases": 5, "summaryCount": 1}


def interrupt_installer(suite):
    # A shim at the process boundary pauses actual installed doctor execution.
    # No installer rollback code or managed file is mocked/replaced.
    import hashlib
    before = {p.relative_to(suite.prefix).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in suite.prefix.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    fake = suite.root / "interrupt-python"; fake.mkdir(exist_ok=True)
    shim = fake / "pause.py"
    shim.write_text("import os,sys,time\n"
                    "if 'doctor' in sys.argv:\n print('Native installer interrupt barrier',flush=True);time.sleep(30)\n"
                    "os.execv(sys.executable,[sys.executable,*sys.argv[1:]])\n", encoding="utf-8")
    python = str(Path(sys.executable)).replace("%", "%%")
    shim_path = str(shim).replace("%", "%%")
    (fake / "python.cmd").write_text('@echo off\r\nsetlocal DisableDelayedExpansion\r\n"' + python + '" "' + shim_path + '" %*\r\n', encoding="utf-8")
    env = dict(suite.env, PATH=str(fake) + os.pathsep + suite.env["PATH"])
    with Console([suite.ps51, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(suite.source / "install.ps1")], env, str(suite.root)) as console:
        console.until("Native installer interrupt barrier", timeout=100)
        console.send("\x03")
        code = console.finish(timeout=40)
        assert code != 0, console.output
        assert "HolyCrab 0.6.0 installed" not in console.output
    after = {p.relative_to(suite.prefix).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in suite.prefix.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    assert after == before, "Interrupted installer did not restore previous runtime/launcher exactly"
    assert suite.cli_run("--version").stdout.strip() == "holycrab 0.6.0"
    return {"ConPTY": True, "exitCode": code, "previousBytesRestored": True}


def special_arguments(suite):
    from run_checks import NETWORK_GUARD
    with local_api() as (origin, rows, _, _):
        guard = suite.root / "argv-loopback-guard"
        guard.mkdir()
        (guard / "sitecustomize.py").write_text(NETWORK_GUARD +
            "\nimport sys\n" + f"sys.path.insert(0,{str(suite.cli.parent)!r})\n" +
            "from holycrab_runtime import settings\n" + f"settings.DEFAULT_BASE_URL={origin!r}\n", encoding="utf-8")
        marker = suite.root / "unexpected-command.marker"
        prompt = '中文 🦀 literal %PATH% !TOKEN! & echo injected > ' + str(marker) + ' $(whoami) `whoami`'
        request = suite.root / "引用 素材 🦀 (x)&%PATH%!TOKEN!.json"
        request.write_text(json.dumps({"model": "seedream-5-0-lite-260128", "prompt": prompt, "size": "2K"}, ensure_ascii=False), encoding="utf-8")
        # The caller disables expansion before inserting literal values from
        # environment variables. This does not promise to recover text that an
        # outer shell has already expanded. The installed launcher still has to
        # preserve the received argv without expanding it a second time.
        batch = suite.root / "invoke-literal-arguments.cmd"
        batch.write_text('@echo off\r\nsetlocal DisableDelayedExpansion\r\n"%HC_TEST_LAUNCHER%" generate estimate --kind image --json "%HC_TEST_REQUEST%"\r\n', encoding="ascii")
        env = dict(suite.env, PYTHONPATH=str(guard), HOLYCRAB_TEST_LOOPBACK="1",
                   HOLYCRAB_API_KEY="hc_test_literal_argv_123456789", HC_TEST_LAUNCHER=str(suite.launcher),
                   HC_TEST_REQUEST="@" + str(request), HC_TEST_DRIVER=str(batch), TOKEN="must-not-expand")
        for shell in (suite.ps51, suite.ps7):
            result = suite.ps("& $env:HC_TEST_DRIVER; exit $LASTEXITCODE", shell=shell, env=env)
            assert result.returncode == 0
        suite.run([os.environ["ComSpec"], "/d", "/v:on", "/c", '"%HC_TEST_DRIVER%"'], env=env)
        assert len(rows) == 3, rows
        assert all(row[0] == "POST" and "freeze-credit" in row[1] and row[2]["prompt"] == prompt for row in rows), rows
        assert not marker.exists(), "Prompt or path text was executed as a shell command"
    return {"shells": ["PowerShell5.1", "PowerShell7", "CMD-delayed-expansion-on"],
            "literalRequestPath": request.name, "payloadsEqual": True, "unwantedCommandExecuted": False,
            "callerQuoting": "setlocal DisableDelayedExpansion; quoted environment values; @request file"}


def run_cases(suite):
    for identifier, fn in (
        ("win.argv.shells", special_arguments),
        ("console.hidden-input", hidden_verification),
        ("console.ctrlc-wait", interrupt_wait),
        ("console.ctrlc-generation", interrupt_generation),
        ("console.install-summary", installer_summary),
        ("install.interrupted-rollback", interrupt_installer),
    ):
        suite.case(identifier, lambda fn=fn: fn(suite))
