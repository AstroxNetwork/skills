#!/usr/bin/env python3
"""Native Windows acceptance run under an ordinary ephemeral user.

Invoked by test_windows_adversarial.ps1. Requires actual baseline sources prepared
from the pinned Git object, and emits a case-by-case report even on failure.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

if not __debug__:
    raise RuntimeError('Native acceptance must run with Python assertions enabled')

from windows_console import Console
from windows_mcp_smoke import run_smoke

BASELINE_TAG_OBJECT = "52f697e35ad2689791423cb10eae5c8259e4c943"
BASELINE_COMMIT = "48126baa08521961de47d48bd2134c44a7e8cba2"


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def snapshot(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file() and "__pycache__" not in path.parts}


@contextmanager
def read_lock(path):
    from ctypes import wintypes as W
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
    kernel.CreateFileW.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        kernel.CloseHandle(handle)


class Suite:
    def __init__(self, args):
        self.args = args
        self.root = Path(args.root).resolve()
        self.source = Path(args.source).resolve()
        self.version = json.loads((self.source / "runtime-files.json").read_text(encoding="utf-8"))["version"]
        self.baseline = Path(args.baseline).resolve()
        self.prefix = self.root / "ordinary prefix"
        self.config = self.root / "private config"
        stamp = json.loads(Path(args.source_stamp).read_text(encoding="utf-8"))
        sys.path.insert(0, str(self.source / "tools"))
        from run_checks import source_fingerprint
        fingerprint = source_fingerprint(self.source)
        assert fingerprint == stamp["sourceFingerprint"], "Copied candidate differs from the source fingerprint"
        expected_cases = json.loads((self.source / "tests/windows_native_cases.json").read_text(encoding="utf-8"))
        self.report = {"commit": stamp["commit"], "sourceFingerprint": fingerprint,
                       "expectedCaseIds": expected_cases,
                       "schemaVersion": 1, "suite": "windows-adversarial", "cases": [],
                       "baselineTagObject": BASELINE_TAG_OBJECT, "baselineCommit": BASELINE_COMMIT, "environment": {
                           "os": platform.platform(), "machine": platform.machine(),
                           "python": sys.version, "executable": sys.executable,
                           "pythonPlatform": sysconfig.get_platform(), "pointerBits": ctypes.sizeof(ctypes.c_void_p) * 8,
                           "windowsEdition": platform.win32_edition(),
                           "networkIsolation": stamp.get("networkIsolation"),
                           "shells": {}, "username": os.environ.get("USERNAME"),
                           "profile": os.environ.get("USERPROFILE"),
                           "administrator": bool(ctypes.windll.shell32.IsUserAnAdmin()),
                           "installerSha256": snapshot(self.source).get("install.ps1")}}
        self.env = dict(os.environ, HOLYCRAB_INSTALL_SOURCE_DIR=str(self.source),
                        HOLYCRAB_INSTALL_PREFIX=str(self.prefix), HOLYCRAB_CONFIG_DIR=str(self.config),
                        HOLYCRAB_INSTALL_AGENTS="none", HOLYCRAB_INSTALL_MCP="0",
                        HOLYCRAB_NO_UPDATE_CHECK="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        self.env = {key: value for key, value in self.env.items() if key.upper() != "PSMODULEPATH"}
        for key in ("HOLYCRAB_API_KEY", "HOLYCRAB_API_BASE", "HOLYCRAB_BASE_URL", "HOLYCRAB_INSTALL_REF"):
            self.env.pop(key, None)
        self.ps51 = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
        self.ps7 = shutil.which("pwsh")
        if not self.ps7:
            self.ps7 = str(Path(os.environ["ProgramFiles"]) / "PowerShell/7/pwsh.exe")
        self.launcher = self.prefix / "bin/holycrab.cmd"
        self.cli = self.prefix / "lib/holycrab/holycrab_cli.py"

    def case(self, identifier, fn):
        start = time.monotonic()
        row = {"id": identifier}
        try:
            detail = fn()
            row.update(status="passed", detail=detail)
        except BaseException as exc:
            row.update(status="failed", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
        row["seconds"] = round(time.monotonic() - start, 3)
        self.report["cases"].append(row)
        print(json.dumps({"id": identifier, "status": row["status"]}), flush=True)
        if row['status'] == 'failed':
            print(row['traceback'], flush=True)
        self.write_report()

    def write_report(self):
        self.report["ok"] = bool(self.report["cases"]) and all(row["status"] == "passed" for row in self.report["cases"])
        Path(self.args.report).write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")

    def run(self, command, *, expected=0, env=None, input=None, timeout=100, descendant_timeout=20):
        from stage_process import WindowsJob
        serialized = command if isinstance(command, str) else [str(part) for part in command]
        actual = [sys.executable, str(self.source / "tools/stage_process.py"),
                  json.dumps(serialized), "--forward-stdin"]
        process = subprocess.Popen(actual, env=env or self.env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   encoding="utf-8", errors="replace", cwd=self.root)
        job = None
        try:
            # The wrapper cannot launch its first child before assignment.
            job = WindowsJob(process)
            try:
                stdout, stderr = process.communicate("G" + (input or ""), timeout=timeout)
            except subprocess.TimeoutExpired as error:
                job.terminate()
                stdout, stderr = process.communicate(timeout=5)
                if not job.wait_empty(timeout=5):
                    raise AssertionError("Timed-out command left descendant processes after termination") from error
                raise subprocess.TimeoutExpired(command, timeout, stdout, stderr) from error
            if not job.wait_empty(timeout=descendant_timeout):
                count = job.active()
                job.terminate()
                if not job.wait_empty(timeout=5):
                    raise AssertionError("Leaked descendants could not be terminated")
                raise RuntimeError(f"Command exited but left {count} descendant processes; the entire job was terminated")
            result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            if expected is not None and result.returncode != expected:
                raise AssertionError(f"exit {result.returncode}, expected {expected}: {result.stdout[-3000:]}\n{result.stderr[-3000:]}")
            return result
        finally:
            if job:
                job.close()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)

    def ps(self, code, *, shell=None, **kwargs):
        encoded = base64.b64encode(("$ErrorActionPreference='Stop'; " + code).encode("utf-16-le")).decode("ascii")
        return self.run([shell or self.ps51, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                         "-EncodedCommand", encoded], **kwargs)

    def cmd(self, expression, *, delayed=False, **kwargs):
        # cmd.exe parses /c itself, not with the CRT rules list2cmdline uses.
        # Pass this as an exact Windows command line; callers supply cmd syntax.
        command = f'"{os.environ["ComSpec"]}" /d /v:{"on" if delayed else "off"} /s /c "{expression}"'
        return self.run(command, **kwargs)

    def install(self, *, source=None, prefix=None, shell=None, expected=0, env=None):
        target = source or self.source
        install_env = dict(env or self.env, HOLYCRAB_INSTALL_SOURCE_DIR=str(target))
        if prefix is not None:
            install_env["HOLYCRAB_INSTALL_PREFIX"] = str(prefix)
        return self.ps("& " + ps_quote(target / "install.ps1"), shell=shell, env=install_env, expected=expected)

    def cli_run(self, *args, **kwargs):
        return self.run([sys.executable, "-X", "utf8", str(self.cli), *args], **kwargs)

    def environment(self):
        assert self.report["environment"]["administrator"] is False, "Acceptance must use a non-admin token"
        actual = self.ps("[Security.Principal.WindowsIdentity]::GetCurrent().Name; $HOME; $PSVersionTable.PSVersion.ToString()").stdout.splitlines()
        assert actual[0].lower().endswith("\\" + self.args.username.lower()), actual
        assert Path(actual[1]).name.lower() == self.args.username.lower(), actual
        assert Path(os.environ["USERPROFILE"]).name.lower() == self.args.username.lower()
        for label, shell in (("WindowsPowerShell", self.ps51), ("PowerShell", self.ps7)):
            result = self.ps("$PSVersionTable.PSVersion.ToString()", shell=shell)
            self.report["environment"]["shells"][label] = result.stdout.strip()
        assert self.report["environment"]["shells"]["WindowsPowerShell"].startswith("5.1.")
        assert self.report["environment"]["shells"]["PowerShell"].startswith("7.")
        probe = self.root / "console_probe.py"
        probe.write_text('import ctypes,json,sys\nfrom ctypes import wintypes as W\n'
                         'k=ctypes.WinDLL("kernel32",use_last_error=True)\n'
                         'k.GetStdHandle.argtypes=[W.DWORD];k.GetStdHandle.restype=W.HANDLE\n'
                         'k.GetConsoleMode.argtypes=[W.HANDLE,ctypes.POINTER(W.DWORD)];k.GetConsoleMode.restype=W.BOOL\n'
                         'modes=[]\n'
                         'for number in (-10,-11,-12):\n'
                         ' mode=W.DWORD();assert k.GetConsoleMode(k.GetStdHandle(number),ctypes.byref(mode));modes.append(mode.value)\n'
                         'assert all(stream.isatty() for stream in (sys.stdin,sys.stdout,sys.stderr))\n'
                         'print("Native console stdout",flush=True)\n'
                         'print("Native console stderr",file=sys.stderr,flush=True)\n', encoding="utf-8")
        with Console([sys.executable, str(probe)], self.env, str(self.root)) as console:
            assert console.finish() == 0, console.output
            assert "Native console stdout" in console.output and "Native console stderr" in console.output, console.output
        self.report["environment"]["console"] = {"stdioIsatty": True, "allWin32ConsoleModes": True,
                                                  "stdoutAndStderrCaptured": True}
        blocked = self.run([sys.executable, "-c", "import socket; socket.create_connection(('holycrab-test.invalid',443),1)"], expected=None)
        assert blocked.returncode != 0 and "Offline validation blocked" in blocked.stderr
        assert self.report["environment"]["networkIsolation"]["firewallEnabled"] is True
        return {"identity": actual[0], "home": actual[1], "nonLoopbackAuditBlocked": True}

    def existing_native(self):
        evidence = self.root / "existing-native.json"
        env = dict(self.env, RUNNER_TEMP=str(self.root))
        env.pop("HOLYCRAB_TEST_INSTALL_REF", None)
        self.ps("& " + ps_quote(self.source / "tests/test_windows_installer.ps1")
                + " -EvidenceReport " + ps_quote(evidence), env=env, timeout=400)
        rows = json.loads(evidence.read_text(encoding="utf-8"))
        assert len(rows) == 7 and len({row["id"] for row in rows}) == 7
        self.report["cases"].extend(rows)
        return {"nativeCases": [row["id"] for row in rows]}

    def upgrade(self):
        assert json.loads((self.baseline / ".holycrab-baseline.json").read_text(encoding="ascii")) == {
            "tag": "v0.5.0", "tagObject": BASELINE_TAG_OBJECT, "commit": BASELINE_COMMIT}
        self.install(source=self.baseline, env=dict(self.env, HOLYCRAB_INSTALL_AGENTS="codex"))
        assert self.cli_run("--version").stdout.strip() == "holycrab 0.5.0"
        key = "hc_test_windows_actual_baseline_123456789"
        saved = self.cli_run("setup", "--stdin", "--no-verify", input=key + "\n")
        assert key not in saved.stdout + saved.stderr
        before = (self.config / "config.json").read_bytes()
        assert key.encode() not in before
        note = self.prefix / "lib/holycrab/user-note.txt"
        note.write_text("keep user contents", encoding="utf-8")
        skill = Path(os.environ["USERPROFILE"]) / ".agents/skills/holycrab"
        (skill / "user-note.txt").write_text("keep skill note", encoding="utf-8")
        self.install(env=dict(self.env, HOLYCRAB_INSTALL_AGENTS="", HOLYCRAB_INSTALL_MCP=""))
        assert self.cli_run("--version").stdout.strip() == "holycrab " + self.version
        assert (self.config / "config.json").read_bytes() == before
        assert note.read_text() == "keep user contents"
        assert (skill / "user-note.txt").read_text() == "keep skill note"
        assert json.loads(self.cli_run("doctor", "--json").stdout)["runtimeOk"] is True
        self.credential_bytes = before
        self.report["cases"].append({"id": "upgrade.real-v050", "status": "passed", "driver": "win.upgrade.real_v050"})
        return {"oldVersion": "0.5.0", "newVersion": self.version, "credentialUnchanged": True,
                "baselineCommit": BASELINE_COMMIT, "baselineTagObject": BASELINE_TAG_OBJECT, "userFilesPreserved": True}

    def shells(self):
        for shell in (self.ps51, self.ps7):
            self.install(shell=shell)
            result = self.ps("& " + ps_quote(self.launcher) + " --version; exit $LASTEXITCODE", shell=shell)
            assert result.stdout.strip() == "holycrab " + self.version
        result = self.cmd('"%HOLYCRAB_TEST_LAUNCHER%" --version', delayed=True,
                          env=dict(self.env, HOLYCRAB_TEST_LAUNCHER=str(self.launcher)))
        assert result.stdout.strip() == "holycrab " + self.version
        launch_text = self.launcher.read_text(encoding="utf-8")
        invocation = next(line for line in launch_text.splitlines() if 'holycrab_cli.py' in line and '%*' in line)
        match = re.match(r'^"([^"]+)"', invocation)
        assert match, "Cannot identify the installed interpreter from its actual launcher"
        selected = Path(match[1].replace('%%', '%')).resolve()
        assert os.path.normcase(str(selected)) == os.path.normcase(str(Path(sys.executable).resolve())), (selected, sys.executable)
        identity = self.run([str(selected), "-c", "import json,sys,sysconfig;print(json.dumps({'version':list(sys.version_info[:3]),'platform':sysconfig.get_platform()}))"])
        actual_python = json.loads(identity.stdout)
        assert actual_python["version"] == list(sys.version_info[:3])
        assert actual_python["platform"] == sysconfig.get_platform()
        self.report["environment"]["installerPython"] = {"executable": str(selected), **actual_python}
        # Copy the actual generated launcher byte-for-byte. These small Python
        # programs test launcher exit-code ownership, not the uninstall flow.
        probe_prefix = self.root / "launcher exit contract"
        probe_launcher = probe_prefix / "bin/holycrab.cmd"
        probe_cli = probe_prefix / "lib/holycrab/holycrab_cli.py"
        probe_launcher.parent.mkdir(parents=True)
        probe_cli.parent.mkdir(parents=True)
        shutil.copyfile(self.launcher, probe_launcher)
        probe_env = dict(self.env, HOLYCRAB_TEST_LAUNCHER=str(probe_launcher))
        for exit_code in (37, 130):
            probe_cli.write_text("import pathlib,sys\npathlib.Path(__file__).unlink()\n"
                                 + f"raise SystemExit({exit_code})\n", encoding="utf-8")
            self.cmd('"%HOLYCRAB_TEST_LAUNCHER%"', env=probe_env, expected=exit_code)
            assert not probe_cli.exists(), "Exit-code fixture did not remove its entrypoint"
            assert probe_launcher.read_bytes() == self.launcher.read_bytes(), "Launcher removed itself after a failed child"
        missing = self.cmd('"%HOLYCRAB_TEST_LAUNCHER%" --version', env=probe_env, expected=2)
        assert "can't open file" in missing.stderr
        assert probe_launcher.read_bytes() == self.launcher.read_bytes(), "Ordinary --version deleted the broken installation's launcher"
        return {"PowerShell5": True, "PowerShell7": True, "cmdDelayedExpansion": True,
                "actualInstallerPythonMatchesMatrix": True,
                "launcherContractExitCodes": [37, 130, 2], "missingEntryPreservesLauncher": True}

    def ps7_python_ps51(self):
        """Exercise the real intermediary process that bypasses PS7's PS5 path fix."""
        prefix = self.root / "PS7 inherited modules"
        proof = self.root / "ps7-inheritance-proof.json"
        intermediary = self.root / "ps7_intermediary.py"
        intermediary.write_text(r'''import json, os, pathlib, subprocess, sys
source, ps51, prefix, proof = sys.argv[1:]
expected_modules = os.environ['HOLYCRAB_TEST_PS7_MODULES']
inherited = next(value for key, value in os.environ.items() if key.upper() == 'PSMODULEPATH')
normalize = lambda value: os.path.normcase(os.path.normpath(value))
assert normalize(expected_modules) in {normalize(value) for value in inherited.split(os.pathsep)}
def run(command):
    # Deliberately pass the inherited PS7 environment unchanged. The installer
    # and cleanup implementation must handle the real user launch chain.
    result = subprocess.run(command, capture_output=True, encoding='utf-8', errors='replace')
    if result.returncode:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    return result.stdout
run([ps51, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
     '-File', str(pathlib.Path(source) / 'install.ps1')])
library = pathlib.Path(prefix) / 'lib/holycrab'
cli = library / 'holycrab_cli.py'
assert run([sys.executable, str(cli), '--version']).strip() == 'holycrab ' + json.loads((pathlib.Path(source) / 'runtime-files.json').read_text(encoding='utf-8'))['version']
run([sys.executable, str(pathlib.Path(source) / 'tools/native_install_checks.py'), 'runtime', str(library)])
run([sys.executable, str(cli), 'uninstall', '--yes'])
pathlib.Path(proof).write_text(json.dumps({'inheritedPS7ModulePath': inherited,
    'runtimeHashesVerified': True, 'uninstallExitCode': 0}), encoding='utf-8')
''', encoding="utf-8")
        env = dict(self.env, HOLYCRAB_INSTALL_PREFIX=str(prefix),
                   HOLYCRAB_CONFIG_DIR=str(self.root / "PS7 inherited modules config"))
        command = ("$env:HOLYCRAB_TEST_PS7_MODULES=Join-Path $PSHOME 'Modules'; & "
                   + " ".join(ps_quote(value) for value in
                              (sys.executable, intermediary, self.source, self.ps51, prefix, proof))
                   + "; exit $LASTEXITCODE")
        self.ps(command, shell=self.ps7, env=env, timeout=180)
        self.wait_missing(prefix / "bin/holycrab.cmd")
        self.wait_missing(prefix / "lib/holycrab/holycrab_cli.py")
        detail = json.loads(proof.read_text(encoding="utf-8"))
        detail["deferredCleanupObserved"] = True
        return detail

    def special_paths(self):
        prefix = self.root / "中文 用户 🦀 (safe)&literal"
        self.install(prefix=prefix)
        launcher = prefix / "bin/holycrab.cmd"
        for shell in (self.ps51, self.ps7):
            result = self.ps("& " + ps_quote(launcher) + " --version; exit $LASTEXITCODE", shell=shell)
            assert result.stdout.strip() == "holycrab " + self.version, result.stdout
        # cmd receives the path through a quoted environment variable.
        cmd_env = dict(self.env, HOLYCRAB_TEST_LAUNCHER=str(launcher))
        result = self.cmd('"%HOLYCRAB_TEST_LAUNCHER%" --version', env=cmd_env)
        assert result.stdout.strip() == "holycrab " + self.version, result.stdout
        code = self.ps("& " + ps_quote(launcher) + " unknown-command; exit $LASTEXITCODE", expected=2)
        assert "invalid choice" in code.stderr
        self.run([sys.executable, str(prefix / "lib/holycrab/holycrab_cli.py"), "uninstall", "--yes"])
        self.wait_missing(prefix / "lib/holycrab/holycrab_cli.py")
        return {"unicode": True, "spaces": True, "metacharacters": "()&", "exitCode": 2}

    def long_paths(self):
        prefix = self.root
        while len(str(prefix)) < 280:
            prefix = prefix / "long-path-component"
        result = self.install(prefix=prefix, expected=None)
        if result.returncode == 0:
            program = prefix / "lib/holycrab/holycrab_cli.py"
            assert self.run([sys.executable, str(program), "--version"]).stdout.strip() == "holycrab " + self.version
            self.run([sys.executable, str(program), "uninstall", "--yes"])
            self.wait_missing(program)
            return {"prefixLength": len(str(prefix)), "supported": True}
        assert not (prefix / "lib/holycrab/holycrab_cli.py").exists(), "Long-path rejection left a half-installed runtime"
        assert any(word in (result.stdout + result.stderr).lower() for word in ("path", "directory", "filename"))
        return {"prefixLength": len(str(prefix)), "supported": False, "cleanFailure": True}

    def rejected_paths(self):
        candidates = [self.root / "CON", self.root / "trailing.", self.root / ("long" * 65),
                      Path(r"\\localhost\C$\holycrab-must-not-write")]
        for prefix in candidates:
            result = self.install(prefix=prefix, expected=None)
            assert result.returncode != 0, f"Unsafe/unsupported prefix unexpectedly succeeded: {prefix}"
            assert "install" in (result.stderr + result.stdout).lower() or "path" in (result.stderr + result.stdout).lower()
        for name in ("literal%PATH%", "literal!TOKEN!"):
            prefix = self.root / name
            assert not prefix.exists()
            result = self.install(prefix=prefix, expected=None)
            assert result.returncode != 0 and "CMD launcher" in result.stderr, result.stderr
            assert "HOLYCRAB_INSTALL_PREFIX" in result.stderr
            assert not prefix.exists(), "Unsupported launcher path was rejected only after writing files"
        target = self.root / "junction-target"
        target.mkdir(exist_ok=True)
        sentinel = target / "keep.txt"; sentinel.write_text("unchanged")
        junction = self.root / "junction-prefix"
        self.run([os.environ["ComSpec"], "/d", "/c", "mklink", "/J", str(junction), str(target)])
        try:
            result = self.install(prefix=junction, expected=None)
            assert result.returncode != 0, "Installer accepted a junction prefix"
            assert list(target.iterdir()) == [sentinel]
        finally:
            junction.rmdir()
        return {"reserved": True, "trailingDot": True, "tooLong": True, "uncDenied": True, "junctionRejected": True,
                "cmdExpansionPathsRejectedBeforeWrites": ["%", "!"]}

    def immutable_ref(self):
        prefix = self.root / "immutable-ref"
        log = self.root / "immutable-downloads.jsonl"
        env = dict(self.env, HOLYCRAB_INSTALL_REF="a" * 40,
                   HOLYCRAB_INSTALL_PREFIX=str(prefix), HOLYCRAB_TEST_SOURCE=str(self.source),
                   HOLYCRAB_TEST_DOWNLOAD_LOG=str(log))
        env.pop("HOLYCRAB_INSTALL_SOURCE_DIR", None)
        code = r"""
function Invoke-WebRequest {
    param([switch]$UseBasicParsing,[string]$Uri,[string]$OutFile)
    $expected='https://raw.githubusercontent.com/AstroxNetwork/skills/' + $env:HOLYCRAB_INSTALL_REF + '/'
    if (-not $Uri.StartsWith($expected)) { throw 'Installer used a mutable/unexpected download reference' }
    $relative=$Uri.Substring($expected.Length)
    Add-Content -LiteralPath $env:HOLYCRAB_TEST_DOWNLOAD_LOG -Value $Uri -Encoding UTF8
    Copy-Item -LiteralPath (Join-Path $env:HOLYCRAB_TEST_SOURCE $relative) -Destination $OutFile
}
""" + "& " + ps_quote(self.source / "install.ps1")
        self.ps(code, env=env)
        urls = log.read_text(encoding="utf-8-sig").splitlines()
        assert len(urls) >= 20 and all("/" + "a" * 40 + "/" in url for url in urls)
        program = prefix / "lib/holycrab/holycrab_cli.py"
        assert self.run([sys.executable, str(program), "--version"], env=env).stdout.strip() == "holycrab " + self.version
        self.run([sys.executable, str(program), "uninstall", "--yes"], env=env)
        self.wait_missing(program)
        return {"immutableRef": "a" * 40, "verifiedDownloads": len(urls), "version": self.version}

    def invalid_ref(self):
        prefix = self.root / "invalid-ref"
        for ref in ("main", "../main", "v0.6.0-rc1", "a" * 39, "a?token=not-real"):
            result = self.install(prefix=prefix, env=dict(self.env, HOLYCRAB_INSTALL_REF=ref), expected=None)
            assert result.returncode != 0 and "HOLYCRAB_INSTALL_REF" in result.stderr
            assert not prefix.exists(), "Invalid reference wrote installation files"
        return {"invalidReferencesRejected": 5, "beforeWrites": True}

    def tampered_hash(self):
        damaged = self.root / "tampered-candidate"
        shutil.copytree(self.source, damaged, ignore=shutil.ignore_patterns(".git", "build", "__pycache__"))
        program = damaged / "holycrab/scripts/holycrab_cli.py"
        program.write_bytes(program.read_bytes() + b"\n# adversarial tampering\n")
        prefix = self.root / "tampered-install"
        result = self.install(source=damaged, prefix=prefix, expected=None)
        assert result.returncode != 0 and "SHA-256 verification failed" in result.stderr
        assert not prefix.exists(), "Hash failure left an installation"
        return {"corruptedDownloadRejected": True, "beforeWrites": True}

    def python_discovery(self):
        # Extract only the PowerShell function via its AST, then exercise real
        # executable candidates. This avoids modifying a production source file.
        function_code = (
            "$ast=[Management.Automation.Language.Parser]::ParseFile(" + ps_quote(self.source / "install.ps1") + ",[ref]$null,[ref]$null);"
            "$fn=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Find-HolyCrabPython'},$true);"
            "Invoke-Expression $fn.Extent.Text; $found=Find-HolyCrabPython; $found|ConvertTo-Json -Compress"
        )
        fake = self.root / "python-discovery"; fake.mkdir(exist_ok=True)
        candidates = ("python.cmd", "python3.cmd", "py.cmd")
        env = dict(self.env, PATH=str(fake))
        missing = self.ps(function_code, env=env, expected=None)
        assert missing.returncode != 0 and "Python 3.10" in missing.stderr
        for name in candidates:
            (fake / name).write_text("@echo off\r\nexit /b 1\r\n", encoding="ascii")
        old = self.ps(function_code, env=env, expected=None)
        assert old.returncode != 0 and "Python 3.10" in old.stderr
        path = str(Path(sys.executable)).replace("%", "%%")
        (fake / "python3.cmd").write_text('@echo off\r\nsetlocal DisableDelayedExpansion\r\n"' + path + '" %*\r\n', encoding="utf-8")
        selected = json.loads(self.ps(function_code, env=env).stdout)
        assert selected["Executable"].lower().endswith("python3.cmd"), selected
        return {"missingRejected": True, "oldRejected": True, "usableFallbackSelected": True}

    def credentials(self):
        stored = json.loads((self.config / "config.json").read_text(encoding="utf-8"))
        assert "apiKey" not in stored and isinstance(stored.get("apiKeyDpapi"), str)
        status = json.loads(self.cli_run("doctor", "--json").stdout)
        assert status["checks"]["config"]["keyConfigured"] is True
        return {"encrypted": True, "ordinaryUserCanRead": True}

    def credential_recovery(self, *, foreign=False):
        path = self.config / "config.json"
        original = path.read_bytes()
        payload = Path(self.args.foreign_config).read_bytes() if foreign else b'{"apiKeyDpapi":"!!!damaged!!!"}'
        try:
            path.write_bytes(payload)
            failed = self.cli_run("doctor", "--json", expected=None)
            assert failed.returncode != 0, "Damaged/foreign ciphertext was reported healthy"
            replacement = "hc_test_repaired_windows_123456789"
            saved = self.cli_run("setup", "--stdin", "--no-verify", input=replacement + "\n")
            assert replacement not in saved.stdout + saved.stderr
            assert replacement.encode() not in path.read_bytes()
            assert json.loads(self.cli_run("doctor", "--json").stdout)["checks"]["config"]["keyConfigured"] is True
        finally:
            path.write_bytes(original)
        return {"foreignUser" if foreign else "damaged": True, "replacementSucceeded": True}

    def health_rollback(self):
        before = snapshot(self.prefix)
        broken = self.root / "broken-candidate"
        self.run([sys.executable, str(self.source / "tools/native_install_checks.py"), "broken-source", str(broken)])
        result = self.install(source=broken, expected=None)
        assert result.returncode != 0
        assert snapshot(self.prefix) == before, "Failed health verification changed previous installation bytes"
        assert self.cli_run("--version").stdout.strip() == "holycrab " + self.version
        return {"exactPreviousFilesRestored": True}

    def lock_rollback(self):
        before = snapshot(self.prefix)
        with read_lock(self.cli):
            result = self.install(expected=None)
            assert result.returncode != 0, "Installer ignored native write-denying file handle"
            assert snapshot(self.prefix) == before
        assert self.cli_run("--version").stdout.strip() == "holycrab " + self.version
        return {"nativeSharingLock": True, "previousInstallationUsable": True}

    def concurrency(self):
        lock = self.prefix / ".holycrab-install.lock"
        lock.mkdir()
        before = snapshot(self.prefix)
        try:
            result = self.install(expected=None)
            assert result.returncode != 0 and "Another lifecycle operation" in result.stderr
            assert snapshot(self.prefix) == before
        finally:
            lock.rmdir()
        # Existing MCP keeps serving when the same verified installation updates.
        process = subprocess.Popen([sys.executable, "-X", "utf8", str(self.cli), "mcp", "serve"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   encoding="utf-8", env=self.env)
        try:
            process.stdin.write('{"jsonrpc":"2.0","id":1,"method":"ping"}\n'); process.stdin.flush()
            # communicate after the reinstall supplies a second request and EOF.
            self.install()
            out, err = process.communicate('{"jsonrpc":"2.0","id":2,"method":"ping"}\n', timeout=20)
            assert process.returncode == 0, err
            assert [json.loads(line)["id"] for line in out.splitlines()] == [1, 2]
        finally:
            if process.poll() is None:
                process.kill(); process.communicate(timeout=5)
        return {"lifecycleLock": True, "activeMcpSurvivesUpdate": True}

    def console(self, kind):
        env = dict(self.env)
        if kind in {"hidden", "empty", "ctrl_c"}:
            args, prompt = ["setup", "--no-verify"], "Paste API Key (input hidden):"
        else:
            args, prompt = ["uninstall"], "[y/N]"
        with Console([sys.executable, "-X", "utf8", str(self.cli), *args], env, str(self.root)) as console:
            console.until(prompt)
            if kind == "hidden":
                key = "hc_test_console_hidden_123456789"
                console.send(key + "\r")
                assert console.finish() == 0
                assert key not in console.output, "Console echoed the hidden API Key"
                assert key.encode() not in (self.config / "config.json").read_bytes()
            elif kind == "empty":
                console.send("\r")
                assert console.finish() != 0
                assert "empty" in console.output.lower()
            elif kind == "ctrl_c":
                console.send("\x03")
                assert console.finish() == 130
            elif kind == "eof":
                console.send("\x1a\r")
                assert console.finish() == 130
                assert self.cli.exists()
            elif kind == "cancel":
                console.send("n\r")
                assert console.finish() == 2
                assert "cancelled" in console.output.lower() and self.cli.exists()
            else:
                console.send("y\r")
                assert console.finish() == 0
                self.wait_missing(self.cli)
        return {"ConPTY": True, "input": kind, "noChildProcesses": True}

    @staticmethod
    def wait_missing(path):
        deadline = time.monotonic() + 20
        while path.exists() and time.monotonic() < deadline:
            time.sleep(.1)
        assert not path.exists(), f"Deferred cleanup did not finish: {path}"

    def uninstall_user_files(self):
        # This is a real installed CLI through its CMD launcher. A native
        # sharing lock lets runtime removal succeed but makes the purge fail.
        failed_prefix = self.root / "failed purge prefix"
        failed_config = self.root / "failed purge config"
        failed_launcher = failed_prefix / "bin/holycrab.cmd"
        failed_cli = failed_prefix / "lib/holycrab/holycrab_cli.py"
        env = dict(self.env, HOLYCRAB_CONFIG_DIR=str(failed_config),
                   HOLYCRAB_TEST_LAUNCHER=str(failed_launcher))
        self.install(prefix=failed_prefix, env=env)
        failed_config.mkdir(exist_ok=True)
        locked_config = failed_config / "config.json"
        original_config = b'{"testState":"must remain while locked"}\n'
        locked_config.write_bytes(original_config)
        with read_lock(locked_config):
            failure = self.cmd('"%HOLYCRAB_TEST_LAUNCHER%" uninstall --purge --yes', env=env, expected=1)
            assert "local data purge was incomplete" in failure.stderr, failure.stderr
            self.wait_missing(failed_cli)
            self.wait_missing(failed_launcher)
            assert locked_config.read_bytes() == original_config

        self.install(env=dict(self.env, HOLYCRAB_INSTALL_AGENTS="codex"))
        skill_file = Path(os.environ["USERPROFILE"]) / ".agents/skills/holycrab/SKILL.md"
        skill_file.write_bytes(skill_file.read_bytes() + b"\nUser-edited guidance: keep this.\n")
        modified_skill = skill_file.read_bytes()
        note = self.prefix / "lib/holycrab/user-note.txt"
        assert note.read_text() == "keep user contents"
        shared_bin = self.prefix / "bin/user-program.cmd"
        shared_bin.write_text("@echo user program\n")
        unknown = self.config / "user-data.txt"; unknown.write_text("keep config note")
        result = self.cli_run("uninstall", "--yes", "--purge")
        assert "scheduled" in result.stdout
        self.wait_missing(self.cli)
        self.wait_missing(self.launcher)
        assert note.read_text() == "keep user contents"
        assert shared_bin.exists() and unknown.read_text() == "keep config note"
        assert not (self.config / "config.json").exists()
        skill = Path(os.environ["USERPROFILE"]) / ".agents/skills/holycrab/user-note.txt"
        assert skill.read_text() == "keep skill note"
        assert skill_file.read_bytes() == modified_skill
        path_after = self.ps("[Environment]::GetEnvironmentVariable('Path','User')").stdout.strip()
        assert str(self.prefix / "bin").casefold() in {part.rstrip('\\').casefold() for part in path_after.split(';')}
        for identifier in ("uninstall.purge-preserve-unknown", "uninstall.shared-path-preservation", "uninstall.preserve-modified-skill"):
            self.report["cases"].append({"id": identifier, "status": "passed", "driver": "win.uninstall.user_files"})
        return {"unknownFilesPreserved": True, "managedFilesGone": True, "asyncCompletionObserved": True,
                "modifiedSkillPreserved": True, "sharedUserPathPreserved": True,
                "lockedConfigPurgeExitCode": 1, "failedPurgeConfigPreserved": True}

    def execute(self):
        self.case("win.environment.non_admin", self.environment)
        from windows_process_tree import run_case as process_tree_case
        self.case("win.process_tree.cleanup", lambda: process_tree_case(self))
        self.case("win.installer.existing", self.existing_native)
        self.case("win.upgrade.real_v050", self.upgrade)
        self.case("win.shells.launchers", self.shells)
        self.case("win.shells.ps7_python_ps51", self.ps7_python_ps51)
        self.case("win.paths.special", self.special_paths)
        self.case("win.paths.rejected", self.rejected_paths)
        self.case("win.paths.long", self.long_paths)
        self.case("win.python.discovery", self.python_discovery)
        self.case("install.immutable-ref", self.immutable_ref)
        self.case("install.invalid-ref", self.invalid_ref)
        self.case("install.tampered-hash", self.tampered_hash)
        self.case("win.credentials.dpapi", self.credentials)
        self.case("win.credentials.corrupt_recovery", self.credential_recovery)
        self.case("win.credentials.foreign_recovery", lambda: self.credential_recovery(foreign=True))
        self.case("win.rollback.health", self.health_rollback)
        self.case("win.rollback.lock", self.lock_rollback)
        self.case("win.lifecycle.concurrent", self.concurrency)
        self.case("win.mcp.tools", lambda: run_smoke(str(self.cli)))
        from windows_console_flows import run_cases as console_cases
        console_cases(self)
        for kind in ("hidden", "empty", "ctrl_c", "eof", "cancel"):
            self.case("win.console." + kind, lambda kind=kind: self.console(kind))
        from windows_ownership_adversarial import run_cases as ownership_cases
        ownership_cases(self)
        self.case("win.uninstall.user_files", self.uninstall_user_files)
        # A fresh install permits explicit interactive acceptance after the purge.
        self.case("win.console.confirm", lambda: (self.install(), self.console("confirm"))[1])
        self.case("win.uninstall.delayed", lambda: {"programAbsent": not self.cli.exists(),
                  "launcherAbsent": not self.launcher.exists()} if not self.cli.exists() and not self.launcher.exists()
                  else (_ for _ in ()).throw(AssertionError("Deferred cleanup left managed entrypoints")))
        actual = [row["id"] for row in self.report["cases"]]
        if sorted(actual) != self.report["expectedCaseIds"] or len(actual) != len(set(actual)):
            self.report["cases"].append({"id": "native.case-inventory", "status": "failed",
                "missing": sorted(set(self.report["expectedCaseIds"]) - set(actual)),
                "unexpected": sorted(set(actual) - set(self.report["expectedCaseIds"]))})
        self.write_report()
        return 0 if self.report["ok"] else 1


def main():
    parser = argparse.ArgumentParser()
    for name in ("source", "baseline", "root", "report", "username", "foreign-config", "source-stamp"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Native Windows is required; emulation does not count as acceptance")
    return Suite(args).execute()


if __name__ == "__main__":
    raise SystemExit(main())
