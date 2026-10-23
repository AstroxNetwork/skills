"""HolyCrab lifecycle responsibilities."""
from __future__ import annotations
from contextlib import contextmanager

import argparse
import errno
import hashlib
import http.client
import json
import math
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable
from . import capabilities as _capabilities
from . import presentation as _presentation
from . import results as _results
from . import settings as _settings
from . import state as _state
from . import tasks as _tasks
from . import transport as _transport
from . import uploads as _uploads



def strict_version(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    match = re.fullmatch(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value.strip())
    return tuple(map(int, match.groups())) if match else None


def read_update_state() -> dict[str, Any]:
    try:
        value = _state.read_json_file(_state.update_state_path(), {})
    except (SystemExit, UnicodeError):
        # The release cache is disposable. A truncated cache must never block a
        # business command or prevent an explicit refresh from repairing it.
        return {}
    return value if isinstance(value, dict) else {}


def release_update_info(release: Any) -> dict[str, Any]:
    current = strict_version(_settings.VERSION)
    tag = release.get("tag_name") if isinstance(release, dict) else None
    latest = strict_version(tag)
    if (
        current is None or latest is None or release.get("draft") is not False
        or release.get("prerelease") is not False
    ):
        raise ValueError("Latest GitHub release is not an eligible stable semantic version")
    page = release.get("html_url")
    expected_page = _settings.RELEASE_PAGE_PREFIX + str(tag)
    if not isinstance(page, str) or page != expected_page:
        raise ValueError("Latest GitHub release has an invalid release page")
    return {
        "checkedAt": _state.utc_now(), "latestVersion": ".".join(map(str, latest)),
        "updateAvailable": latest > current, "releasePage": page,
        "release": release,
    }


def fetch_latest_release(timeout: float = _settings.UPDATE_CHECK_TIMEOUT) -> dict[str, Any]:
    request = urllib.request.Request(
        _settings.UPDATE_API_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"holycrab-cli/{_settings.VERSION}",
                 "X-GitHub-Api-Version": "2022-11-28"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("GitHub release response is too large")
    release = json.loads(raw.decode("utf-8"))
    return release_update_info(release)


def current_update_info(value: Any) -> dict[str, Any]:
    result = dict(value) if isinstance(value, dict) else {}
    latest, current = strict_version(result.get("latestVersion")), strict_version(_settings.VERSION)
    result["updateAvailable"] = bool(latest and current and latest > current)
    return result


_volatile_update_state: tuple[str, dict[str, Any]] | None = None


def _valid_epoch(value: Any) -> bool:
    return type(value) in (int, float) and 0 < value < 1e12 and math.isfinite(value)


def _within_interval(value: Any, interval: float) -> bool:
    # A future timestamp (clock rollback or corrupt cache) must not suppress
    # checks indefinitely. bool/NaN/infinity are not valid timestamps either.
    return _valid_epoch(value) and 0 <= time.time() - value < interval


def _effective_update_state() -> dict[str, Any]:
    cached = read_update_state()
    if _volatile_update_state and _volatile_update_state[0] == str(_state.update_state_path()):
        memory = _volatile_update_state[1]
        disk_epoch = cached.get("checkedAtEpoch")
        memory_epoch = memory.get("checkedAtEpoch")
        if _valid_epoch(memory_epoch) and (not _valid_epoch(disk_epoch) or memory_epoch >= disk_epoch):
            return dict(memory)
    return dict(cached)


def _save_update_state(value: dict[str, Any]) -> None:
    global _volatile_update_state
    try:
        _state.write_private_json(_state.update_state_path(), value)
    except OSError:
        # Long-running MCP servers must still throttle checks and retain the
        # verified release when the cache directory is temporarily unwritable.
        _volatile_update_state = (str(_state.update_state_path()), dict(value))
    else:
        _volatile_update_state = None


@contextmanager
def _update_lock() -> Any:
    path = _state.update_state_path().with_suffix(".lock")
    lock = _state.file_lock(path, blocking=False)
    try:
        lock.__enter__()
    except OSError as error:
        busy = error.errno in {errno.EAGAIN, errno.EWOULDBLOCK}
        if os.name == "nt" and error.errno == errno.EACCES:
            # msvcrt uses EACCES for a busy byte lock, but opening an unwritable
            # directory/file uses it too. A byte lock still permits opening the
            # existing file; probe without creating or truncating anything.
            try:
                descriptor = os.open(path, os.O_RDWR)
            except OSError:
                busy = False
            else:
                os.close(descriptor)
                busy = True
        # None means that persistence is unavailable, not that another checker
        # owns the lock. In that case the process-local cache still works.
        yield False if busy else None
    else:
        try:
            yield True
        finally:
            try:
                lock.__exit__(None, None, None)
            except OSError:
                pass


def _cached_check_is_fresh(cached: dict[str, Any]) -> bool:
    interval = _settings.UPDATE_RETRY_SECONDS if cached.get("checkFailed") is True else _settings.UPDATE_CHECK_SECONDS
    valid_result = cached.get("checkFailed") is True or strict_version(
        current_update_info(cached.get("update")).get("latestVersion")) is not None
    return valid_result and _within_interval(cached.get("checkedAtEpoch"), interval)


def _refresh_update_state(cached: dict[str, Any], *, force: bool, timeout: float) -> dict[str, Any]:
    try:
        update = fetch_latest_release(timeout)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException) as error:
        fallback = {**current_update_info(cached.get("update")), "stale": True}
        _save_update_state({**cached, "checkedAtEpoch": time.time(), "checkFailed": True, "update": fallback})
        if force:
            return {"checkedAt": _state.utc_now(), "latestVersion": None, "updateAvailable": False,
                    "error": _presentation.sanitize_text_for_output(str(error))}
        return fallback
    stored = {key: value for key, value in update.items() if key != "release"}
    _save_update_state({**cached, "checkedAtEpoch": time.time(), "checkFailed": False,
                        "update": stored, "release": update["release"]})
    return update


def check_for_update(*, force: bool = False, timeout: float = _settings.UPDATE_CHECK_TIMEOUT) -> dict[str, Any]:
    cached = _effective_update_state()
    if os.environ.get("HOLYCRAB_NO_UPDATE_CHECK") == "1" and not force:
        return {**current_update_info(cached.get("update")), "disabled": True}
    if not force and _cached_check_is_fresh(cached):
        return current_update_info(cached.get("update"))
    with _update_lock() as acquired:
        cached = _effective_update_state()
        if not force and (acquired is False or _cached_check_is_fresh(cached)):
            return current_update_info(cached.get("update"))
        return _refresh_update_state(cached, force=force, timeout=timeout)


def _update_notice(update: dict[str, Any]) -> str | None:
    if os.environ.get("HOLYCRAB_NO_UPDATE_CHECK") == "1":
        return None
    update = current_update_info(update)
    latest = update.get("latestVersion")
    page = update.get("releasePage")
    if (not update.get("updateAvailable") or update.get("stale") is True
            or not isinstance(page, str)
            or page not in {_settings.RELEASE_PAGE_PREFIX + str(latest), _settings.RELEASE_PAGE_PREFIX + "v" + str(latest)}):
        return None
    return (f"HolyCrab update available: {_settings.VERSION} -> {latest}\n"
            f"Release: {page}\nNext:\n  holycrab update")


def cached_update_notification() -> dict[str, str] | None:
    cached = _effective_update_state()
    if not _cached_check_is_fresh(cached):
        return None
    update = current_update_info(cached.get("update"))
    notice = _update_notice(update)
    if notice is None:
        return None
    return {"currentVersion": _settings.VERSION, "latestVersion": update["latestVersion"],
            "releasePage": update["releasePage"], "command": "holycrab update", "message": notice}


def cached_update_notice() -> str | None:
    notification = cached_update_notification()
    return notification["message"] if notification else None


def automatic_update_notice() -> str | None:
    """Refresh and claim the CLI reminder without delaying another process."""
    if os.environ.get("HOLYCRAB_NO_UPDATE_CHECK") == "1":
        return None
    check_for_update(force=False, timeout=_settings.UPDATE_CHECK_TIMEOUT)
    with _update_lock() as acquired:
        if acquired is False:
            return None
        cached = _effective_update_state()
        if not _cached_check_is_fresh(cached):
            return None
        update = current_update_info(cached.get("update"))
        notice = _update_notice(update)
        if notice is None:
            return None
        notification = cached.get("notification")
        notification = notification if isinstance(notification, dict) else {}
        previous = strict_version(notification.get("latestVersion"))
        latest = strict_version(update.get("latestVersion"))
        if previous and latest and previous >= latest and _within_interval(
                notification.get("notifiedAtEpoch"), _settings.UPDATE_CHECK_SECONDS):
            return None
        cached["notification"] = {"latestVersion": update["latestVersion"], "notifiedAtEpoch": time.time()}
        _save_update_state(cached)
        return notice


def release_installer(release: dict[str, Any]) -> tuple[str, str, str]:
    name = "install.ps1" if os.name == "nt" else "install.sh"
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise SystemExit("GitHub release does not contain installer assets")
    asset = next((item for item in assets if isinstance(item, dict) and item.get("name") == name), None)
    if not isinstance(asset, dict):
        raise SystemExit(f"GitHub release does not contain {name}")
    url = asset.get("browser_download_url")
    digest = asset.get("digest")
    tag = release.get("tag_name")
    version = strict_version(tag)
    expected_tag = f"v{'.'.join(map(str, version))}" if version else None
    expected_prefix = f"https://github.com/AstroxNetwork/skills/releases/download/{expected_tag}/"
    if not isinstance(url, str) or expected_tag is None or not url.startswith(expected_prefix):
        raise SystemExit("Release installer has an invalid download URL")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise SystemExit("Release installer is missing its GitHub SHA-256 digest")
    return name, url, digest.split(":", 1)[1].lower()


def load_installation() -> dict[str, Any] | None:
    path = _state.installation_path()
    value = _state.read_json_file(path, None)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise SystemExit("HolyCrab installation manifest must contain a JSON object")
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise SystemExit(f"Unsafe permissions on {path}; reinstall HolyCrab")
    return value


def run_update(release: dict[str, Any], *, progress: Callable[[str], None] | None = None) -> None:
    name, url, expected = release_installer(release)
    manifest = load_installation()
    if not manifest:
        raise SystemExit("Installation record is missing. Run the installer to repair this installation before updating.")
    settings = installer_settings(manifest, str(_state.installation_path().parent.parent.parent))
    descriptor, temporary_name = tempfile.mkstemp(prefix="holycrab-update-", suffix=Path(name).suffix)
    temporary = Path(temporary_name)
    installing = False
    try:
        if progress:
            progress("Downloading the release installer...")
        digest = hashlib.sha256()
        total = 0
        with _transport.open_download(url) as remote, os.fdopen(descriptor, "wb") as local:
            descriptor = -1
            while chunk := remote.read(1024 * 1024):
                total += len(chunk)
                if total > _settings.MAX_INSTALLER_BYTES:
                    raise SystemExit("Release installer is unexpectedly large; update stopped")
                digest.update(chunk)
                local.write(chunk)
        if progress:
            progress("Verifying the installer SHA-256 digest and release version...")
        if digest.hexdigest() != expected:
            raise SystemExit("Release installer SHA-256 digest mismatch; update stopped")
        release_version = release.get("tag_name")
        installer_text = temporary.read_text(encoding="utf-8")
        version_marker = (f'$Version = "{release_version}"' if name == "install.ps1"
                          else f"VERSION={release_version}")
        if version_marker not in installer_text.splitlines():
            raise SystemExit("Release installer version does not match its release; update stopped")
        environment = os.environ.copy()
        environment.pop("HOLYCRAB_INSTALL_SOURCE_DIR", None)
        environment.pop("HOLYCRAB_INSTALL_REF", None)
        environment["HOLYCRAB_INSTALL_PREFIX"] = manifest["prefix"]
        environment["HOLYCRAB_INSTALL_AGENTS"] = settings["agents"]
        environment["HOLYCRAB_INSTALL_MCP"] = settings["mcp"]
        command = (["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(temporary)]
                   if os.name == "nt" else ["sh", str(temporary)])
        installing = True
        if progress:
            progress("Installing and running post-install checks; the installer will report each stage...")
        interactive = progress is not None and sys.stderr.isatty()
        completed = subprocess.run(command, env=environment, check=False,
                                   stdout=sys.stderr if interactive else subprocess.PIPE,
                                   stderr=None if interactive else subprocess.PIPE)
        if completed.returncode != 0:
            if not interactive:
                diagnostic = completed.stderr or completed.stdout or b""
                if isinstance(diagnostic, bytes):
                    diagnostic = diagnostic.decode("utf-8", errors="replace")
                safe = _presentation.terminal_safe_text(_presentation.sanitize_text_for_output(diagnostic))
                if safe.strip():
                    print("Installer details:\n" + safe[-4000:].strip(), file=sys.stderr)
            raise SystemExit("HolyCrab installer failed. Run `holycrab doctor --json`; reinstall if necessary. Review local state before assuming restoration completed")
        if progress:
            progress("Update completed; the installer's version and health checks passed.")
    except KeyboardInterrupt:
        if installing:
            raise _results.CommandInterrupted({"state": "unknown", "interrupted": True,
                "nextAction": _results.next_action("CHECK_INSTALLATION", "Update interrupted during installation. Check local health; reinstall if the launcher no longer works. Do not assume rollback completed.", "holycrab doctor --json")}) from None
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _normalized_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def installer_settings(previous: dict[str, Any], prefix: str,
                       agents: str = "", mcp: str = "") -> dict[str, str]:
    """Resolve installer settings only after the release source is verified."""
    if not isinstance(previous, dict):
        raise ValueError("Invalid installation record; repair it before installing")
    if previous:
        if (previous.get("managedBy") != _settings.INSTALLATION_MANAGER
                or not isinstance(previous.get("prefix"), str)
                or _normalized_path(previous["prefix"]) != _normalized_path(prefix)
                or not isinstance(previous.get("agents"), list)
                or any(not isinstance(item, str) or item not in {"codex", "claude"} for item in previous["agents"])
                or not isinstance(previous.get("mcp"), bool)):
            raise ValueError("Installation record does not match this installation; repair it before updating")
    chosen = agents.strip() or (",".join(previous.get("enabledAgents", previous["agents"])) or "none" if previous else "codex,claude")
    selected = chosen.split(",")
    if chosen != "none" and (any(item not in {"codex", "claude"} for item in selected)
                              or len(selected) != len(set(selected))):
        raise ValueError("HOLYCRAB_INSTALL_AGENTS must be codex, claude, codex,claude, or none")
    selected_mcp = mcp.strip() or ("1" if previous.get("enabledMcp", previous.get("mcp", True)) else "0")
    if selected_mcp not in {"0", "1"}:
        raise ValueError("HOLYCRAB_INSTALL_MCP must be 0 or 1")
    return {"agents": chosen, "mcp": selected_mcp}


def runtime_package_files(manifest_path: Path | None = None) -> list[str]:
    if manifest_path is None:
        candidates = [_settings.RUNTIME_ROOT / "runtime-files.json",
                      _settings.RUNTIME_ROOT.parent.parent / "runtime-files.json"]
        manifest_path = next((p for p in candidates if p.is_file()), candidates[0])
    manifest = _state.read_json_file(manifest_path, {})
    output = []
    for row in manifest.get("files", []):
        target = row.get("target", "")
        if row.get("kind") == "runtime" and target.startswith("holycrab_runtime/"):
            if not re.fullmatch(r"holycrab_runtime/[A-Za-z_][A-Za-z0-9_]*\.py", target):
                raise ValueError("Invalid runtime module path")
            output.append(target)
    return output


def finalize_installation(previous_path: str, journal_path: str) -> None:
    """Preserve ownership while changing enabled Agent settings, before commit."""
    manifest = load_installation() or {}
    previous = _state.read_json_file(Path(previous_path), {})
    installer_settings(previous, manifest["prefix"])
    selected = list(manifest["agents"])
    enabled_mcp = manifest["mcp"]
    manifest.update(schemaVersion=3, enabledAgents=selected, enabledMcp=enabled_mcp)
    owned_agents = set(selected)
    pending = []
    registrations = dict(previous.get("agentRegistrations", {}))
    registrations.update(manifest.get("agentRegistrations", {}))
    old_hashes = _manifest_hashes(previous)
    expected = str(Path(manifest["prefix"]) / ("lib/holycrab/holycrab_cli.py" if os.name == "nt" else "bin/holycrab"))
    for agent in previous.get("agents", []):
        if agent not in {"codex", "claude"}: continue
        if agent not in selected:
            root = _skill_root(agent)
            for name in _settings.MANAGED_SKILL_FILES:
                path = root / name
                digest = old_hashes.get(_normalized_path(path))
                if not path.exists(): continue
                if digest and not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
                    path.unlink()
                else:
                    pending.append(f"Modified or unmanaged {agent} Skill kept: {path}")
                    owned_agents.add(agent)
            for directory in (root / "references", root / "agents", root):
                try: directory.rmdir()
                except OSError: pass
        if agent in selected and enabled_mcp: continue
        record = agent_registration(previous, agent)
        if not previous.get("mcp") or (record is not None and not record.get("managed")): continue
        executable = find_agent_client(agent, previous)
        if not executable:
            pending.append(f"MCP cleanup pending for {agent}: client unavailable")
            owned_agents.add(agent)
            continue
        current, absent, _ = inspect_agent_registration(executable, agent)
        if absent:
            registrations.pop(agent, None)
        elif registration_matches(current, previous, agent, expected):
            journal = Path(journal_path)
            entries = _state.read_json_file(journal, {})
            entries.setdefault(agent, {"executable": executable, "before": current, "after": None})
            _state.write_private_json(journal, entries)
            scope = ["--scope", "user"] if agent == "claude" else []
            if run_agent_client(executable, ["mcp", "remove", *scope, "holycrab"]).returncode:
                raise RuntimeError(f"Could not remove managed {agent} registration; rolling back")
            _, absent, _ = inspect_agent_registration(executable, agent)
            if not absent: raise RuntimeError(f"Could not verify removal of {agent} registration")
            registrations.pop(agent, None)
        elif current is None:
            pending.append(f"MCP cleanup pending for {agent}: registration cannot be inspected")
            owned_agents.add(agent)
        else:
            registrations.pop(agent, None)  # A different registration is not ours.
    for row in [*previous.get("coreFiles", []), *previous.get("retainedFiles", [])]:
        path = row.get("path", "")
        if any(path == str(_skill_root(agent) / name) for agent in owned_agents - set(selected)
               for name in _settings.MANAGED_SKILL_FILES):
            if row not in manifest.setdefault("retainedFiles", []): manifest["retainedFiles"].append(row)
    manifest["agents"] = sorted(owned_agents)
    manifest["agentRegistrations"] = registrations
    manifest["mcp"] = enabled_mcp or any(r.get("managed") for r in registrations.values()) or any(x.startswith("MCP cleanup pending") for x in pending)
    manifest["cleanupPending"] = pending
    _state.write_private_json(_state.installation_path(), manifest)
    for warning in pending: print("Warning: " + warning, file=sys.stderr)


def remove_managed_program_files(manifest: dict[str, Any], library: Path) -> list[str]:
    """Delete verified owned runtime files, preserving user modifications."""
    warnings = []
    directories = {library}
    # Keep the bootstrap until the other managed files are gone. The Windows
    # batch launcher uses its presence to avoid deleting itself on a failure.
    rows = sorted(manifest.get("coreFiles", []), key=lambda row: row.get("path") == "holycrab_cli.py")
    for row in rows:
        relative = row.get("path", "")
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts: continue
        path = library / relative
        if not path.resolve().is_relative_to(library.resolve()): continue
        if path.exists():
            if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row.get("sha256"):
                warnings.append(f"Modified program file kept: {path}")
                continue
            path.unlink()
        directories.update(path.parents[:len(path.parts)-len(library.parts)])
        if path.suffix == '.py':
            cache = path.parent / '__pycache__'
            if cache.is_dir() and not cache.is_symlink():
                for bytecode in cache.glob(path.stem + '.cpython-*.pyc'):
                    if re.fullmatch(re.escape(path.stem)+r'\.cpython-\d+(?:\.opt-\d+)?\.pyc', bytecode.name): bytecode.unlink()
                directories.add(cache)
    if not warnings:
        (library / 'installation.json').unlink(missing_ok=True)
    for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
        if directory == library.parent: continue
        try: directory.rmdir()
        except OSError: pass
    return warnings


def remove_windows_program_files(manifest: dict[str, Any], library: Path, *,
                                 timeout: float = 8.0, interval: float = 0.2) -> list[str]:
    """Retry transient file locks before allowing launcher removal or data purge."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return remove_managed_program_files(manifest, library)
        except OSError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(interval, remaining))


def prepare_runtime_imports(library: Path) -> None:
    """Discard owned bytecode so rapid same-size upgrades cannot load stale code."""
    for relative in ("holycrab_cli.py", *runtime_package_files()):
        source = library / relative
        cache = source.parent / "__pycache__"
        if cache.is_symlink() or not cache.is_dir():
            continue
        for bytecode in cache.glob(source.stem + ".cpython-*.pyc"):
            if re.fullmatch(re.escape(source.stem) + r"\.cpython-\d+(?:\.opt-\d+)?\.pyc", bytecode.name):
                bytecode.unlink()
        try: cache.rmdir()
        except OSError: pass


def restore_installer_files(backup: Path, prefix: Path, agents: list[str]) -> None:
    """Restore only installer-owned filenames; retain unrelated files and backups on failure."""
    runtime = ("holycrab_cli.py", "installation.json", "references/capabilities.json",
               "vendor/segno-1.6.6-py3-none-any.whl", "vendor/LICENSE.segno")
    runtime = tuple(dict.fromkeys((*runtime, "runtime-files.json", *runtime_package_files(),
                                  *runtime_package_files(backup / "lib" / "runtime-files.json"))))
    launcher = "holycrab.cmd" if os.name == "nt" else "holycrab"
    pairs = [(backup / "lib" / name, prefix / "lib/holycrab" / name) for name in runtime]
    pairs.append((backup / launcher, prefix / "bin" / launcher))
    for agent in ("codex", "claude"):
        if agent not in agents and not (backup / (agent + "-skill")).exists():
            continue
        directory = ".agents" if agent == "codex" else ".claude"
        for name in ("SKILL.md", "references/capabilities.json", "agents/openai.yaml"):
            pairs.append((backup / (agent + "-skill") / name,
                          Path.home() / directory / "skills/holycrab" / name))
    for source, target in pairs:
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        elif target.is_file():
            target.unlink()
    library = prefix / "lib/holycrab"
    prepare_runtime_imports(library)
    for directory in (library / "holycrab_runtime", library / "references", library / "vendor", library):
        try: directory.rmdir()
        except OSError: pass
    restore_installer_mcp(backup / "mcp-transaction.json")


def restore_installer_mcp(journal: Path, *, only_agent: str | None = None) -> None:
    entries = _state.read_json_file(journal, {})
    for agent, entry in entries.items():
        if only_agent is not None and agent != only_agent:
            continue
        executable, before, after = entry["executable"], entry["before"], entry["after"]
        current, absent, _ = inspect_agent_registration(executable, agent)
        if current == before and (before is not None or absent):
            continue
        if not absent and current != after:
            raise RuntimeError(f"{agent} connection changed or cannot be inspected; backup retained")
        scope = ["--scope", "user"] if agent == "claude" else []
        if current is not None:
            if run_agent_client(executable, ["mcp", "remove", *scope, "holycrab"]).returncode:
                raise RuntimeError(f"Could not restore {agent} connection; backup retained")
        if before is not None:
            result = run_agent_client(executable, ["mcp", "add", *scope, "holycrab", "--",
                                                   before["command"], *before["args"]])
            if result.returncode:
                raise RuntimeError(f"Could not restore {agent} connection; backup retained")
        checked, missing, _ = inspect_agent_registration(executable, agent)
        if checked != before or (before is None and not missing):
            raise RuntimeError(f"Could not verify restored {agent} connection; backup retained")


def validated_uninstall_manifest() -> tuple[dict[str, Any], Path, Path, Path]:
    manifest = load_installation()
    if manifest is None:
        raise SystemExit("This is not an installer-managed HolyCrab installation; nothing was removed")
    manager = manifest.get("managedBy")
    if manager not in {None, _settings.INSTALLATION_MANAGER}:
        raise SystemExit("This HolyCrab installation is managed by an unknown installer; nothing was removed")
    schema = manifest.get("schemaVersion")
    if schema not in {None, 2, _settings.INSTALLATION_SCHEMA_VERSION}:
        raise SystemExit("This HolyCrab installation uses an unsupported manifest schema; nothing was removed")
    prefix_value = manifest.get("prefix")
    if not isinstance(prefix_value, str) or not prefix_value.strip():
        raise SystemExit("HolyCrab installation manifest has no valid prefix; nothing was removed")
    prefix = _normalized_path(prefix_value)
    library = prefix / "lib" / "holycrab"
    current_library = _normalized_path(_state.installation_path().parent)
    if current_library != _normalized_path(library):
        raise SystemExit("HolyCrab installation manifest does not match this CLI; nothing was removed")
    launcher = prefix / "bin" / ("holycrab.cmd" if os.name == "nt" else "holycrab")
    agents = manifest.get("agents")
    if not isinstance(agents, list) or any(agent not in {"codex", "claude"} for agent in agents):
        raise SystemExit("HolyCrab installation manifest has invalid Agent selections; nothing was removed")
    return manifest, prefix, library, launcher


def agent_registration(manifest: dict[str, Any], agent: str) -> dict[str, Any] | None:
    records = manifest.get("agentRegistrations")
    record = records.get(agent) if isinstance(records, dict) else None
    return record if isinstance(record, dict) else None


def find_agent_client(agent: str, manifest: dict[str, Any], preferred: str | None = None) -> str | None:
    """Bounded discovery, never scan arbitrary Agent configurations or directories."""
    if agent not in {"codex", "claude"}:
        return None
    record = agent_registration(manifest, agent) or {}
    names = {agent + suffix for suffix in ("", ".exe", ".cmd", ".bat", ".ps1")}
    for value in (preferred, record.get("executable")):
        if isinstance(value, str):
            path = Path(value).expanduser()
            if path.is_absolute() and path.name.lower() in names and path.is_file() and (
                os.name == "nt" or os.access(path, os.X_OK)
            ):
                return str(path)
    executable = shutil.which(agent)
    if executable:
        return executable
    candidates = [Path.home() / ".local" / "bin" / agent]
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / "npm" / f"{agent}.cmd")
    else:
        candidates.extend(Path(directory) / agent for directory in ("/opt/homebrew/bin", "/usr/local/bin"))
        if sys.platform == "darwin" and agent == "codex":
            candidates.extend(root / app / "Contents/Resources/codex"
                              for root in (Path("/Applications"), Path.home() / "Applications")
                              for app in ("ChatGPT.app", "Codex.app"))
    return next((str(path) for path in candidates if path.is_file() and (
        os.name == "nt" or os.access(path, os.X_OK)
    )), None)


def agent_client_command(executable: str, arguments: list[str]) -> list[str]:
    if executable.lower().endswith(".ps1"):
        powershell = shutil.which("powershell")
        if not powershell:
            raise OSError("PowerShell is unavailable for this Agent client")
        return [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", executable, *arguments]
    return [executable, *arguments]


def run_agent_client(executable: str, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(agent_client_command(executable, arguments), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=10, check=False)


def parse_mcp_registration(output: str, agent: str) -> dict[str, Any] | None:
    try:
        value = json.loads(output)
    except (ValueError, TypeError):
        value = None
    if isinstance(value, dict):
        transport = value.get("transport", value)
        if isinstance(transport, dict) and transport.get("type") == "stdio" and (
            isinstance(transport.get("command"), str) and isinstance(transport.get("args", []), list)
            and all(isinstance(item, str) for item in transport.get("args", []))
        ):
            return {"command": transport["command"], "args": transport.get("args", []),
                    "scope": "user" if agent == "codex" else value.get("scope")}
        return None
    # Claude's supported `mcp get` output has separate Command, Args and Scope lines.
    fields = {}
    for line in output.splitlines():
        match = re.fullmatch(r"\s*(Command|Args|Scope|Type):\s*(.*?)\s*", line, re.IGNORECASE)
        if match:
            key = match[1].lower()
            if key in fields:  # Ambiguous output must never establish ownership.
                return None
            fields[key] = match[2]
    if "command" not in fields or fields.get("type", "stdio") != "stdio":
        return None
    try:
        if "args" in fields:
            command = fields["command"].strip('"')
            arguments = shlex.split(fields["args"], posix=os.name != "nt")
        else:
            tokens = shlex.split(fields["command"], posix=os.name != "nt")
            command, arguments = tokens[0], tokens[1:]
    except (ValueError, IndexError):
        return None
    return {"command": command, "args": [item.strip('"') for item in arguments],
            "scope": "user" if agent == "codex" or fields.get("scope", "").lower().startswith("user config") else None}


def registration_matches(registration: dict[str, Any] | None, manifest: dict[str, Any],
                         agent: str, expected_command: str) -> bool:
    if registration is None or registration.get("scope") != "user" or not mcp_points_to_cli(registration, expected_command):
        return False
    record = agent_registration(manifest, agent)
    if record is None:
        return True  # Legacy installer manifest, still require exact CLI command and args.
    return (record.get("managed") is True and record.get("scope") == "user"
            and isinstance(record.get("command"), str)
            and _normalized_path(record["command"]) == _normalized_path(registration["command"])
            and record.get("args") == registration["args"])


def inspect_agent_registration(executable: str, agent: str) -> tuple[dict[str, Any] | None, bool, int]:
    arguments = ["mcp", "get", "holycrab"] + (["--json"] if agent == "codex" else [])
    completed = run_agent_client(executable, arguments)
    if completed.returncode != 0:
        message = (completed.stderr + "\n" + completed.stdout).lower()
        absent = re.search(r"no mcp server (?:named|found with (?:the )?name)\s*:?\s+[\"']?holycrab[\"']?(?: found)?[.\s]*$", message.strip()) is not None
        return None, absent, completed.returncode
    return parse_mcp_registration(completed.stdout, agent), False, 0


def register_installer_mcp(agent: str, preferred: str | None, command: str, arguments: list[str],
                           previous_path: str, journal_path: str | None = None) -> None:
    """Internal installer entry point; no public command, credential or arbitrary Agent mutation."""
    manifest = load_installation() or {}
    previous = _state.read_json_file(Path(previous_path), {}) if previous_path else {}
    if not isinstance(previous, dict) or previous.get("managedBy") != _settings.INSTALLATION_MANAGER or (
        previous.get("prefix") != manifest.get("prefix")
    ):
        previous = {}
    executable = find_agent_client(agent, previous, preferred)
    expected = str(_state.installation_path().parent / "holycrab_cli.py") if os.name == "nt" else command
    record: dict[str, Any] = {"managed": False, "scope": "user", "command": command, "args": arguments,
                              "executable": executable}
    if agent not in {"codex", "claude"} or not mcp_points_to_cli(record, expected):
        raise RuntimeError("Installer MCP command does not point to this HolyCrab installation")
    # Preserve prior ownership when inspection fails, so uninstall reports pending cleanup.
    old_record = agent_registration(previous, agent)
    if old_record is not None:
        record = dict(old_record)
        if executable:
            record["executable"] = executable
    elif previous.get("mcp") is True and agent in previous.get("agents", []):
        record["managed"] = True
    original_record = dict(record)
    change_recorded = False
    try:
        if not executable:
            raise RuntimeError(f"{agent} is unavailable; its HolyCrab MCP registration was not checked")
        current, absent, _ = inspect_agent_registration(executable, agent)
        def remember_change() -> None:
            nonlocal change_recorded
            if journal_path:
                journal = Path(journal_path)
                entries = _state.read_json_file(journal, {})
                entries.setdefault(agent, {"executable": executable, "before": current,
                    "after": {"command": command, "args": arguments, "scope": "user"}})
                _state.write_private_json(journal, entries)
                change_recorded = True
        if current is not None and registration_matches(current, previous, agent, expected) and (
            previous.get("mcp") is True and agent in previous.get("agents", [])
            and (current["command"] != command or current["args"] != arguments)
        ):
            remember_change()
            remove = ["mcp", "remove"] + (["--scope", "user"] if agent == "claude" else [])
            if run_agent_client(executable, [*remove, "holycrab"]).returncode != 0:
                raise RuntimeError(f"Could not repair the managed {agent} MCP entry; it was kept")
            absent = True
            current = None
        if current is not None and mcp_points_to_cli(current, expected) and current.get("scope") == "user":
            owned = previous.get("mcp") is True and agent in previous.get("agents", []) and registration_matches(current, previous, agent, expected)
            record = {"executable": executable, "managed": owned, "scope": "user",
                      "command": current["command"], "args": current["args"]}
            if not owned:
                print(f"Info: the existing {agent} HolyCrab MCP entry was kept; it is not installer-managed.", file=sys.stderr)
        elif absent:
            remember_change()
            add = ["mcp", "add"] + (["--scope", "user"] if agent == "claude" else [])
            result = run_agent_client(executable, [*add, "holycrab", "--", command, *arguments])
            if result.returncode != 0:
                raise RuntimeError(f"{agent} HolyCrab MCP registration failed; check the client and rerun the installer")
            record = {"executable": executable, "managed": True, "scope": "user", "command": command, "args": arguments}
            checked, _, _ = inspect_agent_registration(executable, agent)
            if not registration_matches(checked, {"agentRegistrations": {agent: record}}, agent, expected):
                raise RuntimeError(f"{agent} HolyCrab MCP registration could not be verified; check the client before using it")
        else:
            raise RuntimeError(f"The {agent} MCP entry could not be verified as this installation; it was not changed")
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        if change_recorded and journal_path:
            restore_installer_mcp(Path(journal_path), only_agent=agent)
            record = original_record
        print(f"Warning: {_presentation.sanitize_text_for_output(str(error))}", file=sys.stderr)
    manifest.setdefault("agentRegistrations", {})[agent] = record
    _state.write_private_json(_state.installation_path(), manifest)


def remove_managed_mcp_registrations(manifest: dict[str, Any], expected_command: str) -> list[str]:
    warnings: list[str] = []
    if manifest.get("mcp") is not True:
        return warnings
    for agent in manifest.get("agents", []):
        if agent not in {"codex", "claude"}:
            continue
        record = agent_registration(manifest, agent)
        if record is not None and record.get("managed") is not True:
            warnings.append(f"Info: the {agent} MCP entry is not installer-managed; it was kept")
            continue
        executable = find_agent_client(agent, manifest)
        recovery = f"Open {agent} and remove only its HolyCrab MCP entry if it still points to this uninstalled CLI."
        if executable is None:
            warnings.append(f"MCP cleanup pending for {agent}: the client is unavailable. {recovery}")
            continue
        try:
            registration, absent, _ = inspect_agent_registration(executable, agent)
        except (OSError, subprocess.SubprocessError) as error:
            warnings.append(f"MCP cleanup pending for {agent}: inspection failed. {recovery}")
            continue
        if absent:
            continue
        if registration is None:
            warnings.append(f"MCP cleanup pending for {agent}: its registration could not be read safely. {recovery}")
            continue
        if not registration_matches(registration, manifest, agent, expected_command):
            warnings.append(f"Info: the {agent} MCP entry named holycrab is unmanaged or points elsewhere; it was kept")
            continue
        try:
            arguments = ["mcp", "remove"] + (["--scope", "user"] if agent == "claude" else [])
            removed = run_agent_client(executable, [*arguments, "holycrab"])
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError(
                f"Could not remove the managed {agent} MCP registration; no program files were removed: "
                f"{_presentation.sanitize_text_for_output(str(error))}"
            ) from error
        if removed.returncode != 0:
            raise RuntimeError(
                f"Could not remove the managed {agent} MCP registration; no program files were removed "
                f"(client exit code {removed.returncode})"
            )
    return warnings


def _manifest_hashes(manifest: dict[str, Any]) -> dict[Path, str]:
    result: dict[Path, str] = {}
    core, retained = manifest.get("coreFiles", []), manifest.get("retainedFiles", [])
    if not isinstance(core, list) or not isinstance(retained, list):
        return result
    core_files = [*core, *retained]
    for item in core_files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            continue
        expected = item.get("sha256")
        if isinstance(expected, str) and re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            path = Path(item["path"])
            if not path.is_absolute(): path = _state.installation_path().parent / path
            result[_normalized_path(path)] = expected.lower()
    return result


def _skill_root(agent: str) -> Path:
    parent = ".agents" if agent == "codex" else ".claude"
    return Path.home() / parent / "skills" / "holycrab"


def remove_managed_skill_files(manifest: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    expected_hashes = _manifest_hashes(manifest)
    for agent in manifest.get("agents", []):
        if agent not in {"codex", "claude"}:
            continue
        root = _skill_root(agent)
        for relative in _settings.MANAGED_SKILL_FILES:
            target = root / relative
            if not target.exists() and not target.is_symlink():
                continue
            expected = expected_hashes.get(_normalized_path(target))
            try:
                matches = target.is_file() and expected is not None and _uploads.sha256_stream(target) == expected
            except OSError:
                matches = False
            if not matches:
                warnings.append(f"Modified or unverified Skill file was kept: {target}")
                continue
            try:
                target.unlink()
            except OSError as error:
                warnings.append(f"Could not remove Skill file {target}: {_presentation.sanitize_text_for_output(str(error))}")
        for directory in (root / "references", root / "agents", root):
            try:
                directory.rmdir()
            except (FileNotFoundError, OSError):
                pass
    return warnings


def purge_known_local_state() -> list[str]:
    try:
        with _state.operation_lock(exclusive=True, blocking=False):
            return _purge_known_local_state()
    except BlockingIOError:
        return ["Refused to purge local data while a generation or upload is active. Wait for it to finish, then retry the purge."]


def _purge_known_local_state() -> list[str]:
    warnings: list[str] = []
    configured_root = _state.config_dir().expanduser().absolute()
    root = _normalized_path(configured_root)
    dangerous = {_normalized_path(Path(root.anchor)), _normalized_path(Path.home())}
    reparse = False
    if os.name == "nt":
        for part in (configured_root, *configured_root.parents):
            try:
                reparse = reparse or bool(getattr(part.lstat(), "st_file_attributes", 0) & 0x400)
            except FileNotFoundError:
                pass
    if configured_root.is_symlink() or reparse or root in dangerous:
        return [f"Refused to purge unsafe configuration directory: {root}"]
    for name in ("config.json", "attempts.json", "attempts.lock", "update-state.json", "update-state.lock", "health-state.json"):
        try:
            (root / name).unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            warnings.append(f"Could not remove {root / name}: {_presentation.sanitize_text_for_output(str(error))}")
    plans = root / "upload-plans"
    try:
        if plans.is_symlink():
            plans.unlink()
        elif plans.is_dir():
            for path in plans.iterdir():
                if path.is_symlink() or not path.is_file() or not re.fullmatch(r"[A-Za-z0-9]{1,64}\.json", path.name):
                    continue
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict) and value.get("uploadPlanId") == path.stem:
                    path.unlink()
            try:
                plans.rmdir()
            except OSError as error:
                if error.errno != errno.ENOTEMPTY:
                    raise
                warnings.append(f"Unknown files were kept in {plans}")
    except FileNotFoundError:
        pass
    except OSError as error:
        warnings.append(f"Could not remove all known state from {plans}: {_presentation.sanitize_text_for_output(str(error))}")
    authorizations = root / "real-human"
    try:
        if authorizations.is_symlink():
            authorizations.unlink()
        elif authorizations.is_dir():
            for directory in authorizations.iterdir():
                if directory.is_symlink() or not directory.is_dir() or not re.fullmatch(r"[A-Za-z0-9]{1,64}", directory.name):
                    continue
                for name in ("qr.png", "metadata.json"):
                    path = directory / name
                    if path.is_file() and not path.is_symlink():
                        path.unlink()
                try:
                    directory.rmdir()
                except OSError as error:
                    if error.errno != errno.ENOTEMPTY:
                        raise
                    warnings.append(f"Unknown files were kept in {directory}")
            try:
                authorizations.rmdir()
            except OSError as error:
                if error.errno != errno.ENOTEMPTY:
                    raise
                warnings.append(f"Unknown files were kept in {authorizations}")
    except FileNotFoundError:
        pass
    except OSError as error:
        warnings.append(f"Could not remove all known state from {authorizations}: {_presentation.sanitize_text_for_output(str(error))}")
    try:
        root.rmdir()
    except FileNotFoundError:
        pass
    except OSError as error:
        if error.errno == errno.ENOTEMPTY:
            warnings.append(f"Unknown files were kept in {root}")
        else:
            warnings.append(f"Could not remove empty configuration directory {root}: {_presentation.sanitize_text_for_output(str(error))}")
    return warnings


def _bin_has_other_entries(bin_directory: Path, launcher: Path) -> bool:
    try:
        return any(_normalized_path(item) != _normalized_path(launcher) for item in bin_directory.iterdir())
    except FileNotFoundError:
        return False


def _remove_posix_profile_registration(profile: Path) -> bool:
    path_line = 'export PATH="$HOME/.local/bin:$PATH"'
    try:
        lines = profile.read_text(encoding="utf-8").splitlines(keepends=True)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    kept: list[str] = []
    index = 0
    removed = False
    while index < len(lines):
        if (
            lines[index].rstrip("\r\n") == "# HolyCrab CLI"
            and index + 1 < len(lines)
            and lines[index + 1].rstrip("\r\n") == path_line
        ):
            if kept and not kept[-1].strip():
                kept.pop()
            index += 2
            removed = True
            continue
        kept.append(lines[index])
        index += 1
    if not removed:
        return True
    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{profile.name}.", suffix=".tmp", dir=profile.parent)
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write("".join(kept))
        temporary.chmod(stat.S_IMODE(profile.stat().st_mode))
        os.replace(temporary, profile)
        return True
    except OSError:
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def remove_managed_path_registration(manifest: dict[str, Any], prefix: Path, launcher: Path) -> list[str]:
    registration = manifest.get("pathRegistration")
    if not isinstance(registration, dict):
        return ["PATH registration was kept because this installation has no ownership record"]
    if registration.get("kind") == "none" and registration.get("addedByInstaller") is False:
        return []
    if registration.get("addedByInstaller") is not True:
        return ["PATH registration was kept because the installer did not add it"]
    bin_directory = prefix / "bin"
    if _bin_has_other_entries(bin_directory, launcher):
        return [f"PATH registration was kept because {bin_directory} contains other programs"]
    kind = registration.get("kind")
    recorded_directory = registration.get("directory")
    if not isinstance(recorded_directory, str) or _normalized_path(recorded_directory) != _normalized_path(bin_directory):
        return ["PATH registration was kept because its ownership record is invalid"]
    if kind == "shell-profile" and os.name != "nt":
        profile_value = registration.get("profile")
        if not isinstance(profile_value, str):
            return ["PATH profile entry was kept because its ownership record is incomplete"]
        profile = _normalized_path(profile_value)
        allowed = {_normalized_path(Path.home() / name) for name in (".zshrc", ".bashrc", ".bash_profile", ".profile")}
        if profile not in allowed or not _remove_posix_profile_registration(profile):
            return [f"PATH profile entry could not be removed safely: {profile}"]
        return []
    if kind == "windows-user-path" and os.name == "nt":  # pragma: no cover - Windows CI
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE
            ) as key:
                try:
                    value, value_type = winreg.QueryValueEx(key, "Path")
                except FileNotFoundError:
                    value, value_type = "", winreg.REG_EXPAND_SZ
                target = os.path.normcase(os.path.normpath(str(bin_directory)))
                kept = [
                    entry for entry in str(value).split(";") if entry
                    and os.path.normcase(os.path.normpath(entry)) != target
                ]
                winreg.SetValueEx(key, "Path", 0, value_type, ";".join(kept))
        except OSError:
            return ["Windows user PATH entry could not be removed safely"]
        return []
    return ["PATH registration was kept because its ownership record does not match this platform"]


def schedule_windows_program_cleanup(launcher: Path, library: Path) -> None:  # pragma: no cover - Windows CI
    identity = ""
    expected = ""
    if launcher.exists():
        info = launcher.stat()
        identity = ":".join(str(getattr(info, key)) for key in ("st_dev", "st_ino", "st_mtime_ns", "st_size"))
        expected = hashlib.sha256(launcher.read_bytes()).hexdigest()
    descriptor, helper_name = tempfile.mkstemp(prefix="holycrab-uninstall-", suffix=".py")
    helper = Path(helper_name)
    failure_log = helper.with_suffix(".log")
    # Only stdlib imports: installed runtime files have already been removed.
    # Use the interpreter that is running this CLI, independently of shell hosts.
    program = r'''import hashlib
import sys
import time
from pathlib import Path

launcher, library, helper, failure_log = map(Path, sys.argv[1:5])
expected, identity = sys.argv[5:7]
deadline = time.monotonic() + 8
failure = None
time.sleep(.75)
try:
    while True:
        try:
            info = launcher.stat()
            current = ":".join(str(getattr(info, key)) for key in ("st_dev", "st_ino", "st_mtime_ns", "st_size"))
            if launcher.is_symlink() or current != identity or hashlib.sha256(launcher.read_bytes()).hexdigest() != expected:
                failure = "Launcher changed after uninstall; it was preserved: " + str(launcher)
                break
            launcher.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            if time.monotonic() < deadline:
                time.sleep(.2)
                continue
            failure = "Could not remove launcher: " + str(error)
            break
        # A nonempty library can contain modified or user-owned files.
        try:
            library.rmdir()
        except OSError:
            pass
        break
finally:
    try:
        helper.unlink()
    except OSError as error:
        failure = (failure + "; " if failure else "") + "Could not remove cleanup helper: " + str(error)
    if failure:
        failure_log.write_text(failure, encoding="utf-8")
if failure:
    raise SystemExit(1)
'''
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(program)
    # A windowless process can outlive this parent without DETACHED_PROCESS.
    creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            [sys.executable, "-I", "-S", str(helper), str(launcher), str(library),
             str(helper), str(failure_log), expected, identity],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, creationflags=creation_flags,
        )
    except OSError:
        try:
            helper.unlink()
        except FileNotFoundError:
            pass
        raise


def perform_uninstall(args: argparse.Namespace) -> int:
    manifest, prefix, library, launcher = validated_uninstall_manifest()
    config = _normalized_path(_state.config_dir())
    print("HolyCrab uninstall preview:")
    print("\nRemove:")
    print(f"- Remove program: {library}")
    print(f"- Remove launcher: {launcher}")
    print("- Remove installer-managed HolyCrab MCP registrations and unchanged Skill files")
    print("\nPATH/profile:")
    registration = manifest.get("pathRegistration")
    if not isinstance(registration, dict):
        print("- Keep PATH/profile entry: this installation has no ownership record")
    elif registration.get("kind") == "none" and registration.get("addedByInstaller") is False:
        print("- PATH/profile entry: the installer did not add one")
    elif registration.get("addedByInstaller") is True and _bin_has_other_entries(prefix / "bin", launcher):
        print(f"- Keep PATH/profile entry: {prefix / 'bin'} contains other programs")
    elif registration.get("addedByInstaller") is True:
        print(f"- Remove installer-managed PATH/profile entry for: {prefix / 'bin'}")
    else:
        print("- Keep PATH/profile entry: the installer did not add it")
    print("\nLocal data:")
    if args.purge:
        print(f"- Purge known local credentials and records: {config}")
    else:
        print(f"- Local credentials and records will be preserved: {config}")
    if not args.yes:
        if not sys.stdin.isatty():
            print("Nothing was removed. Re-run with --yes after reviewing this preview.", file=sys.stderr)
            return 2
        prompt = "Uninstall HolyCrab and purge known local data? [y/N] " if args.purge else "Uninstall HolyCrab and preserve local data? [y/N] "
        if _presentation.read_confirmation(prompt) not in {"y", "yes"}:
            print("Uninstall cancelled; nothing was removed.", file=sys.stderr)
            return 2

    if manifest.get("schemaVersion") == 3 and launcher.exists():
        expected_hash = _manifest_hashes(manifest).get(_normalized_path(launcher))
        if launcher.is_symlink() or not expected_hash or hashlib.sha256(launcher.read_bytes()).hexdigest() != expected_hash:
            print("The launcher was modified or has no valid ownership hash; nothing was removed.", file=sys.stderr)
            return 1

    args.cleanup_started = True
    _presentation.command_progress("Removing matching installer-managed HolyCrab MCP registrations...")
    expected_command = str(prefix / ("lib/holycrab/holycrab_cli.py" if os.name == "nt" else "bin/holycrab"))
    try:
        warnings = remove_managed_mcp_registrations(manifest, expected_command)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    _presentation.command_progress("Removing unchanged installer-managed Skill files...")
    warnings.extend(remove_managed_skill_files(manifest))
    _presentation.command_progress("Removing program files and eligible PATH/profile entries...")
    hard_failures: list[str] = []

    if os.name == "nt":  # pragma: no cover - Windows CI
        # Sources are not held open by Python. Retry other transient file locks
        # here, while the full ownership-aware deletion code is still loaded.
        # The background helper only handles the still-running batch launcher.
        try:
            if library.is_symlink():
                library.unlink()
            elif library.exists():
                warnings.extend(remove_windows_program_files(manifest, library))
        except OSError as error:
            hard_failures.append(f"Could not remove program directory {library}: {_presentation.sanitize_text_for_output(str(error))}")
        if not hard_failures:
            try:
                schedule_windows_program_cleanup(launcher, library)
            except (OSError, RuntimeError) as error:
                hard_failures.append(f"Could not schedule Windows program cleanup: {_presentation.sanitize_text_for_output(str(error))}")
        if not hard_failures:
            warnings.extend(remove_managed_path_registration(manifest, prefix, launcher))
    else:
        try:
            launcher.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            hard_failures.append(f"Could not remove launcher {launcher}: {_presentation.sanitize_text_for_output(str(error))}")
        try:
            if library.is_symlink():
                library.unlink()
            elif library.exists():
                warnings.extend(remove_managed_program_files(manifest, library))
        except OSError as error:
            hard_failures.append(f"Could not remove program directory {library}: {_presentation.sanitize_text_for_output(str(error))}")
        warnings.extend(remove_managed_path_registration(manifest, prefix, launcher))

    if hard_failures:
        for failure in hard_failures:
            print(f"Error: {failure}", file=sys.stderr)
        print("Local credentials and records were kept because program removal was incomplete.", file=sys.stderr)
        return 1
    if args.purge:
        _presentation.command_progress("Purging known local HolyCrab credentials and records; unknown files will be kept...")
        purge_warnings = purge_known_local_state()
        warnings.extend(purge_warnings)
        if any(message.startswith(("Could not remove", "Refused to purge")) for message in purge_warnings):
            for warning in warnings:
                print(f"Warning: {warning}")
            print("HolyCrab program files were removed, but local data purge was incomplete.", file=sys.stderr)
            return 1

    print("")
    if args.purge:
        print("Known local HolyCrab credentials and records were purged.")
        print(f"Revoke the API Key separately if it must stop working:\n  {_settings.PUBLIC_ACCOUNT_URL}")
    else:
        print(f"Local HolyCrab credentials and records were preserved at:\n  {config}")
    if warnings:
        print("\nNotes:")
    for warning in warnings:
        informational = warning.startswith("Info:") or warning == "PATH registration was kept because the installer did not add it" or (
            warning.startswith("PATH registration was kept because") and warning.endswith("contains other programs")
        )
        message = warning if warning.startswith("Info:") else f"{'Info' if informational else 'Warning'}: {warning}"
        print(_presentation.terminal_paragraph(message))
    pending = any(warning.startswith("MCP cleanup pending") for warning in warnings)
    if os.name == "nt":
        print("HolyCrab uninstall cleanup is scheduled; exit this process to finish program removal.")
        print("Normally completes within 10 seconds. Check the program paths if files remain.")
        if pending:
            print("Agent registration cleanup is incomplete; follow the pending cleanup instructions above.")
    elif pending:
        print("HolyCrab program files were uninstalled; Agent registration cleanup is incomplete. Follow the pending cleanup instructions above.")
    else:
        print("HolyCrab uninstall completed.")
    # Automation must not mistake an incomplete managed registration cleanup
    # for success. Preserved manual/user-owned entries remain informational.
    return 1 if pending else 0


def file_check(path: Path, expected: str | None = None,
               hash_cache: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "exists": path.is_file(), "readable": os.access(path, os.R_OK)}
    if result["exists"] and result["readable"] and expected:
        info = path.stat()
        key = str(path)
        cached = hash_cache.get(key) if isinstance(hash_cache, dict) else None
        if (
            isinstance(cached, dict)
            and cached.get("size") == info.st_size
            and cached.get("mtimeNs") == info.st_mtime_ns
            and cached.get("expected") == expected.lower()
            and isinstance(cached.get("sha256"), str)
        ):
            result["sha256"] = cached["sha256"]
        else:
            result["sha256"] = _uploads.sha256_stream(path)
            if isinstance(hash_cache, dict):
                hash_cache[key] = {"size": info.st_size, "mtimeNs": info.st_mtime_ns,
                                   "expected": expected.lower(), "sha256": result["sha256"]}
        result["hashMatches"] = result["sha256"] == expected.lower()
    return result


def local_health_report(*, online: bool = False) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    repairs: list[str] = []
    checks["python"] = {"version": platform.python_version(), "ok": sys.version_info >= (3, 10)}
    try:
        checks["capabilities"] = file_check(_capabilities.capabilities_path())
        _capabilities.load_capabilities()
        checks["capabilities"]["valid"] = True
    except (SystemExit, OSError, ValueError) as error:
        checks["capabilities"] = {"ok": False, "error": str(error)}
    qr = Path(str(_settings.RUNTIME_ROOT / "holycrab_cli.py")).resolve().parent / "vendor" / "segno-1.6.6-py3-none-any.whl"
    checks["qrDependency"] = file_check(qr)
    try:
        manifest = load_installation()
        manifest_error = None
    except SystemExit as error:
        manifest = None
        manifest_error = str(error)
    try:
        health_cache = _state.read_json_file(_state.health_state_path(), {})
        if not isinstance(health_cache, dict):
            health_cache = {}
    except SystemExit:
        health_cache = {}
    original_health_cache = json.dumps(health_cache, sort_keys=True)
    if manifest_error:
        checks["installation"] = {"present": True, "readable": False, "error": manifest_error}
        repairs.append(
            "irm https://holycrab.ai/cli/install.ps1 | iex" if os.name == "nt"
            else "curl -fsSL https://holycrab.ai/cli/install.sh | sh"
        )
    elif manifest is None:
        source_checkout = any((parent / ".git").exists() for parent in Path(str(_settings.RUNTIME_ROOT / "holycrab_cli.py")).resolve().parents)
        checks["installation"] = {"present": False, "sourceCheckout": source_checkout}
        if not source_checkout:
            repairs.append(
                "irm https://holycrab.ai/cli/install.ps1 | iex"
                if os.name == "nt" else
                "curl -fsSL https://holycrab.ai/cli/install.sh | sh"
            )
    else:
        core: dict[str, Any] = {}
        core_files = manifest.get("coreFiles", [])
        manifest_format_ok = (
            isinstance(manifest.get("prefix"), str)
            and isinstance(manifest.get("agents"), list)
            and isinstance(manifest.get("mcp"), bool)
            and isinstance(core_files, list)
            and len(core_files) >= 5
            and manifest.get("schemaVersion") in {None, 2, _settings.INSTALLATION_SCHEMA_VERSION}
            and manifest.get("managedBy") in {None, _settings.INSTALLATION_MANAGER}
        )
        if manifest.get("schemaVersion") == 3:
            required = {"holycrab_cli.py", "runtime-files.json", *runtime_package_files()}
            recorded = {row.get("path") for row in core_files if isinstance(row, dict)} if isinstance(core_files, list) else set()
            manifest_format_ok = manifest_format_ok and required.issubset(recorded)
        path_registration = manifest.get("pathRegistration")
        if path_registration is not None:
            manifest_format_ok = manifest_format_ok and (
                isinstance(path_registration, dict)
                and path_registration.get("kind") in {"none", "shell-profile", "windows-user-path"}
                and isinstance(path_registration.get("directory"), str)
                and isinstance(path_registration.get("addedByInstaller"), bool)
            )
        for item in core_files if isinstance(core_files, list) else []:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                candidate = Path(item["path"])
                if not candidate.is_absolute():
                    candidate = _state.installation_path().parent / candidate
                expected = item.get("sha256")
                if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
                    manifest_format_ok = False
                    expected = None
                core[item["path"]] = file_check(candidate, expected, health_cache)
        version_ok = manifest.get("version") == _settings.VERSION
        checks["installation"] = {"present": True, "readable": True, "formatValid": manifest_format_ok,
                                  "versionMatches": version_ok, "coreFiles": core}
        damaged = any(not row.get("exists") or row.get("hashMatches") is False for row in core.values())
        if not version_ok:
            repairs.append("holycrab update --yes")
        if damaged or not manifest_format_ok:
            repairs.append(
                "irm https://holycrab.ai/cli/install.ps1 | iex"
                if os.name == "nt" else
                "curl -fsSL https://holycrab.ai/cli/install.sh | sh"
            )
    if json.dumps(health_cache, sort_keys=True) != original_health_cache:
        try:
            _state.write_private_json(_state.health_state_path(), health_cache)
        except OSError:
            pass
    try:
        config = _state.load_config()
        checks["config"] = {"readable": True, "keyConfigured": bool(os.environ.get("HOLYCRAB_API_KEY") or config.get("apiKey"))}
    except SystemExit as error:
        checks["config"] = {"readable": False, "error": str(error)}
        repairs.append("holycrab setup")
        config = {}
    try:
        _state.load_attempts()
        checks["attempts"] = {"readable": True}
    except SystemExit as error:
        checks["attempts"] = {"readable": False, "error": str(error)}
        repairs.append("Preserve the attempts ledger and recover its records; never delete it to bypass duplicate protection")
    if online:
        checks["apiKey"] = {"ok": False, "configured": bool(os.environ.get("HOLYCRAB_API_KEY") or config.get("apiKey"))}
        if checks["apiKey"]["configured"]:
            try:
                status, response, account = _tasks.query_account()
                checks["apiKey"]["ok"] = account is not None
                checks["apiKey"]["httpStatus"] = status
            except (SystemExit, OSError, urllib.error.URLError, http.client.HTTPException) as error:
                checks["apiKey"]["error"] = _presentation.sanitize_text_for_output(str(error))
        if manifest is not None and manifest.get("mcp") is True:
            prefix = manifest.get("prefix")
            expected = str(
                Path(prefix) / ("lib/holycrab/holycrab_cli.py" if os.name == "nt" else "bin/holycrab")
            ) if isinstance(prefix, str) else ""
            registrations: dict[str, Any] = {}
            for agent in manifest.get("enabledAgents", manifest.get("agents", [])):
                if agent in {"codex", "claude"}:
                    registrations[agent] = mcp_registration_check(agent, expected)
                    if not registrations[agent]["ok"]:
                        repairs.append("irm https://holycrab.ai/cli/install.ps1 | iex" if os.name == "nt" else
                                       "curl -fsSL https://holycrab.ai/cli/install.sh | sh")
            checks["mcpRegistrations"] = registrations
    update = (check_for_update(force=True, timeout=30.0) if online else
              current_update_info(_effective_update_state().get("update", {"checkedAt": None, "latestVersion": _settings.VERSION,
                                                   "updateAvailable": False})))
    ok = checks["python"]["ok"] and checks.get("capabilities", {}).get("valid") is True
    ok = ok and checks["qrDependency"].get("exists") is True
    if manifest_error:
        ok = False
    elif manifest is None and not checks["installation"].get("sourceCheckout"):
        ok = False
    elif manifest is not None:
        ok = ok and checks["installation"].get("formatValid") is True
        ok = ok and checks["installation"].get("versionMatches") is True
        ok = ok and all(row.get("exists") and row.get("hashMatches") is not False for row in checks["installation"]["coreFiles"].values())
    runtime_ok = ok
    ok = ok and checks["attempts"].get("readable") is True and checks.get("config", {}).get("readable") is True
    if online:
        ok = ok and checks.get("apiKey", {}).get("ok") is True and not update.get("error")
        ok = ok and all(row.get("ok") is True for row in checks.get("mcpRegistrations", {}).values())
    return {"ok": ok, "runtimeOk": runtime_ok, "version": _settings.VERSION, "checks": checks, "update": update, "repairs": list(dict.fromkeys(repairs)),
            "onboarding": _presentation.onboarding_guidance(configured=bool(checks.get("config", {}).get("keyConfigured")),
                                               verified=checks.get("apiKey", {}).get("ok") is True,
                                               local_only=not online)}


def startup_maintenance(raw_args: list[str]) -> None:
    if raw_args and raw_args[0] in {"doctor", "uninstall"}:
        return
    try:
        report = local_health_report(online=False)
        for repair in report.get("repairs", []):
            print(f"HolyCrab local check needs attention. Repair: {repair}", file=sys.stderr)
        if raw_args and raw_args[0] == "update":
            return
        if raw_args[:2] == ["mcp", "serve"]:
            # MCP delivers its reminder in protocol results; do not consume a
            # CLI reminder on stderr that the host may never show to the user.
            check_for_update(force=False, timeout=_settings.UPDATE_CHECK_TIMEOUT)
            return
        notice = automatic_update_notice()
        if notice:
            print(notice, file=sys.stderr)
    except (Exception, SystemExit):
        # Maintenance can never block the requested business command.
        return


def mcp_points_to_cli(registration: dict[str, Any], expected_command: str) -> bool:
    command, arguments = registration.get("command"), registration.get("args")
    if not isinstance(command, str) or not isinstance(arguments, list):
        return False
    if not expected_command.lower().endswith("holycrab_cli.py"):
        return _normalized_path(command) == _normalized_path(expected_command) and arguments == ["mcp", "serve"]
    # Windows registers Python directly, including py.exe's optional -3 switch.
    executable_name = command.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if re.fullmatch(r"(?:py|python(?:3(?:\.\d+)?)?)(?:\.exe)?", executable_name) is None:
        return False
    remainder = arguments[1:] if arguments[:1] == ["-3"] else arguments
    return (len(remainder) == 5 and remainder[:2] == ["-X", "utf8"]
            and isinstance(remainder[2], str)
            and _normalized_path(remainder[2]) == _normalized_path(expected_command)
            and remainder[3:] == ["mcp", "serve"])



def mcp_registration_check(agent: str, expected_command: str) -> dict[str, Any]:
    manifest = load_installation() or {}
    executable = find_agent_client(agent, manifest)
    if executable is None:
        record = agent_registration(manifest, agent)
        if record is not None and record.get("managed") is False and not record.get("executable"):
            return {"selected": True, "installed": False, "ok": True, "status": "not-installed",
                    "instruction": f"{agent} is not installed. Its Skill is available; run the installer again after installing the client to connect MCP."}
        return {"selected": True, "installed": False, "ok": False}
    try:
        registration, _, exit_code = inspect_agent_registration(executable, agent)
    except (OSError, subprocess.SubprocessError) as error:
        return {"selected": True, "installed": True, "ok": False,
                "error": _presentation.sanitize_text_for_output(str(error))}
    matches = registration is not None and registration.get("scope") == "user" and mcp_points_to_cli(registration, expected_command)
    return {"selected": True, "installed": True, "ok": matches,
            "exitCode": exit_code}



@contextmanager
def installation_lock(prefix: Path):
    directory = prefix / ".holycrab-install.lock"
    try:
        directory.mkdir()
    except FileExistsError:
        raise SystemExit(f"Another lifecycle operation holds {directory}. Check it before recovering an abandoned lock.") from None
    try:
        yield
    finally:
        directory.rmdir()
