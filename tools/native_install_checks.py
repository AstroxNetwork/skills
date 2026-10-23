#!/usr/bin/env python3
"""Shared assertions and disposable fixtures for native installer CI."""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def version_file(library: Path) -> Path:
    packaged = library / "holycrab_runtime/settings.py"
    return packaged if packaged.is_file() else library / "holycrab_cli.py"


def read_version(library: Path) -> str:
    source = version_file(library)
    for node in ast.parse(source.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, str) and re.fullmatch(r"\d+\.\d+\.\d+", value):
                return value
    raise ValueError(f"No stable VERSION assignment in {source}")


def verify_runtime(library: Path, source: Path = ROOT) -> None:
    manifest_bytes = (source / "runtime-files.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    rows = [row for row in manifest["files"] if row["kind"] == "runtime"]
    expected = {row["target"] for row in rows} | {"runtime-files.json", "installation.json"}
    if len({row["target"] for row in rows}) != len(rows):
        raise ValueError("Duplicate runtime manifest target")
    module_sources = {path.relative_to(source).as_posix() for path in (source / "holycrab/scripts/holycrab_runtime").glob("*.py")}
    recorded_modules = {row["source"] for row in rows if row["target"].startswith("holycrab_runtime/")}
    if module_sources != recorded_modules:
        raise ValueError("Manifest does not contain the complete source package")
    files = {path.relative_to(library).as_posix() for path in library.rglob("*")
             if path.is_file() and "__pycache__" not in path.parts}
    if files != expected:
        raise ValueError(f"Installed runtime file mismatch; missing={sorted(expected-files)}, extra={sorted(files-expected)}")
    if (library / "runtime-files.json").read_bytes() != manifest_bytes:
        raise ValueError("Installed runtime manifest differs from checked-out source")
    for row in rows:
        source_digest = hashlib.sha256((source / row["source"]).read_bytes()).hexdigest()
        actual_digest = hashlib.sha256((library / row["target"]).read_bytes()).hexdigest()
        if actual_digest != row["sha256"] or source_digest != row["sha256"]:
            raise ValueError("Runtime file bytes differ from verified source: " + row["target"])


def set_fixture_version(library: Path, version: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid fixture version")
    path = version_file(library)
    source, count = re.subn(r'(?m)^VERSION = "[^"]+"$', 'VERSION = "' + version + '"', path.read_text(encoding="utf-8"))
    if count != 1:
        raise ValueError("Fixture must replace exactly one active VERSION assignment")
    path.write_text(source, encoding="utf-8")
    for cache in library.rglob("__pycache__"):
        shutil.rmtree(cache)
    if read_version(library) != version:
        raise ValueError("Fixture version replacement did not take effect")


def make_broken_source(target: Path, source: Path = ROOT) -> None:
    """Create a hashed candidate whose doctor intentionally fails after installation."""
    target.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((source / "runtime-files.json").read_text(encoding="utf-8"))
    for name in {row["source"] for row in manifest["files"]} | {"runtime-files.json", "install.sh", "install.ps1"}:
        output = target / name
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, output)
    path = target / "holycrab/scripts/holycrab_runtime/cli.py"
    original = path.read_text(encoding="utf-8")
    needle = "def main() -> int:\n"
    if original.count(needle) != 1:
        raise ValueError("Cannot locate the active CLI main for doctor fault injection")
    path.write_text(original.replace(needle, needle + '    if sys.argv[1:2] == ["doctor"]:\n'
                                    '        print(\'{"ok": false, "runtimeOk": false}\')\n'
                                    '        return 7\n'), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("fixture_hash_generator", source / "tools/refresh_installer_hashes.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    generator.ROOT = target
    for name, data in generator.generated().items():
        (target / name).write_bytes(data)


def snapshot(library: Path) -> dict[str, str]:
    return {path.relative_to(library).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(library.rglob("*")) if path.is_file() and "__pycache__" not in path.parts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("version", "runtime"):
        commands.add_parser(name).add_argument("library", type=Path)
    fixture = commands.add_parser("fixture-version")
    fixture.add_argument("library", type=Path)
    fixture.add_argument("version")
    commands.add_parser("broken-source").add_argument("target", type=Path)
    for name in ("snapshot", "compare-snapshot"):
        command = commands.add_parser(name)
        command.add_argument("library", type=Path)
        command.add_argument("file", type=Path)
    args = parser.parse_args()
    if args.command == "version":
        print(read_version(args.library))
    elif args.command == "runtime":
        verify_runtime(args.library)
    elif args.command == "fixture-version":
        set_fixture_version(args.library, args.version)
    elif args.command == "broken-source":
        make_broken_source(args.target)
    elif args.command == "snapshot":
        args.file.write_text(json.dumps(snapshot(args.library), sort_keys=True), encoding="utf-8")
    elif snapshot(args.library) != json.loads(args.file.read_text(encoding="utf-8")):
        raise ValueError("Rollback did not restore the exact previous file inventory and bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
