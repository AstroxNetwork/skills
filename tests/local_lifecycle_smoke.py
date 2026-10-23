#!/usr/bin/env python3
"""Offline install, failed-upgrade rollback and uninstall in a disposable home.

Never reads the user's credentials, installs into their home, or calls a service.
Pass --baseline-source to exercise an actual previous checkout; by default the
current candidate is installed twice. A failed assertion returns a nonzero code.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).resolve().parents[1]


def checked(command, env, *, expected=0):
    result = subprocess.run(command, env=env, text=True, encoding="utf-8",
                            capture_output=True, timeout=90)
    if (result.returncode == 0) != (expected == 0):
        raise RuntimeError(f"Unexpected exit {result.returncode}: {command[0]}\n{result.stdout}\n{result.stderr}")
    return result


def source_version(source, env):
    output = checked([sys.executable, str(source / "holycrab/scripts/holycrab_cli.py"), "--version"], env).stdout.strip()
    if not output.startswith("holycrab "):
        raise RuntimeError("Source did not return a CLI version")
    return output


def snapshot(directory):
    return {str(file.relative_to(directory)): hashlib.sha256(file.read_bytes()).hexdigest()
            for file in directory.rglob("*")
            if file.is_file() and "__pycache__" not in file.parts and not file.name.endswith(".pyc")}


def private_records(directory):
    # Health/update caches may legitimately change with installed bytes.
    disposable = {"health-state.json", "update-state.json", "update-state.lock"}
    return {name: digest for name, digest in snapshot(directory).items() if name not in disposable}


def run_lifecycle(source=SOURCE, baseline=None):
    if os.name == "nt":
        raise RuntimeError("Use tests/test_windows_installer.ps1 on native Windows")
    source, baseline = Path(source).resolve(), Path(baseline or source).resolve()
    with tempfile.TemporaryDirectory(prefix="holycrab-offline-lifecycle-") as temporary:
        root = Path(temporary)
        home, config, prefix = root / "home", root / "config", root / "prefix"
        config.mkdir()
        # The shell installer invokes python3; use exactly this test interpreter.
        python_bin = root / "bin"
        python_bin.mkdir()
        (python_bin / "python3").symlink_to(sys.executable)
        spec = importlib.util.spec_from_file_location("lifecycle_offline_environment", SOURCE / "tools/run_checks.py")
        checks = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checks)
        env = checks.isolated_environment(root)
        env.update({"HOME": str(home), "USERPROFILE": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                    "PATH": str(python_bin) + os.pathsep + env.get("PATH", ""),
                    "HOLYCRAB_INSTALL_PREFIX": str(prefix), "HOLYCRAB_CONFIG_DIR": str(config),
                    "HOLYCRAB_INSTALL_AGENTS": "codex,claude", "HOLYCRAB_INSTALL_MCP": "0",
                    "HOLYCRAB_NO_UPDATE_CHECK": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        old_version, version = source_version(baseline, env), source_version(source, env)
        launcher = prefix / "bin/holycrab"
        library = prefix / "lib/holycrab"

        def install(checkout, expected=0):
            return checked(["sh", str(checkout / "install.sh")],
                           {**env, "HOLYCRAB_INSTALL_SOURCE_DIR": str(checkout)}, expected=expected)

        def command(*args):
            return checked([str(launcher), *args], env)

        def require(condition, message):
            if not condition:
                raise RuntimeError(message)

        install(baseline)
        require(command("--version").stdout.strip() == old_version, "Baseline version mismatch")
        # Fake private fixtures only. No account lookup or real credentials.
        (config / "config.json").write_text('{"apiKey":"offline-fixture-never-send"}\n')
        (config / "config.json").chmod(0o600)
        (config / "attempts.json").write_text('{"reserved":{"attemptId":"reserved","state":"unknown"}}\n')
        (config / "attempts.json").chmod(0o600)
        (library / "user-note.txt").write_text("Keep user file\n")
        saved_config, saved_program = private_records(config), snapshot(prefix)
        saved_skills = snapshot(home)

        broken = root / "broken-candidate"
        shutil.copytree(source, broken, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
        health = broken / "holycrab/scripts/holycrab_runtime/lifecycle.py"
        text = health.read_text(encoding="utf-8")
        require(text.count("runtime_ok = ok") == 1, "Cannot inject the installed health-check failure")
        health.write_text(text.replace("runtime_ok = ok", "runtime_ok = False  # offline injected failure"), encoding="utf-8")
        checked([sys.executable, str(broken / "tools/refresh_installer_hashes.py")], env)
        failed = install(broken, expected=1)
        require("previous managed files were restored" in failed.stderr, "Installer did not confirm rollback")
        require(snapshot(prefix) == saved_program, "Failed upgrade did not restore original bytes")
        require(snapshot(home) == saved_skills, "Failed upgrade changed the previous Agent Skills")
        require(private_records(config) == saved_config, "Failed upgrade modified local records")
        require(command("--version").stdout.strip() == old_version, "Rollback left the wrong version")

        install(source)
        require(command("--version").stdout.strip() == version, "Candidate version mismatch")
        report = json.loads(command("doctor", "--json").stdout)
        require(report["ok"] is True and report["runtimeOk"] is True, "Installed candidate is unhealthy")
        require(private_records(config) == saved_config, "Upgrade modified private records")
        command("uninstall", "--yes")
        require(not launcher.exists(), "Uninstall left its launcher")
        require(private_records(config) == saved_config, "Default uninstall modified private records")
        require((library / "user-note.txt").read_text() == "Keep user file\n", "Uninstall removed user file")
        require(not (library / "holycrab_runtime").exists(), "Uninstall left runtime modules")
        require(not (library / "holycrab_cli.py").exists(), "Uninstall left the CLI entry point")
        for agent in (".agents", ".claude"):
            require(not (home / agent / "skills/holycrab/SKILL.md").exists(), "Uninstall left an owned Skill")
        return {"status": "passed", "baseline": old_version, "candidate": version,
                "checks": ["baseline-install", "failed-upgrade-byte-rollback", "agent-skill-rollback",
                           "candidate-upgrade", "installed-doctor", "default-uninstall", "private-records-preserved", "user-file-preserved"],
                "network": "not-used", "temporaryHome": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-source", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(run_lifecycle(baseline=args.baseline_source), indent=2))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
