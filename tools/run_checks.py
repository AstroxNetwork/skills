#!/usr/bin/env python3
"""Run reproducible offline checks; optional integrations require explicit local inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIX_ONLY_MODULES = {"test_installer", "test_uninstall"}
NETWORK_GUARD = '''import sys
def _holycrab_offline(event, args):
    if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo", "socket.sendto"}:
        raise RuntimeError("Offline validation blocked an unmocked network operation: " + event)
sys.addaudithook(_holycrab_offline)
'''


def source_fingerprint(root: Path = ROOT) -> dict:
    paths = {path for pattern in ("holycrab/**/*", "tools/*.py", "tests/**/*", ".github/workflows/*", "*.md", "docs/release-acceptance/*.json")
             for path in root.glob(pattern) if path.is_file() and "__pycache__" not in path.parts}
    paths.update(path for path in (root / ".gitattributes", root / "runtime-files.json", root / "install.sh", root / "install.ps1", root / "bin/holycrab",
                                  root / "README.md", root / "HolyCrab CLI 使用指南.md") if path.is_file())
    hashes = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}
    return {"sha256": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(), "files": hashes}


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def isolated_environment(directory: Path) -> dict[str, str]:
    """Never inherit user credentials, install settings, or Agent configuration."""
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("HOLYCRAB_", "CODEX_", "CLAUDE_"))
           and key not in {"PYTHONPATH", "PYTHONHOME", "PYTHONOPTIMIZE"}}
    home = directory / "home"
    home.mkdir(parents=True)
    guard = directory / "offline-guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(NETWORK_GUARD, encoding="utf-8")
    env.update({"HOME": str(home), "USERPROFILE": str(home),
                "PATH": str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", ""),
                "APPDATA": str(home / "AppData/Roaming"), "LOCALAPPDATA": str(home / "AppData/Local"),
                "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(home / ".cache"),
                "HOLYCRAB_CONFIG_DIR": str(home / ".config/holycrab"),
                "HOLYCRAB_NO_UPDATE_CHECK": "1", "PYTHONPATH": str(guard),
                "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def run_stage(name: str, command: list[str], *, directory: Path, env: dict[str, str],
              timeout: float, cwd: Path = ROOT) -> dict:
    log = directory / (name + ".log")
    started = time.monotonic()
    result = {"name": name, "command": command, "log": str(log), "status": "failed"}
    try:
        with log.open("w", encoding="utf-8") as output:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=output,
                                       stderr=subprocess.STDOUT, start_new_session=os.name != "nt")
            try:
                result["exitCode"] = process.wait(timeout=timeout)
                result["status"] = "passed" if result["exitCode"] == 0 else "failed"
            except subprocess.TimeoutExpired:
                result["status"] = "timeout"
                if os.name == "nt":
                    try:
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                       capture_output=True, timeout=10)
                    finally:
                        if process.poll() is None:
                            process.kill()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait(timeout=10)
                result["exitCode"] = process.returncode
                result["reason"] = f"Exceeded {timeout:g} second stage timeout"
    except OSError as error:
        result["status"] = "unavailable"
        result["reason"] = str(error)
    result["seconds"] = round(time.monotonic() - started, 3)
    return result


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def platform_suite(suite, system: str):
    selected = unittest.TestSuite()
    for test in flatten(suite):
        if system == "Windows" and any(part in UNIX_ONLY_MODULES for part in test.id().split(".")):
            def skip_unix():
                raise unittest.SkipTest("Unix shell installer integration; native PowerShell installer runs in Windows CI")
            replacement = unittest.FunctionTestCase(skip_unix)
            replacement.id = test.id
            selected.addTest(replacement)
        else:
            selected.addTest(test)
    return selected


def unit_worker(report: Path, test_directory: Path, names: list[str]) -> int:
    sys.path.insert(0, str(ROOT))
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(names) if names else loader.discover(str(test_directory), pattern="test_*.py")
    suite = platform_suite(suite, platform.system())
    discovered = [test.id() for test in flatten(suite)]
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    executed = result.testsRun - len(result.skipped)
    data = {"discovered": len(discovered), "testIds": discovered, "run": result.testsRun,
            "executed": executed,
            "failures": [{"test": test.id(), "traceback": trace} for test, trace in result.failures],
            "errors": [{"test": test.id(), "traceback": trace} for test, trace in result.errors],
            "skipped": [{"test": test.id(), "reason": reason} for test, reason in result.skipped],
            "expectedFailures": [test.id() for test, _ in result.expectedFailures],
            "unexpectedSuccesses": [test.id() for test in result.unexpectedSuccesses]}
    # Empty or entirely skipped discovery cannot count as verified behavior.
    data["status"] = "passed" if result.wasSuccessful() and executed > 0 else "failed"
    if data["status"] == "passed" and result.skipped:
        data["status"] = "passed_with_skips"
    write_json(report, data)
    return 0 if result.wasSuccessful() and executed > 0 else 1


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--report", type=Path, default=ROOT / "build/validation/report.json")
    value.add_argument("--timeout", type=float, default=600, help="Maximum seconds per check (default: 600)")
    value.add_argument("--website-root", type=Path, help="Read-only website checkout with the 50 localized CLI pages")
    value.add_argument("--generate-root", type=Path, help="Read-only Generate checkout for exact capability comparison")
    value.add_argument("--generate-ref", help="Read committed source at this ref instead of the backend worktree")
    value.add_argument("--baseline-source", type=Path, help="Previous CLI checkout for an additional isolated real-version upgrade/rollback check")
    value.add_argument("--qr-python", type=Path, help="Existing Python with tests/requirements-qr-test.txt installed")
    value.add_argument("--skill-validator", type=Path, help="Existing official quick_validate.py")
    value.add_argument("--skill-python", type=Path, help="Existing Python with PyYAML; defaults to this Python")
    value.add_argument("--unit-worker", type=Path, help=argparse.SUPPRESS)
    value.add_argument("--test-directory", type=Path, default=ROOT / "tests", help=argparse.SUPPRESS)
    value.add_argument("--test-name", action="append", default=[], help=argparse.SUPPRESS)
    return value


def main(argv=None) -> int:
    arguments = parser().parse_args(argv)
    if not math.isfinite(arguments.timeout) or arguments.timeout <= 0:
        parser().error("--timeout must be a finite positive number")
    if arguments.skill_python and not arguments.skill_validator:
        parser().error("--skill-python requires --skill-validator")
    if arguments.unit_worker:
        return unit_worker(arguments.unit_worker, arguments.test_directory, arguments.test_name)
    report = arguments.report.resolve()
    directory = report.parent
    directory.mkdir(parents=True, exist_ok=True)
    stages = []
    python = sys.executable
    unit_report = directory / "unit-results.json"
    unit_report.unlink(missing_ok=True)
    fingerprint = source_fingerprint()
    with tempfile.TemporaryDirectory(prefix="holycrab-offline-") as temporary:
        env = isolated_environment(Path(temporary))

        def check(name, command=None, reason=None, stage_env=None):
            stage = ({"name": name, "status": "skipped", "reason": reason} if command is None else
                     run_stage(name, command, directory=directory, env=stage_env or env, timeout=arguments.timeout))
            stages.append(stage)
            print(f"{stage['status'].upper():18} {name}", flush=True)
            if stage["status"] in {"failed", "timeout", "unavailable"}:
                if stage.get("reason"):
                    print(stage["reason"], flush=True)
                if stage.get("log") and Path(stage["log"]).is_file():
                    print(Path(stage["log"]).read_text(encoding="utf-8", errors="replace")[-12000:], flush=True)
            return stage

        unit = check("unit", [python, str(Path(__file__).resolve()), "--unit-worker", str(unit_report),
                              "--test-directory", str(arguments.test_directory)])
        if unit["status"] == "passed":
            try:
                details = json.loads(unit_report.read_text(encoding="utf-8"))
                if details["status"] not in {"passed", "passed_with_skips"} or details["executed"] < 1:
                    raise ValueError("Unit result did not contain successful executed tests")
                unit["status"] = details["status"]
                unit["tests"] = details
            except (OSError, ValueError, KeyError) as error:
                unit.update(status="failed", reason=f"Invalid unit result: {error}")
        elif unit_report.exists():
            unit["tests"] = json.loads(unit_report.read_text(encoding="utf-8"))
        if "tests" in unit:
            details = unit["tests"]
            print(f"Unit tests: {details['run']} run, {len(details['skipped'])} skipped, "
                  f"{len(details['failures'])} failures, {len(details['errors'])} errors", flush=True)
        check("installer-inventory", [python, "tools/refresh_installer_hashes.py", "--check"])
        check("capabilities", [python, "tools/validate_capabilities.py"])
        check("compile", [python, "-m", "compileall", "-q", "holycrab/scripts", "tools", "tests"])
        # sh -n accepts one script; an additional path would only become its argv[1].
        for name, script in (("shell-install", "install.sh"), ("shell-launcher", "bin/holycrab")):
            check(name, ["sh", "-n", script] if os.name != "nt" else None,
                  "Unix shell syntax is checked by Linux and macOS CI")
        check("whitespace", ["git", "diff", "--check", "HEAD"] if (ROOT / ".git").exists() else None,
              "Source archive has no .git metadata; patch whitespace must be checked in the Git checkout")
        if arguments.website_root:
            website_env = {**env, "HOLYCRAB_GUIDE_SOURCE_DIR": str(arguments.website_root.resolve())}
            check("website", [python, str(Path(__file__).resolve()), "--unit-worker", str(directory / "website-results.json"),
                              "--test-name", "tests.test_public_guidance.PublicGuidanceTests.test_all_five_language_website_executable_cli_examples"],
                  stage_env=website_env)
        else:
            check("website", reason="No --website-root provided; website examples were not verified")
        check("generate-contract", [python, "tools/validate_generate_contract.py", str(arguments.generate_root.resolve()), *(["--ref", arguments.generate_ref] if arguments.generate_ref else [])]
              if arguments.generate_root else None, "No --generate-root provided; current backend contract was not verified")
        check("baseline-upgrade", [python, "tests/local_lifecycle_smoke.py", "--baseline-source", str(arguments.baseline_source.resolve())]
              if arguments.baseline_source else None, "No --baseline-source provided; an actual previous release was not exercised")
        # Keep a virtualenv launcher path: resolving its symlink would discard its dependencies.
        check("qr", [str(arguments.qr_python.absolute()), "tools/validate_qr.py"] if arguments.qr_python else None,
              "No --qr-python provided; independent QR decoding was not run")
        check("skill", [str((arguments.skill_python or Path(python)).absolute()), str(arguments.skill_validator.resolve()), "holycrab"]
              if arguments.skill_validator else None, "No --skill-validator provided; official Skill validation was not run")
    failures = [stage for stage in stages if stage["status"] in {"failed", "timeout", "unavailable"}]
    skipped = any(stage["status"] in {"skipped", "passed_with_skips"} for stage in stages)
    if source_fingerprint() != fingerprint:
        stages.append({"name": "source-stability", "status": "failed", "reason": "Source files changed while checks were running; rerun on the final candidate"})
        failures.append(stages[-1])
    outcome = "failed" if failures else "passed_with_skips" if skipped else "passed"
    write_json(report, {"schemaVersion": 1, "scope": "offline-local-validation", "status": outcome,
                        "platform": platform.platform(), "python": sys.version.split()[0], "source": fingerprint, "checks": stages,
                        "limitations": ["Other operating systems require their native CI jobs.",
                                        "Native PowerShell installation/DPAPI/rollback is checked by the separate install-windows CI job, not this runner.",
                                        "No real authorization, paid generation, account access, upload, or production deployment was performed.",
                                        "This report does not approve a production release."]})
    print(f"Result: {outcome}; report: {report}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
