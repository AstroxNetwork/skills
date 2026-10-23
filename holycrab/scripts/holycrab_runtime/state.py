"""HolyCrab state responsibilities."""
from __future__ import annotations
from . import results as _results

import base64
import json
import os
import re
import stat
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None
try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX runtime
    msvcrt = None
from . import settings as _settings

def config_dir() -> Path:
    override = os.environ.get("HOLYCRAB_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return Path(xdg).expanduser() / "holycrab" if xdg else Path.home() / ".config" / "holycrab"


def config_path() -> Path:
    return config_dir() / "config.json"


def attempts_path() -> Path:
    return config_dir() / "attempts.json"


def attempts_lock_path() -> Path:
    return config_dir() / "attempts.lock"


def update_state_path() -> Path:
    return config_dir() / "update-state.json"


def health_state_path() -> Path:
    return config_dir() / "health-state.json"


def upload_plans_dir() -> Path:
    return config_dir() / "upload-plans"


def installation_path() -> Path:
    return Path(str(_settings.RUNTIME_ROOT / "holycrab_cli.py")).resolve().parent / "installation.json"


def _dpapi_protect(value: str) -> str:  # pragma: no cover - exercised on Windows CI
    if os.name != "nt":
        raise OSError("DPAPI is only available on Windows")
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    raw = value.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    source = DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    destination = DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), "HolyCrab API Key", None, None, None, 0, ctypes.byref(destination)
    ):
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(destination.pbData, destination.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(destination.pbData)


def _dpapi_unprotect(value: str) -> str:  # pragma: no cover - exercised on Windows CI
    if os.name != "nt":
        raise OSError("DPAPI is only available on Windows")
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    raw = base64.b64decode(value, validate=True)
    buffer = ctypes.create_string_buffer(raw)
    source = DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    destination = DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(destination)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(destination.pbData, destination.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(destination.pbData)


def read_json_file(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Could not read {path}: {error}") from error


def write_private_json(path: Path, value: Any) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def load_config(*, decrypt: bool = True) -> dict[str, Any]:
    value = read_json_file(config_path(), {})
    if not isinstance(value, dict):
        raise SystemExit("HolyCrab config must contain a JSON object")
    path = config_path()
    if path.exists() and os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise SystemExit(f"Unsafe permissions on {path}; run chmod 600 {path}")
    if os.name == "nt" and decrypt:  # pragma: no cover - exercised on Windows CI
        encrypted = value.get("apiKeyDpapi")
        if isinstance(encrypted, str) and encrypted:
            try:
                value["apiKey"] = _dpapi_unprotect(encrypted)
            except (OSError, ValueError, UnicodeError) as error:
                raise SystemExit("Could not decrypt the HolyCrab API Key for this Windows user") from error
        elif isinstance(value.get("apiKey"), str) and value["apiKey"].strip():
            plaintext = value["apiKey"].strip()
            migrated = {key: item for key, item in value.items() if key != "apiKey"}
            migrated["apiKeyDpapi"] = _dpapi_protect(plaintext)
            write_private_json(path, migrated)
            value = {**migrated, "apiKey": plaintext}
    return value


def save_config(value: dict[str, Any]) -> None:
    stored = dict(value)
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        plaintext = stored.pop("apiKey", None)
        if isinstance(plaintext, str) and plaintext.strip():
            stored["apiKeyDpapi"] = _dpapi_protect(plaintext.strip())
        elif plaintext is not None:
            stored.pop("apiKeyDpapi", None)
    write_private_json(config_path(), stored)


def load_attempts() -> dict[str, Any]:
    path = attempts_path()
    if path.exists() and os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise SystemExit(f"Unsafe permissions on {path}; run chmod 600 {path}")
    value = read_json_file(path, {})
    if not isinstance(value, dict):
        raise SystemExit("HolyCrab attempts ledger must contain a JSON object")
    clean: dict[str, Any] = {}
    for attempt_id, record in value.items():
        if not isinstance(attempt_id, str):
            continue
        if not isinstance(record, dict):
            record = {"state": "unknown", "note": "Unreadable reserved record; do not resubmit."}
        record = {**record, "attemptId": attempt_id}
        state = record.get("state")
        if state not in _settings.ATTEMPT_STATES:
            record = {**record, "state": "unknown", "note": "Recovered an invalid local state; do not resubmit."}
        clean[attempt_id] = record
    return clean


def save_attempts(value: dict[str, Any]) -> None:
    write_private_json(attempts_path(), value)


@contextmanager
def file_lock(path: Path, *, blocking: bool = True, shared: bool = False) -> Any:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    acquired = False
    try:
        os.chmod(path, 0o600)
        if fcntl is not None:
            fcntl.flock(descriptor, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | (0 if blocking else fcntl.LOCK_NB))
        elif msvcrt is not None:  # pragma: no cover - Windows fallback
            import ctypes
            import errno
            from ctypes import wintypes
            class Overlapped(ctypes.Structure):
                _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                            ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                         wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
            kernel.LockFileEx.restype = wintypes.BOOL
            kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                           wintypes.DWORD, ctypes.POINTER(Overlapped)]
            kernel.UnlockFileEx.restype = wintypes.BOOL
            handle, overlapped = msvcrt.get_osfhandle(descriptor), Overlapped()
            if not kernel.LockFileEx(handle, (0 if shared else 2) | (0 if blocking else 1), 0, 1, 0, ctypes.byref(overlapped)):
                error = ctypes.get_last_error()
                if error == 33:
                    raise BlockingIOError(errno.EWOULDBLOCK, "Another HolyCrab operation holds this lock")
                raise ctypes.WinError(error)
        acquired = True
        yield
    finally:
        if acquired and fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        elif acquired and msvcrt is not None:  # pragma: no cover - Windows fallback
            kernel.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlapped))
        os.close(descriptor)


def attempts_lock() -> Any:
    return file_lock(attempts_lock_path())


def operation_lock(*, exclusive: bool = False, blocking: bool = True) -> Any:
    """Stable namespace outside purged state: mutations share it; purge excludes them."""
    import hashlib
    root = config_dir().expanduser().resolve()
    identity = hashlib.sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()[:24]
    return file_lock(root.parent / (".holycrab-operations-" + identity + ".lock"),
                     blocking=blocking, shared=not exclusive)


def protect_operation(function):
    from functools import wraps
    @wraps(function)
    def protected(*args, **kwargs):
        with operation_lock():
            return function(*args, **kwargs)
    return protected


def credential(explicit: str | None = None) -> tuple[str, str]:
    value = explicit or os.environ.get("HOLYCRAB_API_KEY") or load_config().get("apiKey")
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(
            "HolyCrab API Key is not configured. Run `holycrab setup` and paste a Key from "
            + _settings.PUBLIC_ACCOUNT_URL
        )
    resolved = validate_api_key(value)
    if len(resolved) >= 8:
        _settings.SENSITIVE_OUTPUT_VALUES.add(resolved)
    return "X-User-Token", resolved


def validate_api_key(value: str) -> str:
    resolved = value.strip()
    if not resolved or any(ord(character) < 33 or ord(character) > 126 for character in resolved):
        raise SystemExit("API Key contains invalid characters. Copy one complete Key and configure it again; do not share it in chat.")
    return resolved


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def update_attempt(attempt_id: str, **changes: Any) -> None:
    attempt_id = local_attempt_id(attempt_id)
    with attempts_lock():
        attempts = load_attempts()
        record = attempts.get(attempt_id, {})
        record.update(changes)
        record["updatedAt"] = utc_now()
        attempts[attempt_id] = record
        save_attempts(attempts)


def local_attempt_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("attemptId must contain 1-128 letters, digits, underscores, or hyphens")
    return value


def attempt_record(attempt_id: str) -> dict[str, Any]:
    identifier = local_attempt_id(attempt_id)
    with attempts_lock():
        record = load_attempts().get(identifier)
    if not isinstance(record, dict):
        raise SystemExit(f"Generation attempt not found: {identifier}")
    return {**record, "nextAction": _results.attempt_next_action(record)}


def attempt_records() -> list[dict[str, Any]]:
    with attempts_lock():
        records = [{**value, "nextAction": _results.attempt_next_action(value)}
                   for value in load_attempts().values() if isinstance(value, dict)]
    return sorted(records, key=lambda item: str(item.get("createdAt", "")), reverse=True)


def attempt_exists(attempt_id: str) -> bool:
    attempt_id = local_attempt_id(attempt_id)
    with attempts_lock():
        return attempt_id in load_attempts()


def reserve_attempt(attempt_id: str, record: dict[str, Any]) -> None:
    attempt_id = local_attempt_id(attempt_id)
    with attempts_lock():
        attempts = load_attempts()
        if attempt_id in attempts:
            raise SystemExit(
                f"Local submission attempt {attempt_id} already exists; query it instead of submitting again"
            )
        attempts[attempt_id] = {**record, "updatedAt": utc_now()}
        save_attempts(attempts)
