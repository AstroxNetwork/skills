#!/usr/bin/env python3
"""HolyCrab CLI and local stdio MCP server with a bundled offline QR encoder."""

from __future__ import annotations

import argparse
import base64
import errno
import getpass
import hashlib
import html
import http.client
import importlib.util
import io
import ipaddress
import json
import math
import os
import platform
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX runtime
    msvcrt = None


VERSION = "0.4.3"
DEFAULT_BASE_URL = "https://abgzfc.holycrab.ai"
PUBLIC_ACCOUNT_URL = "https://generate.holycrab.ai/user-tokens"
REAL_HUMAN_CALLBACK_URL = "https://generate.holycrab.ai/real-human-authorization/callback"
AUTHORIZATION_TERMINAL = {"SUCCEEDED", "FAILED", "EXPIRED"}
ATTEMPT_STATES = {"prepared", "submitting", "created", "unknown", "failed"}
UPDATE_CHECK_SECONDS = 24 * 60 * 60
UPDATE_CHECK_TIMEOUT = 2.0
UPDATE_API_URL = "https://api.github.com/repos/AstroxNetwork/skills/releases/latest"
RELEASE_PAGE_PREFIX = "https://github.com/AstroxNetwork/skills/releases/tag/"
MAX_DOWNLOAD_REDIRECTS = 5
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_DOWNLOAD_RESPONSE_BYTES = 2 * 1024 * 1024 * 1024
MAX_INSTALLER_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_FILES = 10
UPLOAD_PLAN_SECONDS = 30 * 60
MAX_IMAGE_UPLOAD_BYTES = 30 * 1024 * 1024
MAX_VIDEO_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_REAL_HUMAN_VIDEO_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_AUDIO_UPLOAD_BYTES = 15 * 1024 * 1024
INSTALLATION_SCHEMA_VERSION = 2
INSTALLATION_MANAGER = "holycrab-installer"
MANAGED_SKILL_FILES = (
    "SKILL.md",
    "references/capabilities.json",
    "agents/openai.yaml",
)
GENERATION_ROUTES = {
    "seedanceVideo": ("/api/tasks/generation/freeze-credit", "/api/tasks/generation"),
    "minimaxVideo": (
        "/api/tasks/minimax-generation/freeze-credit",
        "/api/tasks/minimax-generation",
    ),
    "imageGeneration": (
        "/api/tasks/image-generation/freeze-credit",
        "/api/tasks/image-generation",
    ),
    "audioGeneration": (
        "/api/tasks/audio-generation/freeze-credit",
        "/api/tasks/audio-generation",
    ),
}
LATEST_INITIALIZE_PROTOCOL = "2025-11-25"
SUPPORTED_INITIALIZE_PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
SENSITIVE_OUTPUT_KEYS = {
    "presignedurl", "authorization", "xusertoken", "apikey", "accesstoken", "refreshtoken",
    "bytedtoken", "arkgroupid", "arkassetid", "arkaccountid", "objectkey", "ak", "sk",
}
SENSITIVE_OUTPUT_VALUES: set[str] = set()
SENSITIVE_URL_QUERY_KEYS = {
    "bytedtoken", "pl",
    "signature",
    "sig",
    "token",
    "accesstoken",
    "credential",
    "securitytoken",
    "policy",
    "googleaccessid",
    "ossaccesskeyid",
    "awsaccesskeyid",
    "xamzsignature",
    "xamzcredential",
    "xamzsecuritytoken",
    "xtossignature",
    "xtoscredential",
    "xtossecuritytoken",
    "xgoogsignature",
    "xgoogcredential",
}
URL_IN_TEXT_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
PRIVATE_FIELD_IN_TEXT = re.compile(
    r'''(?i)((?:byted[_-]?token|ark[_-]?(?:group|asset|account)[_-]?id|api[_-]?key|object[_-]?key)\s*["']?\s*[:=]\s*["']?)([^\s,;"'&<>}]+)'''
)
PUBLIC_ACCOUNT_FIELDS = ("username", "nickname", "credit")
PUBLIC_TASK_FIELDS = (
    "uniqId",
    "taskId",
    "taskType",
    "model",
    "step",
    "status",
    "progress",
    "error",
    "frozenCredit",
    "duration",
    "resolution",
    "ratio",
    "generateAudio",
    "videoUrl",
    "imageUrls",
    "audioIds",
    "audioUrls",
    "videoIds",
    "imageIds",
    "cdnUrl",
    "textResult",
    "createTime",
    "updateTime",
)


class CommandInterrupted(Exception):
    """A stopped operation whose known outcome must still be reported safely."""

    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__("Stopped by user; review the operation result before taking another action.")
        self.result = result


class CommandUsageError(ValueError):
    """A locally rejected argument, before any operation was attempted."""


def command_progress(message: str) -> None:
    if sys.stderr.isatty():
        print(terminal_paragraph(message), file=sys.stderr, flush=True)


def terminal_paragraph(message: str, *, indent: str = "") -> str:
    """Wrap explanations, never split a copyable command, URL or path."""
    message = terminal_safe_text(sanitize_text_for_output(message))
    lines = []
    for line in message.split("\n"):
        if line.lstrip().startswith(("Run: ", "holycrab ", "codex ", "claude ",
                                     "unset ", "export ", "Remove-Item ", "Path: ", "Release: ",
                                     "https://", "Private link: ", "QR image: ")):
            lines.append(indent + line)
        else:
            lines.append(textwrap.fill(line, width=78, initial_indent=indent,
                                       subsequent_indent=indent, break_long_words=False,
                                       break_on_hyphens=False) if line else "")
    return "\n".join(lines)


def terminal_safe_text(value: Any) -> str:
    text = re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))", "", str(value))
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)


def clear_environment_command(name: str) -> str:
    if not re.fullmatch(r"HOLYCRAB_[A-Z_]+", name):
        raise ValueError("Unsupported environment variable")
    return f"Remove-Item Env:{name} -ErrorAction SilentlyContinue" if os.name == "nt" else f"unset {name}"


def read_confirmation(prompt: str) -> str:
    print(prompt, end="", file=sys.stderr, flush=True)
    return input().strip().lower()


def validate_wait_limits(timeout: float, interval: float) -> None:
    if not math.isfinite(timeout) or timeout < 0 or not math.isfinite(interval) or interval <= 0:
        raise CommandUsageError("timeout must be finite and nonnegative; interval must be finite and positive")


def task_list_query(page: int, page_size: int, start: str | None, end: str | None,
                    task_type: str | None = None) -> list[tuple[str, str]]:
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 100:
        raise CommandUsageError("page must be positive and pageSize must be between 1 and 100")
    if bool(start) != bool(end):
        raise CommandUsageError("--start-date and --end-date must be supplied together")
    if start and end:
        for value in (start, end):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise CommandUsageError("Dates must use YYYY-MM-DD")
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError as error:
                raise CommandUsageError("Dates must use valid YYYY-MM-DD calendar dates") from error
        if start > end:
            raise CommandUsageError("start date must not be after end date")
    query = [("page", str(page)), ("pageSize", str(page_size))]
    if start and end:
        query.extend((("startDate", start), ("endDate", end)))
    if task_type:
        if task_type not in {"IMAGE", "VIDEO", "AUDIO", "TEXT"}:
            raise CommandUsageError("taskType must be IMAGE, VIDEO, AUDIO, or TEXT")
        query.append(("taskType", task_type))
    return query


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
    return Path(__file__).resolve().parent / "installation.json"


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
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def load_config() -> dict[str, Any]:
    value = read_json_file(config_path(), {})
    if not isinstance(value, dict):
        raise SystemExit("HolyCrab config must contain a JSON object")
    path = config_path()
    if path.exists() and os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise SystemExit(f"Unsafe permissions on {path}; run chmod 600 {path}")
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
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
        if not isinstance(attempt_id, str) or not isinstance(record, dict):
            continue
        record = {**record, "attemptId": attempt_id}
        state = record.get("state")
        if state not in ATTEMPT_STATES:
            record = {**record, "state": "unknown", "note": "Recovered an invalid local state; do not resubmit."}
        clean[attempt_id] = record
    if clean != value:
        save_attempts(clean)
    return clean


def save_attempts(value: dict[str, Any]) -> None:
    write_private_json(attempts_path(), value)


@contextmanager
def attempts_lock() -> Any:
    path = attempts_lock_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.chmod(path, 0o600)
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        elif msvcrt is not None:  # pragma: no cover - Windows fallback
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        elif msvcrt is not None:  # pragma: no cover - Windows fallback
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        os.close(descriptor)


def credential(explicit: str | None = None) -> tuple[str, str]:
    value = explicit or os.environ.get("HOLYCRAB_API_KEY") or load_config().get("apiKey")
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(
            "HolyCrab API Key is not configured. Run `holycrab setup` and paste a Key from "
            + PUBLIC_ACCOUNT_URL
        )
    resolved = value.strip()
    if len(resolved) >= 8:
        SENSITIVE_OUTPUT_VALUES.add(resolved)
    return "X-User-Token", resolved


def base_url() -> str:
    if "HOLYCRAB_BASE_URL" in os.environ:
        raise SystemExit(
            "Custom API origins are not supported. Run `" + clear_environment_command("HOLYCRAB_BASE_URL") + "` and try again."
        )
    return DEFAULT_BASE_URL


def url_origin(url: str) -> tuple[str, str, int]:
    parsed = urllib.parse.urlsplit(url)
    if not parsed.hostname:
        raise ValueError("URL has no hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL contains embedded credentials")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"URL has an invalid port: {error}") from error
    scheme = parsed.scheme.lower()
    return scheme, parsed.hostname.lower(), port if port is not None else 443 if scheme == "https" else 80


class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, trusted_origin: str) -> None:
        super().__init__()
        self.trusted_origin = url_origin(trusted_origin)

    def redirect_request(
        self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> urllib.request.Request | None:
        try:
            redirect_origin = url_origin(newurl)
        except ValueError as error:
            raise urllib.error.HTTPError(newurl, code, f"Cross-origin redirect blocked: {error}", headers, fp)
        if redirect_origin != self.trusted_origin:
            raise urllib.error.HTTPError(newurl, code, "Cross-origin redirect blocked", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        # A redirect could move a signed PUT to an attacker-controlled origin.
        # Never include either signed URL in the error message.
        raise urllib.error.HTTPError("https://upload.invalid/", code, "upload redirect blocked", headers, fp)


def open_presigned_upload(request: urllib.request.Request) -> Any:
    return urllib.request.build_opener(RejectRedirectHandler()).open(request, timeout=300)


def validate_presigned_upload_url(value: Any) -> str:
    if not isinstance(value, str):
        raise SystemExit("Presigned upload URL must be a credential-free HTTPS URL")
    parsed = urllib.parse.urlsplit(value)
    try:
        parsed.port
    except ValueError as error:
        raise SystemExit(f"Presigned upload URL must be a credential-free HTTPS URL: {error}") from error
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise SystemExit("Presigned upload URL must be a credential-free HTTPS URL")
    return value


def validate_verification_link(value: Any) -> str:
    if not isinstance(value, str) or any(character.isspace() for character in value):
        raise ValueError("invalid verification link")
    parsed = urllib.parse.urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("invalid verification link") from error
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != "www.byteplus.com"
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/en/liveness-face-manage/authorization"
        or parsed.fragment
    ):
        raise ValueError("invalid verification link")
    return value


def normalized_name(value: Any) -> str:
    return "".join(character for character in str(value).lower() if character.isalnum())


def is_signed_url(value: str) -> bool:
    parsed = urllib.parse.urlsplit(html.unescape(value))
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or not parsed.query:
        return False
    return any(
        normalized_name(key) in SENSITIVE_URL_QUERY_KEYS
        for key, _ in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    )


def sanitize_text_for_output(value: str) -> str:
    def replace_url(match: re.Match[str]) -> str:
        candidate = match.group(0)
        return "<redacted-signed-url>" if is_signed_url(candidate) else candidate

    sanitized = URL_IN_TEXT_PATTERN.sub(replace_url, value)
    sanitized = PRIVATE_FIELD_IN_TEXT.sub(r"\1<redacted>", sanitized)
    for secret in sorted(SENSITIVE_OUTPUT_VALUES, key=len, reverse=True):
        sanitized = sanitized.replace(secret, "<redacted-secret>")
    return sanitized


def sanitize_for_output(value: Any) -> Any:
    if isinstance(value, dict):
        output: dict[Any, Any] = {}
        for key, item in value.items():
            normalized = normalized_name(key)
            # Only the locally constructed start result may disclose its one-time
            # verification link. An arbitrary API response cannot opt into this.
            if isinstance(value, AuthorizationStartResult) and key == "h5Link":
                output[key] = item
            else:
                output[key] = "<redacted>" if normalized in SENSITIVE_OUTPUT_KEYS else sanitize_for_output(item)
        return output
    if isinstance(value, list):
        return [sanitize_for_output(item) for item in value]
    if isinstance(value, str):
        return sanitize_text_for_output(value)
    return value


def decode_json(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def encode_multipart_form(
    fields: dict[str, Any], *, boundary: str | None = None
) -> tuple[bytes, str]:
    resolved_boundary = boundary or f"HolyCrab{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        if value is None:
            continue
        if any(character in name for character in ('"', "\r", "\n")):
            raise ValueError("Multipart field name contains an unsafe character")
        parts.extend(
            (
                f"--{resolved_boundary}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"),
                str(value).encode("utf-8"),
                b"\r\n",
            )
        )
    parts.append(f"--{resolved_boundary}--\r\n".encode("ascii"))
    return b"".join(parts), f"multipart/form-data; boundary={resolved_boundary}"


def send(
    method: str,
    path: str,
    *,
    query: list[tuple[str, str]] | None = None,
    payload: Any = None,
    form: dict[str, Any] | None = None,
    api_key: str | None = None,
) -> tuple[int, Any]:
    if not path.startswith("/") or path.startswith("//"):
        raise SystemExit("API path must start with one / character")
    trusted_base = base_url()
    url = trusted_base + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    header_name, header_value = credential(api_key)
    headers = {
        header_name: header_value,
        "Accept": "application/json",
        "User-Agent": f"holycrab-cli/{VERSION}",
    }
    if payload is not None and form is not None:
        raise ValueError("Use either payload or form, not both")
    body: bytes | None = None
    if form is not None:
        body, headers["Content-Type"] = encode_multipart_form(form)
    elif payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
    try:
        opener = urllib.request.build_opener(SameOriginRedirectHandler(trusted_base))
        with opener.open(request, timeout=60) as response:
            return response.status, decode_json(response.read())
    except urllib.error.HTTPError as error:
        return error.code, decode_json(error.read())


def response_ok(status: int, response: Any) -> bool:
    return 200 <= status < 300 and (
        not isinstance(response, dict) or response.get("code", 0) in (0, 200)
    )


def response_data(status: int, response: Any) -> Any:
    remember_private_fields(response)
    if not response_ok(status, response):
        message = response.get("message") if isinstance(response, dict) else None
        request_id = response.get("requestId") if isinstance(response, dict) else None
        detail = f": {sanitize_text_for_output(str(message))}" if message else ""
        if request_id:
            detail += f" (requestId: {sanitize_text_for_output(str(request_id))})"
        raise SystemExit(f"HolyCrab API request failed with HTTP {status}{detail}")
    return response.get("data") if isinstance(response, dict) and "data" in response else response


def public_envelope(response: Any, projector: Any) -> Any:
    if not isinstance(response, dict):
        return response
    output = {
        key: response[key]
        for key in ("code", "message", "errorCode", "requestId")
        if key in response
    }
    if "data" in response:
        output["data"] = projector(response.get("data"))
    return output


def public_account_data(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {key: value[key] for key in PUBLIC_ACCOUNT_FIELDS if key in value}


def valid_account_payload(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    named = any(isinstance(value.get(key), str) and value[key].strip() for key in ("username", "nickname"))
    credit = value.get("credit")
    return bool(named or (isinstance(credit, (int, float)) and not isinstance(credit, bool) and math.isfinite(credit)))


ONBOARDING_EXAMPLES = (
    {"code": "PRODUCT_IMAGES", "title": "Product images",
     "prompt": "Turn these product photos into clean e-commerce listing images that highlight the product's selling points."},
    {"code": "SOCIAL_AD_VIDEO", "title": "Social media ad video",
     "prompt": "Use these product images to make a social media ad video showing how the product is used."},
    {"code": "PRODUCT_VOICEOVER", "title": "Product voiceover",
     "prompt": "Turn this product introduction into a natural, clear Chinese voiceover for a promotional video."},
)
ONBOARDING_AGENT_INSTRUCTION = (
    "Use short headings and bullet lists for user-facing guidance; never show internal instructions or raw JSON to beginners. "
    "During installation or the first account connection, use the onboarding state to guide setup or verify the active account. "
    "Only after verification, introduce the business uses and translate the three examples into the user's current language. "
    "Give the full introduction once in that installation conversation, then ask what the user wants to work on first. "
    "Do not repeat it for ordinary queries, reconnections, or updates. The examples are suggestions, not permission to execute. "
    "Ask for the user's goal, selected materials, and desired result; check actual capabilities before proposing a workflow. "
    "Keep credit estimates in the execution workflow, not in the example prompts. Confirm uploads and paid generation separately. "
    "Real-person authorization does not create assets; continue with file selection, preview, and upload confirmation. "
    "If the Agent must reload its tools, explain how to restart it; do not restart it yourself."
)


def onboarding_guidance(*, configured: bool, verified: bool = False, environment_override: bool = False) -> dict[str, Any]:
    if not configured:
        state, instruction, command = "CONNECT_ACCOUNT", "Connect your HolyCrab account locally. Never send your API Key in chat.", "holycrab setup"
    elif environment_override:
        state, instruction, command = (
            "VERIFY_ACCOUNT", "HOLYCRAB_API_KEY overrides the saved login. Clear the override, then check the active account.",
            clear_environment_command("HOLYCRAB_API_KEY") + ("; " if os.name == "nt" else " && ") + "holycrab auth status",
        )
    elif not verified:
        state, instruction, command = "VERIFY_ACCOUNT", "A Key is configured, but the active account has not been verified. Check it before starting work.", "holycrab auth status"
    else:
        state, instruction, command = "READY", "Your HolyCrab account is connected. Return to Codex or Claude Code and describe what you want to create.", None
    return {
        "state": state, "instruction": instruction, "command": command,
        "businessUses": ["E-commerce product images", "Social media advertising videos", "Product voiceovers",
                         "Upload selected local media", "Authorize a real person and upload their media",
                         "Check tasks and download results"],
        "examples": [dict(example) for example in ONBOARDING_EXAMPLES],
        "question": "What would you like to work on first?",
        "agentInstruction": ONBOARDING_AGENT_INSTRUCTION,
    }


def format_onboarding(guidance: dict[str, Any], *, installed: bool = False,
                      introduce: bool = False, upgrading: bool = False) -> str:
    """One user-facing summary shared by both installers and account commands."""
    lines = [f"HolyCrab {VERSION} installed", ""] if installed else []
    state = guidance.get("state")
    if state == "READY":
        if not installed:
            lines += ["HolyCrab account connected", ""]
        lines += ["Next: return to Codex or Claude Code."]
    else:
        if state == "CONNECT_ACCOUNT":
            title = "Next: connect your account"
        elif "overrides" in str(guidance.get("instruction", "")):
            title = "Next: clear HOLYCRAB_API_KEY, then verify your account"
        else:
            title = "Next: verify your account"
        if not installed and state == "VERIFY_ACCOUNT":
            lines += ["Account not verified", ""]
        lines += [title]
        if guidance.get("command"):
            # Commands stay on one line, including the PowerShell override fix.
            lines += ["  " + str(guidance["command"])]
    if not upgrading and (installed or (state == "READY" and introduce)):
        title = "Ask Codex or Claude Code to:" if state == "READY" else "After verification, ask Codex or Claude Code to:"
        lines += ["", title, "  - Create product listing images",
                  "  - Make advertising videos", "  - Produce product voiceovers"]
        if state == "READY":
            lines += ["", str(guidance["question"])]
    if installed or state != "READY":
        lines += ["", "Do not share your API Key in chat."]
    return "\n".join(lines)


def show_onboarding(guidance: dict[str, Any], *, introduce: bool = False) -> None:
    command_progress("\n" + format_onboarding(guidance, introduce=introduce))


def public_account_response(response: Any) -> Any:
    return public_envelope(response, public_account_data)


def public_task_data(value: Any) -> Any:
    if isinstance(value, list):
        return [public_task_data(item) for item in value]
    if not isinstance(value, dict):
        return value
    if isinstance(value.get("records"), list):
        output = {
            key: value[key]
            for key in ("total", "size", "current", "pages")
            if key in value
        }
        output["records"] = [public_task_data(item) for item in value["records"]]
        return output
    output = {key: value[key] for key in PUBLIC_TASK_FIELDS if key in value}
    output["audioUrls"] = parse_audio_urls(value.get("audioIds"))
    identifier = value.get("uniqId") or value.get("taskId")
    if isinstance(identifier, str) and re.fullmatch(r"[A-Za-z0-9]{1,64}", identifier):
        output["nextAction"] = task_next_action(identifier, value.get("step"), output)
    return output


def parse_audio_urls(value: Any) -> list[str]:
    parsed = value
    if isinstance(value, str):
        if not value.strip():
            return []
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(parsed, list):
        return []
    return [item.strip() for item in parsed if isinstance(item, str) and item.strip().startswith("https://")]


def public_task_response(response: Any) -> Any:
    return public_envelope(response, public_task_data)


def print_json(value: Any) -> None:
    print(json.dumps(sanitize_for_output(value), ensure_ascii=False, indent=2))


TERMINAL_ACTION_COPY = {
    "WAIT_FOR_AUTHORIZATION": "Have the person complete verification and confirm on their phone.\nKeep this authorization; do not create another one.",
    "UPLOAD_ASSETS": "No materials were created by this authorization.\nSelect this person's files, preview them, and confirm an upload.\nPaid generation needs separate confirmation.",
    "SELECT_FILES_TO_UPLOAD": "No materials have been uploaded to this person group.\nSelect this person's files, preview them, and confirm an upload.",
    "QUERY_RECENT_TASKS": "Do not submit this attempt again.\nNo matching result does not prove that no task was created.",
    "REVIEW_ESTIMATE": "Review the request and cost before confirming a paid task.",
    "WAIT_FOR_TASK": "Wait for this same task. Do not submit it again.",
    "WAIT_FOR_ASSET": "Upload registered; processing is still running.\nDo not upload the file again.",
    "WAIT_FOR_ASSETS": "Uploads registered; wait until all materials are ready.",
    "CONTINUE_QUERYING": "Continue checking the same ID. Nothing was resubmitted.",
    "ASK_BEFORE_AUTHORIZATION": "No authorized people were found.\nAsk Codex to start authorization for the intended person.",
    "ASK_BEFORE_NEW_AUTHORIZATION": "Ask before starting a new authorization.",
    "ESTIMATE_GENERATION": "Tell Codex how you want to use this material.\nReview the proposed work and cost before confirming generation.",
    "CHOOSE_DOWNLOAD_DESTINATION": "Choose where to save the completed result.",
    "REVIEW_TASK_FAILURE": "Review the error before deciding whether to create a new task.",
    "DO_NOT_REUPLOAD_AUTOMATICALLY": "Review the processing error and choose what to do next.\nDo not upload the file again automatically.",
    "QUERY_ASSET": "Keep this material ID and check its status.\nDo not upload the file again.",
    "QUERY_GROUP_ASSETS": "Check this person's materials before continuing.\nDo not upload these files again automatically.",
    "REVIEW_ASSETS": "Review existing materials before continuing.\nDo not upload these files again automatically.",
    "QUERY_EXISTING_RECORDS": "Review existing records before continuing.\nDo not repeat this operation automatically.",
    "QUERY_TASK": "Check this same task. Do not submit it again.",
    "FIX_REQUEST": "Correct the rejected request, then confirm a new task.",
    "CHECK_INSTALLATION": "Check local health; reinstall if the command no longer works.\nRollback has not been confirmed.",
    "CHECK_LOCAL_CLEANUP": "Cleanup may be incomplete. Check local health or reinstall.\nSome files or registrations may already have been removed.",
}


def terminal_next_action(action: Any) -> list[str]:
    if not isinstance(action, dict):
        return []
    instruction = TERMINAL_ACTION_COPY.get(action.get("code"), action.get("instruction"))
    lines = ["", "Next:"]
    if instruction:
        lines += terminal_paragraph(str(instruction), indent="  ").splitlines()
    if isinstance(action.get("command"), str) and action["command"]:
        lines += ["  " + action["command"]]
    return lines


def terminal_scalar(value: Any) -> str:
    if value is None:
        return "Not provided"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    # Preserve spaces inside copyable paths; remove terminal controls, not data.
    return terminal_safe_text(value).replace("\n", " ").replace("\t", " ")


def terminal_label(key: str) -> str:
    labels = {"uniqId": "ID", "taskId": "Task ID", "attemptId": "Attempt ID",
              "authorizationId": "Authorization ID", "groupUniqId": "Person group ID",
              "assetUniqId": "Material ID", "frozenCredit": "Credits", "textResult": "Text",
              "size": "Size", "bytes": "Bytes", "path": "Path", "step": "Status",
              "id": "ID", "output": "Path", "file": "Path", "h5Link": "Private link",
              "qrPath": "QR image", "taskType": "Type", "assetType": "Type"}
    return labels.get(key, re.sub(r"([a-z])([A-Z])", r"\1 \2", key).replace("_", " ").capitalize())


def terminal_check_failed(check: Any) -> bool:
    if not isinstance(check, dict):
        return False
    failure_fields = {"ok", "readable", "valid", "exists", "formatValid", "versionMatches", "hashMatches"}
    return any((key in failure_fields and value is False) or terminal_check_failed(value)
               for key, value in check.items())


def terminal_fields(value: Any, *, indent: str = "") -> list[str]:
    """Preserve result details without JSON punctuation or internal Agent prose."""
    if isinstance(value, list):
        if not value:
            return [indent + "None"]
        lines = []
        for index, item in enumerate(value, 1):
            if isinstance(item, (dict, list)):
                lines += [indent + f"{index}."] + terminal_fields(item, indent=indent + "  ")
            else:
                lines += terminal_paragraph("- " + terminal_scalar(item), indent=indent).splitlines()
        return lines
    if not isinstance(value, dict):
        return terminal_paragraph(terminal_scalar(value), indent=indent).splitlines()
    lines = []
    for key, item in value.items():
        if key in {"nextAction", "onboarding", "agentInstruction"}:
            continue
        if item == []:
            continue
        label = terminal_label(key)
        if key == "step" and type(item) is int:
            item = {0: "Queued", 1: "Processing", 2: "Completed", 3: "Failed"}.get(item, item)
        if isinstance(item, (dict, list)):
            lines += [indent + label + ":"] + terminal_fields(item, indent=indent + "  ")
        else:
            text = label + ": " + terminal_scalar(item)
            if key in {"path", "file", "output", "qrPath", "h5Link", "releasePage"}:
                lines += [indent + text]
            else:
                lines += terminal_paragraph(text, indent=indent).splitlines()
    lines += [indent + line if line else "" for line in terminal_next_action(value.get("nextAction"))]
    return lines


def terminal_status(value: Any) -> str:
    statuses = {0: "Queued", 1: "Processing", 2: "Completed", 3: "Failed",
                "UPLOADED": "Processing", "UPLOADING_TO_ARK": "Processing",
                "GETTING_UPLOADED_RESULT": "Processing", "UPLOADED_TO_ARK": "Ready",
                "CREATED": "Created", "SUCCEEDED": "Succeeded",
                "FAILED": "Failed", "EXPIRED": "Expired", "unknown": "Unknown",
                "prepared": "Prepared", "submitting": "Submitting", "created": "Created",
                "failed": "Failed", "uploaded": "Registered (not yet ready)"}
    return statuses.get(value, terminal_scalar(value)) if type(value) in (str, int) else "Not provided"


def terminal_table(title: str, columns: tuple[str, ...], rows: list[list[Any]]) -> list[str]:
    cells = [[terminal_scalar(cell) for cell in row] for row in rows]
    widths = [max(len(columns[i]), *(len(row[i]) for row in cells)) for i in range(len(columns))]
    return [title, "", "  ".join(columns[i].ljust(widths[i]) for i in range(len(columns))).rstrip(),
            *["  ".join(row[i].ljust(widths[i]) for i in range(len(columns))).rstrip() for row in cells]]


def terminal_record_list(value: dict[str, Any], view: str) -> list[str]:
    source = value.get("records", value.get("attempts", []))
    if not isinstance(source, list):
        return ["Records could not be read. Check the same query again."]
    records = [item for item in source if isinstance(item, dict)]
    unreadable = len(source) - len(records)
    titles = {"tasks": ("Tasks", "No tasks on this page"),
              "people": ("Authorized people", "No authorized people on this page"),
              "materials": ("Materials", "No materials on this page"),
              "attempts": ("Submission attempts", "No submission attempts")}
    title, empty = titles[view]
    if not records:
        lines = [title if unreadable else empty]
    elif view == "people":
        lines = terminal_table(title, ("Person ID", "Name", "Materials"), [
            [item.get("uniqId"), textwrap.shorten(terminal_scalar(item.get("name")), width=28, placeholder="..."), item.get("assetCount")]
            for item in records])
    else:
        lines = terminal_table(title, ("ID", "Status", "Type" if view != "attempts" else "Task ID"), [
            [item.get("attemptId") or item.get("uniqId") or item.get("taskId"),
             terminal_status(item.get("state", item.get("step", item.get("status")))),
             item.get("taskId") if view == "attempts" else item.get("taskType", item.get("assetType"))]
            for item in records])
    if unreadable:
        lines += [f"Unreadable records: {unreadable}"]
    if value.get("current") is not None and value.get("pages") is not None:
        lines += ["", f"Page {value['current']} of {value['pages']} | Total: {value.get('total', 'Not provided')}"]
    elif "total" in value:
        lines += ["", "Total: " + terminal_scalar(value["total"])]
    if view == "attempts" and any(item.get("state") in {"prepared", "submitting", "unknown"} for item in records):
        lines += ["", "Do not submit an existing attempt again.",
                  "No matching task does not prove that no task was created."]
    return lines + terminal_next_action(value.get("nextAction"))


def terminal_failed_checks(value: dict[str, Any], *, indent: str = "") -> list[str]:
    lines = []
    for key, item in value.items():
        if isinstance(item, dict) and terminal_check_failed(item):
            lines += [indent + terminal_scalar(key) + ":"] + terminal_failed_checks(item, indent=indent + "  ")
        elif item is False or key in {"error", "path", "version", "expectedVersion"}:
            lines += [indent + terminal_label(key) + ": " + terminal_scalar(item)]
    return lines


def terminal_upload_result(value: dict[str, Any]) -> list[str]:
    failed = value.get("failedOrUnknown")
    lines = ["Upload summary", f"  Uploaded: {len(value['uploaded'])}",
             f"  Failed or unknown: {int(failed is not None)}",
             f"  Not attempted: {len(value['notAttempted'])}"]
    sections = [("Registered (wait for processing)", value["uploaded"]),
                ("Failed or unknown", [failed] if failed else []), ("Not attempted", value["notAttempted"])]
    for title, items in sections:
        if not items:
            continue
        lines += ["", title + ":"]
        for item in items:
            lines += terminal_fields({key: item[key] for key in
                                     ("file", "assetUniqId", "groupUniqId", "state", "phase", "error", "warning")
                                     if key in item}, indent="  ")
    if value.get("warning"):
        lines += terminal_paragraph("Warning: " + str(value["warning"])).splitlines()
    return lines + terminal_next_action(value.get("nextAction"))


def terminal_capability(value: dict[str, Any]) -> list[str]:
    lines = ["Model capabilities (installed snapshot)"]
    for key, item in value.items():
        if key in {"requestSchema", "capabilitySnapshotPublishedAt"}:
            continue
        if isinstance(item, list) and all(isinstance(entry, (str, int)) for entry in item):
            text = ", ".join(terminal_scalar(entry) for entry in item)
        elif isinstance(item, dict):
            text = "; ".join(terminal_label(name) + ": " + terminal_scalar(limit) for name, limit in item.items())
        else:
            text = terminal_scalar(item)
        lines += terminal_paragraph(terminal_label(key) + ": " + text).splitlines()
    return lines


def format_terminal_result(value: Any, *, introduce_onboarding: bool = False, view: str | None = None) -> str:
    value = sanitize_for_output(value)
    if view == "models" and isinstance(value, list) and value:
        return "\n".join(terminal_table("Models", ("Model ID", "Type", "Name"), [
            [item.get("id"), item.get("kind"), item.get("label")] for item in value]))
    if not isinstance(value, dict):
        return "\n".join(terminal_fields(value))
    if "httpStatus" in value and "response" in value:
        response = value["response"]
        if not response_ok(value["httpStatus"], response):
            return f"Request failed (HTTP {value['httpStatus']})\n" + "\n".join(terminal_fields(response))
        value = response.get("data", response) if isinstance(response, dict) else response
        return format_terminal_result(value, view=view)
    if "checks" in value and "ok" in value:
        lines = ["Local checks passed" if value["ok"] else "Local checks need attention",
                 f"Version: {value.get('version', VERSION)}"]
        for name, check in value["checks"].items():
            failed = terminal_check_failed(check)
            lines += [f"  {terminal_label(name)}: {'Needs attention' if failed else 'Checked'}"]
            if failed:
                lines += terminal_failed_checks(check, indent="    ")
        if value.get("repairs"):
            lines += ["", "Repair:"] + ["  " + str(command) for command in value["repairs"]]
        if value.get("update", {}).get("error"):
            lines += ["", "Version check:"] + terminal_fields(value["update"], indent="  ")
        elif value.get("update", {}).get("updateAvailable"):
            lines += ["", "Update available: " + str(value["update"].get("latestVersion")), "  holycrab update"]
        if isinstance(value.get("onboarding"), dict):
            lines += ["", format_onboarding(value["onboarding"])]
        return "\n".join(lines)
    if isinstance(value.get("onboarding"), dict):
        account_failed = value.get("valid") is False and value.get("configured") and value.get("httpStatus")
        lines = ([f"Account check failed (HTTP {value['httpStatus']})"] if account_failed else
                 [format_onboarding(value["onboarding"], introduce=introduce_onboarding)])
        account = value.get("account", value)
        if isinstance(account, dict):
            details = {key: account[key] for key in PUBLIC_ACCOUNT_FIELDS if key in account}
            if details:
                lines += [""] + terminal_fields(details)
        if value.get("nextAction") and (account_failed or
                value["nextAction"].get("command") != value["onboarding"].get("command")):
            lines += terminal_next_action(value["nextAction"])
        return "\n".join(lines)
    if isinstance(value.get("files"), list) and "uploadPlanId" in value:
        target = value.get("target", {})
        person = target.get("name") or "Personal materials"
        lines = ["Upload preview", "", "Target: " + terminal_scalar(person)]
        if target.get("uniqId"):
            lines += ["Person group ID: " + terminal_scalar(target["uniqId"])]
        for index, item in enumerate(value["files"], 1):
            lines += ["", f"{index}. {terminal_scalar(item.get('name'))}", "   Path: " + terminal_scalar(item.get("path")),
                      f"   Type: {terminal_scalar(item.get('mediaType'))} ({terminal_scalar(item.get('contentType'))})",
                      f"   Size: {terminal_scalar(item.get('size'))} bytes"]
            if item.get("durationSeconds") is not None:
                lines += [f"   Duration: {terminal_scalar(item['durationSeconds'])} seconds"]
        lines += ["", "Format, dimensions, frame rate, duration and codecs are still checked online.",
                  "Local checks do not guarantee acceptance.", "Confirm only if all files and the target are correct."]
        return "\n".join(lines)
    if view in {"tasks", "people", "materials", "attempts"} or "attempts" in value:
        return "\n".join(terminal_record_list(value, view or "attempts"))
    if "uploaded" in value and "notAttempted" in value:
        return "\n".join(terminal_upload_result(value))
    if "updateAvailable" in value:
        title = ("Version check failed" if value.get("error") else "Update available" if value["updateAvailable"] else
                 "No newer stable release" if value.get("latestVersion") else "Version status unavailable")
        lines = [title, "Current version: " + VERSION]
        lines += terminal_fields({key: value[key] for key in ("latestVersion", "releasePage", "error") if value.get(key)})
        if value["updateAvailable"]:
            lines += ["", "Next:", "  holycrab update"]
        return "\n".join(lines)
    if "id" in value and "requestSchema" in value:
        return "\n".join(terminal_capability(value))
    if "output" in value and "bytes" in value:
        return "\n".join(["Download completed", *terminal_fields(value)])
    if "authorizationId" in value and value.get("status"):
        titles = {"CREATED": "Authorization created", "SUCCEEDED": "Authorization succeeded",
                  "FAILED": "Authorization failed", "EXPIRED": "Authorization expired"}
        lines = [titles.get(value["status"], "Authorization status")]
        group = value.get("group", {})
        details = {"name": group.get("name", value.get("name")), "authorizationId": value["authorizationId"]}
        if group.get("uniqId"):
            details["groupUniqId"] = group["uniqId"]
        details.update({key: value[key] for key in ("h5Link", "qrPath", "expiresAt", "warning") if key in value})
        lines += terminal_fields({key: item for key, item in details.items() if item is not None})
        if value.get("h5Link"):
            lines += ["Keep the verification link and QR private."]
        return "\n".join(lines + terminal_next_action(value.get("nextAction")))
    if "attemptId" in value and "state" in value:
        titles = {"created": "Task created", "failed": "Submission rejected", "unknown": "Submission outcome unknown",
                  "submitting": "Submission not yet confirmed", "prepared": "Submission not yet confirmed"}
        lines = [titles.get(value["state"], "Submission status")]
        lines += terminal_fields({key: value[key] for key in
                                 ("attemptId", "taskId", "kind", "httpStatus", "note") if key in value})
        if value["state"] == "failed" and value.get("message"):
            lines += terminal_paragraph("Error: " + value["message"]).splitlines()
        return "\n".join(lines + terminal_next_action(value.get("nextAction")))
    if "kind" in value and "estimate" in value:
        return "\n".join(["Credit estimate (no task created)", *terminal_fields({"kind": value["kind"]}),
                          *terminal_fields(value["estimate"]), *terminal_next_action(value.get("nextAction"))])
    if "uniqId" in value and "step" in value:
        lines = ["Material status" if "ready" in value else "Task status"]
        details = {key: value[key] for key in
                   ("uniqId", "name", "taskType", "assetType", "model", "progress", "duration", "resolution",
                    "ratio", "generateAudio", "frozenCredit", "textResult", "error") if key in value}
        details["step"] = terminal_status(value["step"])
        lines += terminal_fields(details)
        if "ready" not in value:
            outputs = task_output_urls(value)
            if outputs:
                lines += [f"Outputs: {len(outputs)}"]
        return "\n".join(lines + terminal_next_action(value.get("nextAction")))
    lines = ["Wait timed out", ""] if value.get("timedOut") else []
    if view:
        lines += [view, ""]
    lines += terminal_fields({key: item for key, item in value.items() if key != "timedOut"})
    return "\n".join(lines)


def print_result(value: Any, *, force_json: bool = False, introduce_onboarding: bool = False,
                 view: str | None = None) -> None:
    if force_json or not sys.stdout.isatty():
        print_json(value)
    else:
        print(format_terminal_result(value, introduce_onboarding=introduce_onboarding, view=view), flush=True)


def print_response(status: int, response: Any, *, view: str | None = None) -> int:
    print_result({"httpStatus": status, "response": response}, view=view)
    return 0 if response_ok(status, response) else 1


def capabilities_path() -> Path:
    if "HOLYCRAB_CAPABILITIES_PATH" in os.environ:
        raise SystemExit(
            "Capability overrides are not supported. Run `" + clear_environment_command("HOLYCRAB_CAPABILITIES_PATH") + "` and try again."
        )
    candidates = [
        Path(__file__).resolve().parents[1] / "references" / "capabilities.json",
        Path(__file__).resolve().parent / "references" / "capabilities.json",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    raise SystemExit("HolyCrab capability manifest is missing; reinstall the CLI")


def load_capabilities() -> dict[str, Any]:
    value = read_json_file(capabilities_path(), {})
    if not isinstance(value, dict):
        raise SystemExit("HolyCrab capability manifest is invalid")
    return value


def all_models() -> list[dict[str, Any]]:
    manifest = load_capabilities()
    source = manifest.get("source")
    if not isinstance(source, dict):
        raise SystemExit("HolyCrab capability snapshot metadata is missing")
    snapshot_version = source.get("version")
    published_at = source.get("publishedAt")
    if not isinstance(snapshot_version, str) or not isinstance(published_at, str):
        raise SystemExit("HolyCrab capability snapshot version is missing")
    schemas = manifest.get("requestSchemas")
    if not isinstance(schemas, dict):
        raise SystemExit("HolyCrab capability request schemas are missing")
    output = []
    for kind, group in (("video", "videoModels"), ("image", "imageModels"), ("audio", "audioModels")):
        for model in manifest.get(group, []):
            resolved = {
                "kind": kind,
                "capabilitySnapshotVersion": snapshot_version,
                "capabilitySnapshotPublishedAt": published_at,
                **model,
            }
            schema_ref = resolved.pop("requestSchemaRef", None)
            schema = schemas.get(schema_ref)
            if not isinstance(schema, dict):
                raise SystemExit(f"HolyCrab capability schema is missing: {schema_ref}")
            resolved["requestSchema"] = schema
            output.append(resolved)
    return output


def capability_snapshot() -> dict[str, Any]:
    models = all_models()
    first = models[0] if models else {}
    return {
        "snapshotVersion": first.get("capabilitySnapshotVersion"),
        "publishedAt": first.get("capabilitySnapshotPublishedAt"),
        "models": models,
    }


def find_model(model_id: str) -> dict[str, Any]:
    for model in all_models():
        if model.get("id") == model_id:
            return model
    raise SystemExit(f"Unknown public model: {model_id}. Run `holycrab models list`.")


def _validate_schema_value(name: str, value: Any, rule: dict[str, Any]) -> None:
    expected = rule.get("type")
    matches = {
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "boolean": type(value) is bool,
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }.get(expected, True)
    if not matches:
        raise SystemExit(f"Generation field {name} must be {expected}")
    if "enum" in rule and value not in rule["enum"]:
        raise SystemExit(f"Generation field {name} has an unsupported value")
    if "const" in rule and value != rule["const"]:
        raise SystemExit(f"Generation field {name} must be {rule['const']}")
    if isinstance(value, str):
        if len(value) < rule.get("minLength", 0) or len(value) > rule.get("maxLength", math.inf):
            raise SystemExit(f"Generation field {name} has an invalid length")
    if type(value) is int and (value < rule.get("minimum", -math.inf) or value > rule.get("maximum", math.inf)):
        raise SystemExit(f"Generation field {name} is out of range")
    if isinstance(value, list):
        if len(value) > rule.get("maxItems", math.inf):
            raise SystemExit(f"Generation field {name} contains too many items")
        if rule.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            raise SystemExit(f"Generation field {name} must not contain duplicates")
        item_rule = rule.get("items")
        if isinstance(item_rule, dict):
            for index, item in enumerate(value):
                _validate_schema_value(f"{name}[{index}]", item, item_rule)
    if isinstance(value, dict):
        properties = rule.get("properties", {})
        if rule.get("additionalProperties") is False and set(value) - set(properties):
            raise SystemExit(f"Generation field {name} contains an unsupported property")
        for required in rule.get("required", []):
            if required not in value:
                raise SystemExit(f"Generation field {name} is missing {required}")
        for key, item in value.items():
            if isinstance(properties.get(key), dict):
                _validate_schema_value(f"{name}.{key}", item, properties[key])
        one_of = rule.get("oneOf")
        if isinstance(one_of, list) and not any(
            set(option.get("required", [])).issubset(value)
            for option in one_of if isinstance(option, dict)
        ):
            raise SystemExit(f"Generation field {name} does not match a supported form")


def validate_generation_request(kind: str, payload: dict[str, Any]) -> None:
    if kind == "audio":
        model = next((item for item in all_models() if item.get("kind") == "audio"), None)
    else:
        model_id = payload.get("model")
        model = find_model(model_id) if isinstance(model_id, str) else None
    if not isinstance(model, dict) or model.get("kind") != kind:
        raise SystemExit(f"Generation request does not select a {kind} model")
    schema = model.get("requestSchema")
    if not isinstance(schema, dict):
        raise SystemExit("Generation capability schema is missing")
    allowed = schema.get("properties", {})
    unknown = set(payload) - set(allowed)
    if schema.get("additionalProperties") is False and unknown:
        raise SystemExit(f"Unsupported generation field: {sorted(unknown)[0]}")
    missing = [name for name in schema.get("required", []) if name not in payload]
    if missing:
        raise SystemExit(f"Missing required generation field: {missing[0]}")
    for name, value in payload.items():
        rule = allowed.get(name)
        if isinstance(rule, dict):
            _validate_schema_value(name, value, rule)

    if kind == "audio":
        audio_fields = {"speaker", "audio_data", "audio_url"}
        image_fields = {"image_data", "image_url"}
        audio_count = 0
        image_count = 0
        for reference in payload.get("references", []) or []:
            active = {
                key for key, value in reference.items()
                if key in audio_fields | image_fields and isinstance(value, str) and value.strip()
            }
            if len(active) != 1:
                raise SystemExit("Each audio generation reference must contain exactly one supported source")
            if active & audio_fields:
                audio_count += 1
            else:
                image_count += 1
        limits = model.get("referenceLimits", {})
        if audio_count and image_count:
            raise SystemExit("Audio generation cannot mix image and audio references")
        if audio_count > limits.get("audio", 0) or image_count > limits.get("images", 0):
            raise SystemExit("Too many references for audio generation")
        prompt = payload.get("textPrompt", "")
        if re.search(r"(?:@图片[1-9]\d*|@Image[1-9]\d*(?![A-Za-z0-9_]))", prompt):
            raise SystemExit("Image mentions are not supported in audio generation prompts")
        mentions = [
            int(match.group(1) or match.group(2))
            for match in re.finditer(r"(?:@音频([1-9]\d*)|@Audio([1-9]\d*)(?![A-Za-z0-9_]))", prompt)
        ]
        if not audio_count and mentions:
            raise SystemExit("Audio generation prompts cannot mention audio without audio references")
        if audio_count and set(mentions) != set(range(1, audio_count + 1)):
            raise SystemExit("Audio generation prompts must mention every audio reference and no out-of-range reference")

    if kind == "image":
        image_urls = payload.get("imageUrls", [])
        if isinstance(image_urls, list) and len(image_urls) > model.get("referenceLimits", {}).get("images", 0):
            raise SystemExit("Too many reference images for the selected model")
        size = payload.get("size")
        if isinstance(size, str) and size:
            named = {str(item).lower() for item in model.get("sizes", [])}
            if size.lower() not in named:
                match = re.fullmatch(r"(\d+)\s*[xX×]\s*(\d+)", size)
                if not match:
                    raise SystemExit("Image size must be a supported named size or WIDTHxHEIGHT")
                width, height = int(match.group(1)), int(match.group(2))
                custom = model.get("customSize", {})
                pixels = width * height
                if pixels < custom.get("minPixels", 1) or pixels > custom.get("maxPixels", 0):
                    raise SystemExit("Image custom size pixel count is outside the selected model limit")
                if width <= 0 or height <= 0 or not 1 / 16 <= width / height <= 16:
                    raise SystemExit("Image custom size aspect ratio must be between 1:16 and 16:1")
                multiple = custom.get("dimensionMultiple")
                if type(multiple) is int and multiple > 1 and (width % multiple or height % multiple):
                    raise SystemExit(f"Image width and height must be multiples of {multiple}")
        if payload.get("outputFormat") is not None and not model.get("outputFormatSupported"):
            raise SystemExit(f"Model {model['id']} does not support outputFormat")
        resolution = payload.get("resolution")
        if resolution is not None and resolution.upper() not in model.get("sizes", []):
            raise SystemExit(f"Image resolution {resolution} is not supported by {model['id']}")

    if kind == "video":
        resolution = payload.get("resolution")
        if resolution not in model.get("resolutions", []):
            raise SystemExit(f"Resolution {resolution} is not supported by {model['id']}")
        duration = payload.get("duration")
        explicit_task_type = payload.get("videoTaskType")
        task_type = explicit_task_type or ("frames" if payload.get("firstFrameAssetId") else "reference")
        if task_type not in model.get("taskTypes", ["reference", "frames"]):
            raise SystemExit(f"Video task type {task_type} is not supported by {model['id']}")
        if task_type == "edit":
            if duration != -1:
                raise SystemExit("Seedance edit requests require duration -1")
        else:
            limits = model.get("durationSeconds", {})
            if type(duration) is not int or duration < limits.get("min", 0) or duration > limits.get("max", 0):
                raise SystemExit("Video duration is outside the selected model limit")
        images = payload.get("imageAssetIds", []) or []
        videos = payload.get("videoAssetIds", []) or []
        audios = payload.get("audioAssetIds", []) or []
        first_frame = bool(payload.get("firstFrameAssetId"))
        last_frame = bool(payload.get("lastFrameAssetId"))
        frame_mode = first_frame or last_frame
        references = bool(images or videos or audios)
        if last_frame and not first_frame and model["id"] != "MiniMax-H3":
            raise SystemExit("lastFrameAssetId requires firstFrameAssetId")
        if frame_mode and references:
            raise SystemExit("Frame assets and reference assets cannot be combined")
        if task_type == "frames" and not first_frame and model["id"] != "MiniMax-H3":
            raise SystemExit("Video frame requests require firstFrameAssetId")
        limits = model.get("referenceLimits", {})
        for label, values, key in (("image", images, "images"), ("video", videos, "videos"), ("audio", audios, "audio")):
            maximum = limits.get(key)
            if type(maximum) is int and len(set(values)) > maximum:
                raise SystemExit(f"Too many {label} references for {model['id']}")
        if model["id"] == "MiniMax-H3":
            ratio = payload.get("ratio")
            if frame_mode:
                payload["ratio"] = "adaptive"
            if not frame_mode and not references and (ratio is None or ratio == "adaptive"):
                raise SystemExit("MiniMax H3 text-only requests require a concrete ratio")
            if references and ratio is None:
                payload["ratio"] = "adaptive"
            if audios and not images and not videos:
                raise SystemExit("MiniMax H3 cannot use audio-only references")
        elif audios and not images and not videos and model["id"] != "dreamina-seedance-2-5-260628":
            raise SystemExit("Audio references require at least one visual reference")
        source = bool(payload.get("sourceVideoAssetId"))
        if (task_type in {"edit", "extend"}) != source:
            raise SystemExit("Seedance edit/extend requests require sourceVideoAssetId")
        if source and (first_frame or last_frame):
            raise SystemExit("Seedance edit/extend requests cannot include frame assets")
        if model["id"] == "dreamina-seedance-2-5-260628":
            if videos and explicit_task_type is None:
                raise SystemExit("Seedance 2.5 video references require videoTaskType")
            video_ids = set(videos)
            if source:
                video_ids.add(payload["sourceVideoAssetId"])
            if len(video_ids) > limits.get("videos", math.inf):
                raise SystemExit("Too many video references for Seedance 2.5")
            if len(set(images)) + len(video_ids) + len(set(audios)) > limits.get("total", math.inf):
                raise SystemExit("Too many total references for Seedance 2.5")
            if task_type in {"frames", "edit", "extend"}:
                payload["ratio"] = "adaptive"


def generation_endpoints(kind: str, payload: dict[str, Any]) -> tuple[str, str]:
    if kind not in {"video", "image", "audio"}:
        raise SystemExit("Generation kind must be video, image, or audio")
    if kind == "audio":
        return GENERATION_ROUTES["audioGeneration"]
    model_id = payload.get("model")
    if not isinstance(model_id, str):
        raise SystemExit("Generation request must include model")
    model = find_model(model_id)
    if model["kind"] != kind:
        raise SystemExit(f"Model {model_id} is a {model['kind']} model, not {kind}")
    route = "minimaxVideo" if model_id == "MiniMax-H3" else "seedanceVideo" if kind == "video" else "imageGeneration"
    return GENERATION_ROUTES[route]


def normalize_generation_request(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SystemExit("Generation request must be an object")
    normalized = dict(payload)
    if kind == "video":
        model_id = normalized.get("model")
        if not isinstance(model_id, str):
            raise SystemExit("Generation request must include model")
        model = find_model(model_id)
        if model.get("generateAudioSupported") is True and model.get("generateAudioDefault") is True:
            normalized.setdefault("generateAudio", True)
    validate_generation_request(kind, normalized)
    return normalized


def canonical_request_hash(kind: str, payload: dict[str, Any]) -> str:
    raw = json.dumps(
        {"kind": kind, "request": payload}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
    return {**record, "nextAction": attempt_next_action(record)}


def attempt_records() -> list[dict[str, Any]]:
    with attempts_lock():
        records = [{**value, "nextAction": attempt_next_action(value)}
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


def extract_task_id(response: Any) -> str | None:
    data = response.get("data") if isinstance(response, dict) else None
    if isinstance(data, dict):
        value = data.get("uniqId") or data.get("taskId")
        return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9]{1,64}", value) else None
    return None


def generation_unknown(attempt_id: str, *, http_status: int | None = None, note: str) -> dict[str, Any]:
    changes: dict[str, Any] = {"state": "unknown", "note": note}
    if http_status is not None:
        changes["httpStatus"] = http_status
    update_attempt(attempt_id, **changes)
    return {
        "attemptId": attempt_id,
        "state": "unknown",
        "message": "The result is uncertain. Do not submit this attempt again.",
        "nextAction": {
            "code": "QUERY_RECENT_TASKS",
            "instruction": "Query recent tasks. Finding no result cannot prove that the online task was not created.",
            "command": "holycrab tasks list --page 1 --page-size 20",
        },
    }


def estimate_generation(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_generation_request(kind, payload)
    freeze_endpoint, _ = generation_endpoints(kind, normalized)
    status, response = send("POST", freeze_endpoint, payload=None if kind == "audio" else normalized)
    return {"kind": kind, "estimate": response_data(status, response),
            "nextAction": next_action("REVIEW_ESTIMATE", "Review this estimate and confirm the exact request before creating one billable task.", None)}


def create_generation(
    kind: str,
    payload: dict[str, Any],
    *,
    confirmed: bool,
    attempt_id: str | None = None,
    approved_estimate: dict[str, Any] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    selected_attempt = attempt_id or uuid.uuid4().hex
    context: dict[str, Any] = {}
    try:
        return _create_generation(kind, payload, confirmed=confirmed, attempt_id=selected_attempt,
                                  approved_estimate=approved_estimate, progress=progress, context=context)
    except KeyboardInterrupt:
        if not context.get("reserved"):
            raise
        result = generation_unknown(selected_attempt, note="Interrupted while submitting or recording the result; do not resubmit.")
        if context.get("taskId"):
            update_attempt(selected_attempt, taskId=context["taskId"])
            result["taskId"] = context["taskId"]
            result["nextAction"] = next_action("QUERY_TASK", "A valid task ID was received before interruption. Query this same task; do not resubmit.", f"holycrab tasks get {context['taskId']}")
        raise CommandInterrupted(result) from None


def _create_generation(kind: str, payload: dict[str, Any], *, confirmed: bool, attempt_id: str,
                       approved_estimate: dict[str, Any] | None, progress: Callable[[str], None] | None,
                       context: dict[str, Any]) -> dict[str, Any]:
    payload = normalize_generation_request(kind, payload)
    selected_attempt = attempt_id or uuid.uuid4().hex
    if confirmed and attempt_exists(selected_attempt):
        raise SystemExit(
            f"Local submission attempt {selected_attempt} already exists; query it instead of submitting again"
        )
    if not approved_estimate and progress:
        progress("Estimating credits; no task has been submitted...")
    estimate = approved_estimate or estimate_generation(kind, payload)
    if not confirmed:
        return {
            **estimate,
            "confirmationRequired": True,
            "message": "Confirm once before creating this billable task.",
        }
    _, create_endpoint = generation_endpoints(kind, payload)
    reserve_attempt(
        selected_attempt,
        {
            "attemptId": selected_attempt,
            "kind": kind,
            "requestHash": canonical_request_hash(kind, payload),
            "state": "prepared",
            "createdAt": utc_now(),
            "endpoint": create_endpoint,
        },
    )
    context["reserved"] = True
    update_attempt(selected_attempt, state="submitting")
    try:
        if progress:
            progress("Submitting exactly one billable task. If interrupted, query its attempt instead of submitting again...")
        status, response = send("POST", create_endpoint, payload=payload)
    except KeyboardInterrupt:
        raise CommandInterrupted(generation_unknown(selected_attempt, note="Submission was interrupted. The online outcome is uncertain; do not resubmit.")) from None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        return generation_unknown(
            selected_attempt,
            note=f"Network outcome was ambiguous ({error.__class__.__name__}); do not resubmit.",
        )
    if not response_ok(status, response):
        code = response.get("code") if isinstance(response, dict) else None
        explicit_rejection = (400 <= status < 500 and status != 408) or (
            200 <= status < 300 and type(code) is int and 400 <= code < 500 and code != 408
        )
        if not explicit_rejection:
            return generation_unknown(
                selected_attempt,
                http_status=status,
                note="The service response was ambiguous; do not resubmit.",
            )
        update_attempt(selected_attempt, state="failed", httpStatus=status)
        try:
            response_data(status, response)
        except SystemExit as error:
            return {
                "attemptId": selected_attempt,
                "state": "failed",
                "message": str(error),
                "nextAction": {
                    "code": "FIX_REQUEST",
                    "instruction": "Correct the rejected request and create a new attempt only after confirmation.",
                    "command": None,
                },
            }
    task_id = extract_task_id(response)
    if task_id is None:
        return generation_unknown(
            selected_attempt,
            http_status=status,
            note="The success response did not contain a valid task ID; do not resubmit.",
        )
    context["taskId"] = task_id
    update_attempt(selected_attempt, state="created", taskId=task_id, httpStatus=status)
    return {
        "attemptId": selected_attempt,
        "taskId": task_id,
        "state": "created",
        "estimate": estimate["estimate"],
        "response": public_task_data(response_data(status, response)),
        "nextAction": next_action("WAIT_FOR_TASK", "The task was created. Wait for this same task; do not submit it again.",
                                  f"holycrab tasks wait {task_id} --timeout 600"),
    }


def parse_json_argument(value: str) -> dict[str, Any]:
    raw = Path(value[1:]).expanduser().read_text(encoding="utf-8") if value.startswith("@") else value
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise SystemExit("JSON input must be an object")
    return parsed


def command_set_key(args: argparse.Namespace) -> int:
    if args.stdin:
        api_key = sys.stdin.readline().strip()
    else:
        command_progress(f"Create or copy an API Key at: {PUBLIC_ACCOUNT_URL}")
        api_key = getpass.getpass("Paste API Key (input hidden): ").strip()
    if not api_key:
        raise SystemExit("API Key cannot be empty")
    SENSITIVE_OUTPUT_VALUES.add(api_key)
    account = None
    if not args.no_verify:
        command_progress("API Key received. Verifying your HolyCrab account...")
        status, response = send("GET", "/api/user/me", api_key=api_key)
        account = response_data(status, response)
        if not valid_account_payload(account):
            raise ValueError("Account verification returned an incomplete response; the API Key was not saved")
    config = load_config()
    previously_configured = bool(config.get("apiKey"))
    config["apiKey"] = api_key
    save_config(config)
    command_progress("HolyCrab API Key saved locally with user-only permissions.")
    overridden = bool(os.environ.get("HOLYCRAB_API_KEY"))
    if overridden and not sys.stdout.isatty():
        command_progress("Warning: HOLYCRAB_API_KEY is still set and overrides the saved login.")
        command_progress("Run: " + clear_environment_command("HOLYCRAB_API_KEY"))
    verified = valid_account_payload(account)
    guidance = onboarding_guidance(configured=True, verified=verified, environment_override=overridden)
    result = public_account_data(account) if isinstance(account, dict) else {}
    result.update({"configured": True, "valid": verified and not overridden, "savedKeyVerified": verified,
                   "credentialSource": "environment" if overridden else "local config", "onboarding": guidance})
    print_result(result, force_json=args.stdin, introduce_onboarding=not previously_configured)
    if not sys.stdout.isatty() and not args.stdin:
        show_onboarding(guidance, introduce=not previously_configured)
    return 0


def command_auth_status(args: argparse.Namespace) -> int:
    source = (
        "environment" if os.environ.get("HOLYCRAB_API_KEY")
        else "local config" if load_config().get("apiKey") else None
    )
    if not source:
        print_result({"configured": False, "valid": False, "next": "Run `holycrab setup`.",
                    "onboarding": onboarding_guidance(configured=False),
                    "nextAction": next_action("CONNECT_ACCOUNT", "Configure your API Key locally; never send it in chat.", "holycrab setup")})
        return 1
    command_progress("Checking your HolyCrab account...")
    status, response = send("GET", "/api/user/me")
    account = response_data(status, response) if response_ok(status, response) else None
    if not valid_account_payload(account):
        print_result({"configured": True, "valid": False, "credentialSource": source, "httpStatus": status,
                    "onboarding": onboarding_guidance(configured=True),
                    "nextAction": next_action("CHECK_API_KEY", "Check that the API Key is enabled on the account page, then configure the correct Key locally.", "holycrab setup")})
        return 1
    print_result({"configured": True, "valid": True, "credentialSource": source, "account": public_account_data(account),
                "onboarding": onboarding_guidance(configured=True, verified=True)})
    return 0


def command_clear_key(args: argparse.Namespace) -> int:
    config = load_config()
    config.pop("apiKey", None)
    config.pop("apiKeyDpapi", None)
    save_config(config)
    print("Saved API Key cleared from this computer.\n"
          "Environment variables were not changed.\n"
          "The online API Key was not revoked.")
    return 0


def command_models_list(args: argparse.Namespace) -> int:
    models = all_models()
    brief = [{"id": item["id"], "label": item.get("label"), "kind": item["kind"]} for item in models]
    print_result(capability_snapshot() if args.json else brief, force_json=args.json, view="models")
    return 0


def command_models_show(args: argparse.Namespace) -> int:
    print_result(find_model(args.model))
    return 0


def command_credits_balance(args: argparse.Namespace) -> int:
    command_progress("Checking your HolyCrab credit balance...")
    status, response = send("GET", "/api/user/me")
    return print_response(status, public_account_response(response), view="Credit balance")


def command_generation_estimate(args: argparse.Namespace) -> int:
    command_progress(f"Estimating credits for this {args.kind} request; no task will be created...")
    print_result(estimate_generation(args.kind, parse_json_argument(args.json)))
    return 0


def command_generation_create(args: argparse.Namespace) -> int:
    payload = parse_json_argument(args.json)
    approved_estimate = None
    if not args.yes:
        command_progress("Estimating credits before confirmation; no task has been submitted...")
        approved_estimate = create_generation(args.kind, payload, confirmed=False)
        print_result(approved_estimate)
        if sys.stdout.isatty():
            print("\nRequest to confirm:\n" + "\n".join(terminal_fields(normalize_generation_request(args.kind, payload))), flush=True)
        if not sys.stdin.isatty():
            print("Not submitted. Re-run with --yes only after the user confirms the estimate.", file=sys.stderr)
            return 2
        answer = read_confirmation("Create one billable task with this request? [y/N] ")
        if answer not in {"y", "yes"}:
            print("Cancelled; no generation task was created.", file=sys.stderr)
            return 2
    result = create_generation(
        args.kind,
        payload,
        confirmed=True,
        attempt_id=args.attempt_id,
        approved_estimate=approved_estimate,
        progress=command_progress,
    )
    print_result(result)
    if not sys.stdout.isatty():
        command_progress("Task created. Next: " + result["nextAction"]["command"] if result.get("state") == "created"
                         else "Creation not confirmed. Query existing tasks; do not resubmit.")
    return 0 if result.get("state") == "created" else 1


def command_attempts_list(args: argparse.Namespace) -> int:
    print_result({"attempts": attempt_records()})
    return 0


def command_attempts_get(args: argparse.Namespace) -> int:
    print_result(attempt_record(args.attempt_id))
    return 0


def command_task_get(args: argparse.Namespace) -> int:
    command_progress(f"Checking task {args.uniq_id}...")
    status, response = send("GET", f"/api/tasks/{urllib.parse.quote(args.uniq_id, safe='')}")
    return print_response(status, public_task_response(response))


def command_task_list(args: argparse.Namespace) -> int:
    query = task_list_query(args.page, args.page_size, args.start_date, args.end_date, args.type)
    command_progress("Checking tasks for your HolyCrab account...")
    status, response = send(
        "GET", "/api/tasks", query=query
    )
    return print_response(status, public_task_response(response), view="tasks")


def poll_task(uniq_id: str, timeout: float, interval: float, *, emit: bool = False) -> tuple[int, Any]:
    validate_wait_limits(timeout, interval)
    deadline = time.monotonic() + timeout
    latest: Any = None
    previous_state: Any = object()
    while True:
        status, latest = send("GET", f"/api/tasks/{urllib.parse.quote(uniq_id, safe='')}")
        data = response_data(status, latest) if response_ok(status, latest) else None
        step = data.get("step") if isinstance(data, dict) else None
        state = (status, step)
        if emit and state != previous_state:
            print_response(status, public_task_response(latest))
            previous_state = state
        if not response_ok(status, latest):
            return 1, latest
        if step == 2:
            return 0, latest
        if step == 3:
            return 1, latest
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 2, latest
        time.sleep(min(interval, remaining))


def command_poll_task(args: argparse.Namespace) -> int:
    validate_wait_limits(args.timeout, args.interval)
    command_progress(f"Waiting for task {args.uniq_id}, up to {args.timeout:g} seconds. Ctrl+C stops only this local wait...")
    code, latest = poll_task(args.uniq_id, args.timeout, args.interval, emit=True)
    if code == 2:
        print_result({"timedOut": True, "taskId": args.uniq_id,
                    "nextAction": next_action("CONTINUE_QUERYING", "The local wait timed out; the task was not resubmitted. Query this same task again.", f"holycrab tasks get {args.uniq_id}")})
        if not sys.stdout.isatty():
            print("Polling timed out; the task was not resubmitted.", file=sys.stderr)
    else:
        if not sys.stdout.isatty():
            command_progress("Task wait finished: completed." if code == 0 else "Task wait finished without a successful result. Review the returned status and error.")
    return code


def task_output_urls(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return []
    values: list[str] = []
    for key in ("videoUrl", "cdnUrl"):
        value = data.get(key)
        if isinstance(value, str) and value:
            values.append(value)
    image_urls = data.get("imageUrls")
    if isinstance(image_urls, list):
        values.extend(value for value in image_urls if isinstance(value, str) and value)
    values.extend(parse_audio_urls(data.get("audioIds")))
    return values


def validate_download_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.fragment)
    ):
        raise SystemExit("Task output must be a credential-free HTTPS URL")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise SystemExit("Task output URL cannot use a local or private address")
    addresses: list[str] = []
    try:
        addresses = [item[4][0] for item in socket.getaddrinfo(hostname, parsed.port or 443)]
    except socket.gaierror:
        try:
            addresses = [str(ipaddress.ip_address(hostname))]
        except ValueError:
            pass
    for address in addresses:
        try:
            resolved = ipaddress.ip_address(address)
        except ValueError:
            continue
        if not resolved.is_global:
            raise SystemExit("Task output URL cannot use a local or private address")
    return value


class SecureDownloadRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> urllib.request.Request | None:
        count = int(getattr(req, "holycrab_redirects", 0)) + 1
        if count > MAX_DOWNLOAD_REDIRECTS:
            raise urllib.error.HTTPError(newurl, code, "too many download redirects", headers, fp)
        validated = validate_download_url(urllib.parse.urljoin(req.full_url, newurl))
        redirected = super().redirect_request(req, fp, code, msg, headers, validated)
        if redirected is not None:
            setattr(redirected, "holycrab_redirects", count)
        return redirected


def open_download(url: str) -> Any:
    request = urllib.request.Request(validate_download_url(url), headers={"User-Agent": f"holycrab-cli/{VERSION}"})
    return urllib.request.build_opener(SecureDownloadRedirectHandler()).open(request, timeout=300)


def command_download(args: argparse.Namespace) -> int:
    command_progress(f"Checking downloadable results for task {args.uniq_id}...")
    status, response = send("GET", f"/api/tasks/{urllib.parse.quote(args.uniq_id, safe='')}")
    data = response_data(status, response)
    urls = task_output_urls(data)
    if not urls:
        raise SystemExit("This task has no downloadable URL yet")
    if args.index < 0 or args.index >= len(urls):
        raise SystemExit(f"Output index must be between 0 and {len(urls) - 1}")
    url = validate_download_url(urls[args.index])
    destination = Path(args.output).expanduser().resolve()
    force = bool(getattr(args, "force", False))
    if destination.exists() and not force:
        raise SystemExit(f"Output already exists: {destination}. Use --force to replace it")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, partial_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".part", dir=destination.parent
    )
    partial = Path(partial_name)
    total = 0
    last_progress = time.monotonic()
    try:
        command_progress(f"Downloading output {args.index + 1}...\nPath: {destination}")
        with open_download(url) as remote, os.fdopen(descriptor, "wb") as local:
            descriptor = -1
            content_length = remote.headers.get("Content-Length") if getattr(remote, "headers", None) else None
            if isinstance(content_length, str) and content_length.isdigit() and int(content_length) > MAX_DOWNLOAD_RESPONSE_BYTES:
                raise SystemExit("Download response is larger than the allowed limit")
            while True:
                chunk = remote.read(1024 * 1024)
                if not chunk:
                    break
                local.write(chunk)
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise SystemExit("Download exceeded the allowed size limit")
                now = time.monotonic()
                if now - last_progress >= 2:
                    command_progress(f"Downloaded {total} bytes...")
                    last_progress = now
        if force:
            os.replace(partial, destination)
        else:
            try:
                os.link(partial, destination)
            except FileExistsError as error:
                raise SystemExit(f"Output already exists: {destination}. Use --force to replace it") from error
            partial.unlink()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            partial.unlink()
        except FileNotFoundError:
            pass
    print_result({"taskId": args.uniq_id, "output": str(destination), "bytes": total})
    if not sys.stdout.isatty():
        command_progress(f"Download completed: {destination} ({total} bytes).")
    return 0


class AuthorizationStartResult(dict):
    """Explicit disclosure of a user-requested verification link, never a raw response."""

    qr_bytes: bytes | None = None


def remember_private_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if normalized_name(key) in SENSITIVE_OUTPUT_KEYS and isinstance(item, str) and item:
                SENSITIVE_OUTPUT_VALUES.add(item)
            else:
                remember_private_fields(item)
    elif isinstance(value, list):
        for item in value:
            remember_private_fields(item)


def public_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9]{1,64}", value):
        raise ValueError(f"{label} must contain 1-64 letters or digits")
    return value


def public_fields(value: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("API response must be an object")
    # A nested object in a normally scalar field must not escape the whitelist.
    return {key: item for key in fields if key in value
            for item in (value[key],) if item is None or type(item) in (str, int, float, bool)}


def public_group(value: Any) -> dict[str, Any]:
    return public_fields(value, ("uniqId", "name", "coverUrl", "assetCount", "processingCount", "createdAt"))


def public_asset(value: Any) -> dict[str, Any]:
    output = public_fields(value, ("uniqId", "name", "assetType", "error", "step", "status",
                                  "duration", "url", "createTime", "updateTime"))
    output["ready"] = output.get("step") == "UPLOADED_TO_ARK"
    return output


def public_page(value: Any, projector: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("records"), list):
        raise ValueError("API response is missing its records array")
    output = public_fields(value, ("total", "current", "size", "pages"))
    output["records"] = [projector(item) for item in value["records"]]
    return output


def page_query(page: int = 1, page_size: int = 20) -> list[tuple[str, str]]:
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 100:
        raise CommandUsageError("page must be a positive integer and pageSize must be an integer from 1 to 100")
    return [("page", str(page)), ("pageSize", str(page_size))]


def authorization_cache_dir(*, create: bool = True) -> Path:
    root = config_dir().absolute() / "real-human"
    if root.is_symlink():
        raise ValueError("Authorization cache must not be a symbolic link")
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
    return root


def remove_authorization_qr(authorization_id: str) -> None:
    directory = authorization_cache_dir(create=False) / public_id(authorization_id, "authorizationId")
    if directory.is_symlink() or not directory.is_dir():
        return
    for name in ("qr.png", "metadata.json"):
        (directory / name).unlink(missing_ok=True)
    try:
        directory.rmdir()
    except OSError:
        pass  # Never delete unrelated files placed in this directory.


def cleanup_authorization_qrs() -> str | None:
    try:
        _cleanup_authorization_qrs()
    except (OSError, ValueError):
        return "Could not clean the local QR cache; remote operations remain available. Remove abandoned QR files manually."
    return None


def _cleanup_authorization_qrs() -> None:
    root = authorization_cache_dir(create=False)
    if not root.exists():
        return
    for directory in root.iterdir():
        if directory.is_symlink() or not directory.is_dir() or not re.fullmatch(r"[A-Za-z0-9]{1,64}", directory.name):
            continue
        metadata = directory / "metadata.json"
        if metadata.is_symlink():
            continue
        try:
            data = json.loads(metadata.read_text(encoding="utf-8"))
            deadline = data.get("deleteAfter") if isinstance(data, dict) else None
            if type(deadline) in (int, float) and deadline <= time.time():
                remove_authorization_qr(directory.name)
        except (OSError, ValueError):
            continue


def qr_png(link: str) -> bytes:
    wheel = Path(__file__).resolve().parent / "vendor" / "segno-1.6.6-py3-none-any.whl"
    if not wheel.is_file():
        raise OSError("Bundled QR encoder is missing; reinstall HolyCrab")
    wheel_path = str(wheel)
    sys.path.insert(0, wheel_path)
    try:
        import segno
        if segno.__version__ != "1.6.6":
            raise ValueError("Unexpected QR encoder version")
        stream = io.BytesIO()
        segno.make_qr(link, error="m").save(stream, kind="png", scale=6, border=4)
        return stream.getvalue()
    finally:
        sys.path.remove(wheel_path)


def authorization_qr(authorization_id: str, link: str, expires_at: str) -> tuple[str, bytes]:
    directory = authorization_cache_dir() / public_id(authorization_id, "authorizationId")
    directory.mkdir(mode=0o700)
    # The API currently returns a timezone-less timestamp. Do not interpret that
    # as the client's timezone; use the documented 30-minute session lifetime.
    deadline = time.time() + 30 * 60
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if expiry.tzinfo is not None:
            deadline = min(deadline, expiry.timestamp())
    except ValueError:
        pass
    write_private_json(directory / "metadata.json", {"deleteAfter": deadline})
    raw = qr_png(link)
    path = directory / "qr.png"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
    return str(path), raw


def mutation_response_data(status: int, response: Any, context: str) -> Any:
    remember_private_fields(response)
    code = response.get("code") if isinstance(response, dict) else None
    uncertain = status >= 500 or status == 408 or (type(code) is int and code >= 500)
    malformed = 200 <= status < 300 and (not isinstance(response, dict) or code is None)
    if uncertain or malformed:
        raise ValueError(f"{context} outcome is uncertain (HTTP {status}); query existing records, do not retry automatically")
    return response_data(status, response)


def next_action(code: str, instruction: str, command: str | None) -> dict[str, Any]:
    return {"code": code, "instruction": instruction, "command": command}


def task_next_action(task_id: str, step: Any, data: dict[str, Any]) -> dict[str, Any]:
    if type(step) is int and step in {0, 1}:
        return next_action("WAIT_FOR_TASK", "This task is still processing. Wait for the same task; do not submit it again.",
                           f"holycrab tasks wait {task_id} --timeout 600")
    if step == 2:
        if task_output_urls(data):
            return next_action("CHOOSE_DOWNLOAD_DESTINATION", "The task completed. Choose a local destination before downloading its result.", None)
        if data.get("textResult"):
            return next_action("VIEW_TEXT_RESULT", "The task completed. Read the returned textResult.", None)
        return next_action("QUERY_TASK", "The task completed but no downloadable result was returned. Query this same task again.",
                           f"holycrab tasks get {task_id}")
    if step == 3:
        return next_action("REVIEW_TASK_FAILURE", "This task failed. Review its public error; a new billable task requires a separate estimate and explicit confirmation.", None)
    return next_action("QUERY_TASK", "The task state is not clear. Query this same task; do not resubmit it.", f"holycrab tasks get {task_id}")


def attempt_next_action(record: dict[str, Any]) -> dict[str, Any]:
    task_id = record.get("taskId")
    if record.get("state") == "created" and isinstance(task_id, str) and re.fullmatch(r"[A-Za-z0-9]{1,64}", task_id):
        return next_action("QUERY_TASK", "This attempt already created a task. Query that same task.", f"holycrab tasks get {task_id}")
    if record.get("state") == "failed":
        return next_action("REVIEW_REJECTED_REQUEST", "The service rejected this request. Correct it, estimate again, and obtain new confirmation before using a new attempt.", None)
    return next_action("QUERY_RECENT_TASKS", "Do not submit this attempt again. Query recent tasks; finding no task cannot prove that the online task was not created.",
                       "holycrab tasks list --page 1 --page-size 20")


def interrupted_mutation(instruction: str, *, command: str | None = None, **identifiers: Any) -> CommandInterrupted:
    return CommandInterrupted({"state": "unknown", "interrupted": True, **identifiers,
                               "message": "The operation was interrupted after submission began; its online outcome is uncertain.",
                               "nextAction": next_action("QUERY_EXISTING_RECORDS", instruction + " Do not retry automatically.", command)})


def authorization_next_action(status: str, authorization_id: str, group_id: str | None = None) -> dict[str, Any]:
    if status == "CREATED":
        return next_action(
            "WAIT_FOR_AUTHORIZATION",
            "The person must open the private link or scan the QR code on their phone, complete verification, and confirm it themselves. Query this same authorization ID; do not create another one.",
            f"holycrab real-human wait {authorization_id} --timeout 600",
        )
    if status == "SUCCEEDED" and group_id:
        return next_action(
            "UPLOAD_ASSETS",
            "Authorization succeeded and the person group was created. Authorization does not create assets. Select this person's files, preview an upload to this group, and confirm it separately. Paid generation also requires separate confirmation.",
            None,
        )
    if status == "EXPIRED":
        return next_action(
            "ASK_BEFORE_NEW_AUTHORIZATION",
            "The private link expired. Ask the user before creating a new authorization.",
            None,
        )
    return next_action(
        "ASK_BEFORE_NEW_AUTHORIZATION",
        "This authorization did not complete. Explain the failure and ask the user before creating a new authorization.",
        None,
    )


def asset_next_action(asset_id: str, step: str | None, error: str | None = None) -> dict[str, Any]:
    if step == "UPLOADED_TO_ARK":
        return next_action(
            "ESTIMATE_GENERATION",
            "The asset is ready. Put its asset ID into the generation request and estimate credits before asking for paid-generation confirmation.",
            None,
        )
    if step == "FAILED":
        detail = f" Public error: {error}" if error else ""
        return next_action(
            "DO_NOT_REUPLOAD_AUTOMATICALLY",
            "Asset processing failed." + detail + " Explain it and ask the user before choosing another file.",
            f"holycrab assets get {asset_id}",
        )
    if step in {"UPLOADED", "UPLOADING_TO_ARK", "GETTING_UPLOADED_RESULT", "PROCESSING", "CREATED"}:
        return next_action(
            "WAIT_FOR_ASSET",
            "Upload finished, but online processing is still running. Keep this asset ID and wait; do not upload it again.",
            f"holycrab assets wait {asset_id} --timeout 600",
        )
    return next_action(
        "QUERY_ASSET",
        "The asset result is not clear. Keep the asset ID and query it; do not upload the file again.",
        f"holycrab assets get {asset_id}",
    )


def create_authorization(name: str) -> AuthorizationStartResult:
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 255:
        raise ValueError("name must contain 1-255 characters")
    cleanup_authorization_qrs()
    try:
        status, response = send("POST", "/api/real-human-authorizations/sessions",
                                payload={"name": name.strip(), "callbackUrl": REAL_HUMAN_CALLBACK_URL})
    except KeyboardInterrupt:
        raise interrupted_mutation("Check existing authorizations on the HolyCrab website before deciding whether another authorization is needed.") from None
    except (OSError, http.client.HTTPException) as error:
        raise ValueError("Authorization outcome is uncertain; do not retry automatically. Check the website.") from error
    data = mutation_response_data(status, response, "Authorization")
    if not isinstance(data, dict):
        raise ValueError("Authorization response is incomplete; do not retry automatically. Check the website.")
    try:
        identifier = public_id(data.get("authorizationId"), "authorizationId")
    except ValueError:
        raise ValueError("Authorization response has no usable ID; do not retry automatically. Check the website.") from None
    link = data.get("h5Link")
    expiry = data.get("expiresAt")
    try:
        link = validate_verification_link(link)
        if not isinstance(expiry, str) or not expiry:
            raise ValueError()
    except ValueError:
        raise ValueError(f"Authorization {identifier} returned an invalid verification link or expiry; do not retry automatically") from None
    SENSITIVE_OUTPUT_VALUES.add(link)
    result = AuthorizationStartResult(
        authorizationId=identifier,
        name=name.strip(),
        status="CREATED",
        h5Link=link,
        expiresAt=expiry,
        nextAction=authorization_next_action("CREATED", identifier),
    )
    try:
        result["qrPath"], result.qr_bytes = authorization_qr(identifier, link, expiry)
    except KeyboardInterrupt:
        result["interrupted"] = True
        result["warning"] = "The authorization was created, but QR preparation was interrupted. Use this existing link; do not create another session."
        raise CommandInterrupted(result) from None
    except (OSError, ValueError, ImportError, SystemExit):
        result["warning"] = "QR image could not be saved. Use the authorization link; do not create another session."
    return result


def get_authorization(authorization_id: str) -> dict[str, Any]:
    identifier = public_id(authorization_id, "authorizationId")
    warning = cleanup_authorization_qrs()
    data = response_data(*send("GET", f"/api/real-human-authorizations/{identifier}"))
    output = public_fields(data, ("authorizationId", "status", "completedAt"))
    if warning:
        output["warning"] = warning
    if output.get("authorizationId") != identifier or output.get("status") not in AUTHORIZATION_TERMINAL | {"CREATED"}:
        raise ValueError("API returned an invalid authorization status")
    if isinstance(data.get("group"), dict):
        output["group"] = public_fields(data["group"], ("uniqId", "name"))
    if output["status"] == "SUCCEEDED" and not output.get("group", {}).get("uniqId"):
        raise ValueError("Successful authorization is missing its group; query again, do not create another session")
    output["nextAction"] = authorization_next_action(
        str(output["status"]), identifier, output.get("group", {}).get("uniqId")
    )
    if output["status"] in AUTHORIZATION_TERMINAL:
        try:
            remove_authorization_qr(identifier)
        except (OSError, ValueError):
            output["warning"] = "Could not remove the local QR image; delete it manually."
    return output


def list_real_human_groups(page: int = 1, page_size: int = 20) -> dict[str, Any]:
    query = page_query(page, page_size)
    cleanup_authorization_qrs()
    output = public_page(response_data(*send("GET", "/api/real-human-groups", query=query)), public_group)
    if output.get("total") == 0:
        output["nextAction"] = next_action("ASK_BEFORE_AUTHORIZATION", "No authorized people were found. Ask the user whether to start a new authorization for the intended person.", None)
    return output


def list_real_human_assets(group_id: str, page: int = 1, page_size: int = 50) -> dict[str, Any]:
    identifier = public_id(group_id, "groupUniqId")
    query = page_query(page, page_size)
    cleanup_authorization_qrs()
    output = public_page(response_data(*send("GET", f"/api/real-human-groups/{identifier}/assets", query=query)), public_asset)
    if output.get("total") == 0:
        output["nextAction"] = next_action("SELECT_FILES_TO_UPLOAD", "This person group has no uploaded assets. Select this person's local files, preview the complete upload to this group, and confirm it separately.", None)
    return output


def find_real_human_group(group_id: str) -> dict[str, Any]:
    identifier = public_id(group_id, "groupUniqId")
    page = 1
    while True:
        result = list_real_human_groups(page, 100)
        for group in result.get("records", []):
            if isinstance(group, dict) and group.get("uniqId") == identifier:
                return group
        pages = result.get("pages")
        if type(pages) is not int or page >= pages:
            break
        page += 1
    raise ValueError("Real-human group not found")


def reconcile_group(group_id: str) -> str:
    try:
        group = find_real_human_group(group_id)
        return f"A follow-up list still contains group {group.get('uniqId')}."
    except ValueError as error:
        if str(error) == "Real-human group not found":
            return "A follow-up list did not find the group."
    except (SystemExit, OSError, http.client.HTTPException):
        pass
    return "The follow-up group query could not confirm its state."


def reconcile_asset(asset_id: str) -> str:
    try:
        asset = get_asset(asset_id)
        return f"A follow-up query still contains asset {asset.get('uniqId')}."
    except (SystemExit, ValueError, OSError, http.client.HTTPException):
        return "The follow-up asset query could not confirm its state."


def rename_real_human_group(group_id: str, name: str) -> dict[str, Any]:
    identifier = public_id(group_id, "groupUniqId")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 255:
        raise ValueError("name must contain 1-255 characters")
    try:
        status, response = send("PATCH", f"/api/real-human-groups/{identifier}",
                                payload={"name": name.strip()})
    except KeyboardInterrupt:
        raise interrupted_mutation("Query the person groups to check the current name.", command="holycrab real-human groups list", groupUniqId=identifier) from None
    except (OSError, http.client.HTTPException) as error:
        detail = reconcile_group(identifier)
        raise ValueError(f"Rename outcome is uncertain. {detail} Do not retry automatically") from error
    try:
        data = mutation_response_data(status, response, "Rename")
    except ValueError as error:
        detail = reconcile_group(identifier)
        raise ValueError(f"Rename outcome is uncertain. {detail} Do not retry automatically") from error
    group = public_fields(data, ("uniqId", "name"))
    if group.get("uniqId") != identifier or group.get("name") != name.strip():
        raise ValueError("Rename outcome is uncertain; query the group, do not retry automatically")
    return group


def delete_real_human_group(group_id: str, confirmed: bool,
                            target: dict[str, Any] | None = None) -> dict[str, Any]:
    if confirmed is not True:
        raise ValueError("confirmed must be true after the user explicitly approves permanent deletion")
    identifier = public_id(group_id, "groupUniqId")
    target = target or find_real_human_group(identifier)
    if target.get("uniqId") != identifier:
        raise ValueError("Real-human group not found")
    try:
        status, response = send("DELETE", f"/api/real-human-groups/{identifier}")
    except KeyboardInterrupt:
        raise interrupted_mutation("Query the person groups to check whether this group remains.", command="holycrab real-human groups list", groupUniqId=identifier) from None
    except (OSError, http.client.HTTPException) as error:
        detail = reconcile_group(identifier)
        raise ValueError(f"Delete outcome is uncertain. {detail} Do not retry automatically") from error
    try:
        mutation_response_data(status, response, "Delete")
    except ValueError as error:
        detail = reconcile_group(identifier)
        raise ValueError(f"Delete outcome is uncertain. {detail} Do not retry automatically") from error
    return {"deleted": True, "groupUniqId": identifier}


def delete_real_human_asset(group_id: str, asset_id: str, confirmed: bool,
                            group: dict[str, Any] | None = None,
                            asset: dict[str, Any] | None = None) -> dict[str, Any]:
    if confirmed is not True:
        raise ValueError("confirmed must be true after the user explicitly approves permanent deletion")
    group_identifier = public_id(group_id, "groupUniqId")
    asset_identifier = public_id(asset_id, "assetId")
    group = group or find_real_human_group(group_identifier)
    asset = asset or get_asset(asset_identifier)
    if group.get("uniqId") != group_identifier or asset.get("uniqId") != asset_identifier:
        raise ValueError("Real-human asset not found")
    endpoint = f"/api/real-human-groups/{group_identifier}/assets/{asset_identifier}"
    try:
        status, response = send("DELETE", endpoint)
    except KeyboardInterrupt:
        raise interrupted_mutation("Query this person's assets to check whether the asset remains.", command=f"holycrab real-human assets list --group {group_identifier}", groupUniqId=group_identifier, assetUniqId=asset_identifier) from None
    except (OSError, http.client.HTTPException) as error:
        detail = reconcile_asset(asset_identifier)
        raise ValueError(f"Delete outcome is uncertain. {detail} Do not retry automatically") from error
    try:
        mutation_response_data(status, response, "Delete")
    except ValueError as error:
        detail = reconcile_asset(asset_identifier)
        raise ValueError(f"Delete outcome is uncertain. {detail} Do not retry automatically") from error
    return {"deleted": True, "groupUniqId": group_identifier, "assetUniqId": asset_identifier}


def get_asset(asset_id: str) -> dict[str, Any]:
    identifier = public_id(asset_id, "assetId")
    data = public_asset(response_data(*send("GET", f"/api/user-assets/{identifier}")))
    if data.get("uniqId") != identifier:
        raise ValueError("API returned an unexpected asset ID")
    data["nextAction"] = asset_next_action(identifier, data.get("step"), data.get("error"))
    return data


def poll_resource(identifier: str, timeout: float, interval: float, *, authorization: bool) -> int:
    validate_wait_limits(timeout, interval)
    deadline = time.monotonic() + timeout
    previous_state: Any = object()
    data: dict[str, Any] = {}
    while True:
        data = get_authorization(identifier) if authorization else get_asset(identifier)
        state = data.get("status") if authorization else data.get("step")
        if state != previous_state:
            print_result(data)
            previous_state = state
        if state == ("SUCCEEDED" if authorization else "UPLOADED_TO_ARK"):
            if not sys.stdout.isatty():
                command_progress("Authorization completed; select files to upload next." if authorization else "Asset is ready.")
            return 0
        if state in ({"FAILED", "EXPIRED"} if authorization else {"FAILED", "DELETING"}):
            if not sys.stdout.isatty():
                command_progress("Waiting ended without success. Review the returned status and nextAction.")
            return 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timeout_action = next_action(
                "CONTINUE_QUERYING",
                "The wait timed out; this does not mean failure. Keep the same ID and query again. Nothing was resubmitted.",
                (f"holycrab real-human get {identifier}" if authorization else f"holycrab assets get {identifier}"),
            )
            print_result({"timedOut": True, "id": identifier, "lastStatus": state, "nextAction": timeout_action})
            if not sys.stdout.isatty():
                print("Polling timed out; keep the ID and query again. Nothing was resubmitted.", file=sys.stderr)
            return 2
        time.sleep(min(interval, remaining))


def command_real_human_start(args: argparse.Namespace) -> int:
    command_progress("Creating one real-human authorization. Do not repeat this request if the result is unclear.")
    print_result(create_authorization(args.name))
    return 0


def command_real_human_get(args: argparse.Namespace) -> int:
    command_progress("Checking the existing authorization...")
    print_result(get_authorization(args.authorization_id))
    return 0


def command_real_human_wait(args: argparse.Namespace) -> int:
    validate_wait_limits(args.timeout, args.interval)
    command_progress(f"Waiting for this authorization for up to {args.timeout:g} seconds. Ctrl+C stops local waiting only.")
    return poll_resource(args.authorization_id, args.timeout, args.interval, authorization=True)


def command_real_human_groups(args: argparse.Namespace) -> int:
    command_progress("Listing authorized people...")
    print_result(list_real_human_groups(args.page, args.page_size), view="people")
    return 0


def command_real_human_group_rename(args: argparse.Namespace) -> int:
    command_progress("Renaming the selected authorized person...")
    print_result(rename_real_human_group(args.group_id, args.name), view="Person renamed")
    return 0


def deletion_confirmed(prompt: str, yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        print("Deletion was not confirmed; rerun interactively or use --yes after explicit approval.", file=sys.stderr)
        return False
    accepted = read_confirmation(prompt) in {"y", "yes"}
    if not accepted:
        print("Cancelled; nothing was deleted.", file=sys.stderr)
    return accepted


def command_real_human_group_delete(args: argparse.Namespace) -> int:
    command_progress("Checking the person and assets selected for deletion...")
    target = find_real_human_group(args.group_id)
    print_result({"warning": "Permanent deletion removes this person, all group assets, and upstream records.",
                "target": public_fields(target, ("uniqId", "name", "assetCount"))})
    if not deletion_confirmed("Permanently delete this person and every group asset? [y/N] ", args.yes):
        return 2
    command_progress("Deleting the confirmed person and group assets...")
    print_result(delete_real_human_group(args.group_id, True, target), view="Person deleted")
    return 0


def command_real_human_assets(args: argparse.Namespace) -> int:
    command_progress("Listing assets for the selected authorized person...")
    print_result(list_real_human_assets(args.group, args.page, args.page_size), view="materials")
    return 0


def command_real_human_asset_delete(args: argparse.Namespace) -> int:
    command_progress("Checking the real-human asset selected for deletion...")
    group = find_real_human_group(args.group)
    asset = get_asset(args.asset_id)
    print_result({"warning": "Permanent deletion removes this asset's storage, upstream record, and database record.",
                "target": {"groupUniqId": group.get("uniqId"), "groupName": group.get("name"),
                           "groupAssetCount": group.get("assetCount"),
                           **public_fields(asset, ("uniqId", "name", "assetType"))}})
    if not deletion_confirmed("Permanently delete this real-human asset? [y/N] ", args.yes):
        return 2
    command_progress("Deleting the confirmed real-human asset...")
    print_result(delete_real_human_asset(args.group, args.asset_id, True, group, asset), view="Material deleted")
    return 0


def command_asset_get(args: argparse.Namespace) -> int:
    command_progress("Checking the existing asset...")
    print_result(get_asset(args.uniq_id))
    return 0


def command_asset_wait(args: argparse.Namespace) -> int:
    validate_wait_limits(args.timeout, args.interval)
    result = 0
    identifiers = args.uniq_id if isinstance(args.uniq_id, list) else [args.uniq_id]
    command_progress(f"Waiting for {len(identifiers)} assets, up to {args.timeout:g} seconds per asset. Ctrl+C stops local waiting only.")
    completed = 0
    for index, identifier in enumerate(identifiers, 1):
        command_progress(f"Checking asset {index}/{len(identifiers)}: {identifier}; {completed} ready.")
        code = poll_resource(identifier, args.timeout, args.interval, authorization=False)
        completed += int(code == 0)
        result = max(result, code)
    command_progress(f"Asset wait ended: {completed}/{len(identifiers)} ready.")
    return result


UPLOAD_FORMATS: dict[str, tuple[str, str]] = {
    "jpg": ("image", "image/jpeg"), "jpeg": ("image", "image/jpeg"),
    "png": ("image", "image/png"), "webp": ("image", "image/webp"),
    "bmp": ("image", "image/bmp"), "tiff": ("image", "image/tiff"), "tif": ("image", "image/tiff"),
    "gif": ("image", "image/gif"), "heic": ("image", "image/heic"), "heif": ("image", "image/heif"),
    "mp4": ("video", "video/mp4"), "mov": ("video", "video/quicktime"),
    "wav": ("audio", "audio/wav"), "mp3": ("audio", "audio/mpeg"),
}
REAL_HUMAN_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "heic", "mp4", "mov"}


def file_signature_type(path: Path) -> str | None:
    with path.open("rb") as stream:
        head = stream.read(32)
    if head.startswith(b"\xff\xd8\xff"): return "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"): return "png"
    if head.startswith((b"GIF87a", b"GIF89a")): return "gif"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP": return "webp"
    if head.startswith(b"BM"): return "bmp"
    if head.startswith((b"II*\x00", b"MM\x00*")): return "tiff"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE": return "wav"
    if head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0): return "mp3"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12].lower()
        if brand in {b"heic", b"heix", b"hevc", b"hevx"}: return "heic"
        if brand in {b"mif1", b"msf1", b"heif"}: return "heif"
        if brand == b"qt  ": return "mov"
        return "mp4"
    return None


def sha256_stream(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_upload_file(value: str, *, real_human: bool, duration_seconds: int | None = None) -> dict[str, Any]:
    requested = Path(value).expanduser()
    try:
        path = requested.resolve(strict=True)
    except FileNotFoundError as error:
        raise SystemExit(f"file not found: {requested}") from error
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise SystemExit(f"Upload source must be a regular file: {path}")
    extension = path.suffix.lstrip(".").lower()
    declared = UPLOAD_FORMATS.get(extension)
    signature = file_signature_type(path)
    if declared is None or signature is None:
        raise SystemExit(f"Unsupported or unrecognized upload format: {path.name}")
    compatible = (
        signature == extension
        or {signature, extension} <= {"jpg", "jpeg"}
        or {signature, extension} <= {"tif", "tiff"}
        or {signature, extension} <= {"mp4", "mov"}
    )
    if not compatible:
        raise SystemExit(f"File signature does not match its extension: {path.name}")
    media_type, mime = declared
    if duration_seconds is not None and (type(duration_seconds) is not int or duration_seconds <= 0):
        raise SystemExit("Known media duration must be a positive whole number of seconds")
    resolved_duration = duration_seconds if media_type in {"video", "audio"} else None
    if real_human and extension not in REAL_HUMAN_EXTENSIONS:
        raise SystemExit(f"Real-human uploads do not support {extension or 'this format'}")
    maximum = MAX_IMAGE_UPLOAD_BYTES if media_type == "image" else (
        MAX_REAL_HUMAN_VIDEO_UPLOAD_BYTES if real_human and media_type == "video" else
        MAX_VIDEO_UPLOAD_BYTES if media_type == "video" else MAX_AUDIO_UPLOAD_BYTES
    )
    exceeds = info.st_size > maximum if real_human and media_type == "video" else info.st_size >= maximum
    if exceeds:
        raise SystemExit(f"Upload file is too large: {path.name}")
    return {
        "path": str(path), "name": path.name, "extension": extension, "mediaType": media_type,
        "contentType": mime, "size": info.st_size, "durationSeconds": resolved_duration,
        "mtimeNs": info.st_mtime_ns, "device": info.st_dev, "inode": info.st_ino,
        "sha256": sha256_stream(path),
    }


def upload_plan_path(plan_id: str) -> Path:
    return upload_plans_dir() / f"{public_id(plan_id, 'uploadPlanId')}.json"


def cleanup_upload_plans() -> None:
    directory = upload_plans_dir()
    try:
        if not directory.is_dir() or directory.is_symlink():
            return
        now = time.time()
        for path in directory.iterdir():
            if path.is_symlink() or not path.is_file() or not re.fullmatch(r"[A-Za-z0-9]{1,64}\.json", path.name):
                continue
            plan = read_json_file(path, None)
            if isinstance(plan, dict) and plan.get("state") == "executing":
                continue  # Keep the one-shot ledger for interrupted uploads.
            if not isinstance(plan, dict) or now > float(plan.get("expiresAtEpoch", 0)):
                path.unlink()
    except (SystemExit, OSError, TypeError, ValueError):
        return


def prepare_upload_plan(files: list[str], *, group_uniq_id: str | None = None,
                        duration_seconds: int | None = None) -> dict[str, Any]:
    if not files or len(files) > MAX_UPLOAD_FILES:
        raise SystemExit(f"Choose between 1 and {MAX_UPLOAD_FILES} files")
    group = find_real_human_group(public_id(group_uniq_id, "groupUniqId")) if group_uniq_id else None
    inspected = [inspect_upload_file(value, real_human=group is not None,
                                     duration_seconds=duration_seconds) for value in files]
    plan_id = uuid.uuid4().hex
    stored = {
        "uploadPlanId": plan_id, "state": "prepared", "createdAt": utc_now(),
        "expiresAtEpoch": time.time() + UPLOAD_PLAN_SECONDS,
        "group": public_fields(group, ("uniqId", "name")) if group else None, "files": inspected,
    }
    write_private_json(upload_plan_path(plan_id), stored)
    return {
        "uploadPlanId": plan_id, "expiresInSeconds": UPLOAD_PLAN_SECONDS,
        "target": stored["group"] or {"type": "ordinary-assets"},
        "files": [{key: item[key] for key in ("path", "name", "mediaType", "contentType", "size", "durationSeconds")} for item in inspected],
        "onlineChecks": "Dimensions, aspect ratio, frame rate, duration, and codecs are still checked online; local preview does not guarantee acceptance.",
        "confirmationRequired": True,
        "nextAction": next_action("CONFIRM_UPLOAD_PLAN", "Confirm once only if every file and the target person are correct.", None),
    }


class FileChunks:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __iter__(self):
        with self.path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                yield chunk


def upload_prepared_file(item: dict[str, Any], group_id: str | None,
                         progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    path = Path(item["path"])
    query = [("file_extension", item["extension"]), ("content_type", item["contentType"])]
    if item.get("durationSeconds") is not None:
        query.append(("duration_seconds", str(item["durationSeconds"])))
    try:
        if progress:
            progress("Requesting an upload location...")
        status, response = send("GET", "/api/user-assets/pre-signed-download-url", query=query)
    except KeyboardInterrupt:
        return {"state": "unknown", "phase": "presign", "interrupted": True}
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        return {"state": "unknown", "phase": "presign", "message": str(error)}
    if not response_ok(status, response):
        code = response.get("code") if isinstance(response, dict) else None
        explicit = (400 <= status < 500 and status != 408) or (
            200 <= status < 300 and type(code) is int and 400 <= code < 500 and code != 408
        )
        return {"state": "failed" if explicit else "unknown", "phase": "presign",
                "httpStatus": status}
    try:
        data = response_data(status, response)
    except SystemExit as error:
        return {"state": "unknown", "phase": "presign", "message": str(error)}
    if not isinstance(data, dict):
        return {"state": "unknown", "phase": "presign",
                "message": "Presign response is missing data"}
    try:
        asset_id = public_id(data.get("uniqId"), "assetId")
        presigned_url = validate_presigned_upload_url(data.get("preSignedUrl"))
    except (SystemExit, ValueError) as error:
        return {"state": "unknown", "phase": "presign", "message": str(error)}
    object_key = data.get("objectKey")
    if not isinstance(object_key, str):
        return {"assetUniqId": asset_id, "state": "unknown", "phase": "presign",
                "message": "Presign response omitted objectKey"}
    request = urllib.request.Request(
        presigned_url, data=FileChunks(path),
        headers={"Content-Type": item["contentType"], "Content-Length": str(item["size"])}, method="PUT",
    )
    try:
        if progress:
            progress(f"Uploading {item['size']} bytes...")
        with open_presigned_upload(request) as upload_response:
            if not 200 <= upload_response.status < 300:
                raise OSError(f"Upload returned HTTP {upload_response.status}")
    except KeyboardInterrupt:
        return {"assetUniqId": asset_id, "state": "unknown", "phase": "upload", "interrupted": True}
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        return {"assetUniqId": asset_id, "state": "unknown", "message": str(error)}
    form: dict[str, Any] = {
        "name": item["name"], "object_key": object_key, "content_type": item["contentType"]
    }
    if item.get("durationSeconds") is not None:
        form["duration_seconds"] = item["durationSeconds"]
    endpoint = f"/api/real-human-groups/{group_id}/assets/upload" if group_id else "/api/user-assets/upload"
    try:
        if progress:
            progress("Registering the uploaded asset...")
        register_status, register_response = send("POST", endpoint, form=form)
    except KeyboardInterrupt:
        return {"assetUniqId": asset_id, "state": "unknown", "phase": "registration", "interrupted": True}
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        return {"assetUniqId": asset_id, "state": "unknown", "message": str(error)}
    code = register_response.get("code") if isinstance(register_response, dict) else None
    if not response_ok(register_status, register_response):
        explicit = (400 <= register_status < 500 and register_status != 408) or (
            200 <= register_status < 300 and type(code) is int and 400 <= code < 500 and code != 408
        )
        return {"assetUniqId": asset_id, "state": "failed" if explicit else "unknown", "httpStatus": register_status}
    if not isinstance(register_response, dict) or code is None:
        return {"assetUniqId": asset_id, "state": "unknown", "httpStatus": register_status}
    return {
        "assetUniqId": asset_id, "groupUniqId": group_id, "state": "uploaded", "ready": False,
        "nextAction": asset_next_action(asset_id, "UPLOADED"),
    }


def execute_upload_plan(plan_id: str, *, confirmed: bool,
                        progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    context: dict[str, Any] = {}
    try:
        return _execute_upload_plan(plan_id, confirmed=confirmed, progress=progress, context=context)
    except KeyboardInterrupt:
        if not context.get("started"):
            raise
        plan = context["plan"]
        current = context.get("current", {})
        failed = {"file": current.get("path"), "state": "unknown", "phase": "recording", "interrupted": True}
        known = context.get("currentResult", {})
        if known.get("assetUniqId"):
            failed["assetUniqId"] = known["assetUniqId"]
        group_id = context.get("groupId")
        if group_id:
            failed["groupUniqId"] = group_id
        output = {"uploaded": context.get("completed", []), "failedOrUnknown": failed,
                  "notAttempted": [{"file": item["path"]} for item in context.get("remaining", [])]}
        if failed.get("assetUniqId"):
            output["nextAction"] = asset_next_action(failed["assetUniqId"], None)
        else:
            output["nextAction"] = next_action("REVIEW_ASSETS", "Upload was interrupted. Keep this batch inventory and inspect existing assets before deciding what to do. Do not upload these files again automatically.",
                                               f"holycrab real-human assets list --group {group_id}" if group_id else None)
        plan.update(uploaded=output["uploaded"], failedOrUnknown=failed, notAttempted=output["notAttempted"])
        try:
            write_private_json(upload_plan_path(plan_id), plan)
        except (OSError, SystemExit, KeyboardInterrupt):
            output["warning"] = "Could not finish recording the batch. Keep this result; the plan must not be executed again."
        raise CommandInterrupted(output) from None


def _execute_upload_plan(plan_id: str, *, confirmed: bool,
                         progress: Callable[[str], None] | None, context: dict[str, Any]) -> dict[str, Any]:
    if confirmed is not True:
        raise ValueError("confirmed must be true after the user approves the complete upload preview")
    path = upload_plan_path(plan_id)
    plan = read_json_file(path, None)
    if not isinstance(plan, dict) or plan.get("state") != "prepared":
        raise SystemExit("Upload plan is missing or has already been executed")
    if time.time() > plan.get("expiresAtEpoch", 0):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise SystemExit("Upload plan expired; prepare the files again")
    for stored in plan.get("files", []):
        current = inspect_upload_file(stored["path"], real_human=bool(plan.get("group")),
                                      duration_seconds=stored.get("durationSeconds"))
        for key in ("path", "size", "mtimeNs", "device", "inode", "sha256", "contentType"):
            if current.get(key) != stored.get(key):
                raise SystemExit(f"File changed after confirmation: {stored.get('path')}")
    # Persist the one-shot boundary before the first network write. A crash cannot
    # leave an executable plan that silently repeats an upload.
    plan["state"] = "executing"
    context.update(started=True, plan=plan, remaining=plan["files"])
    write_private_json(path, plan)
    uploaded: list[dict[str, Any]] = []
    failed: dict[str, Any] | None = None
    not_attempted: list[dict[str, Any]] = []
    group_id = plan.get("group", {}).get("uniqId") if isinstance(plan.get("group"), dict) else None
    context["groupId"] = group_id
    files = plan["files"]
    for index, item in enumerate(files):
        context.update(current=item, currentResult={}, remaining=files[index + 1:], completed=list(uploaded))
        try:
            if progress:
                progress(f"Processing file {index + 1}/{len(files)}: {item['name']}")
            result = (upload_prepared_file(item, group_id, progress) if progress
                      else upload_prepared_file(item, group_id))
        except KeyboardInterrupt:
            result = {"state": "unknown", "interrupted": True}
        context["currentResult"] = result
        if result.get("state") != "uploaded":
            failed = {"file": item["path"], **result}
            if group_id:
                failed["groupUniqId"] = group_id
            not_attempted = [{"file": remaining["path"]} for remaining in files[index + 1:]]
            plan.update(uploaded=uploaded, failedOrUnknown=failed, notAttempted=not_attempted)
            write_private_json(path, plan)
            break
        uploaded.append({"file": item["path"], **result})
        plan["uploaded"] = uploaded
        write_private_json(path, plan)
        context["completed"] = list(uploaded)
    output: dict[str, Any] = {"uploaded": uploaded, "failedOrUnknown": failed, "notAttempted": not_attempted}
    if failed and failed.get("assetUniqId"):
        output["nextAction"] = asset_next_action(
            failed["assetUniqId"], "FAILED" if failed.get("state") == "failed" else None
        )
    elif failed and group_id:
        output["nextAction"] = next_action(
            "QUERY_GROUP_ASSETS",
            "The upload result is not clear and no asset ID was returned. Keep the group ID, inspect its assets, and do not upload these files again automatically.",
            f"holycrab real-human assets list --group {group_id}",
        )
    elif failed:
        output["nextAction"] = next_action(
            "REVIEW_ASSETS",
            "The upload result is not clear and no asset ID was returned. Review the account's assets before deciding what to do; do not upload these files again automatically.",
            None,
        )
    elif uploaded:
        ids = " ".join(item["assetUniqId"] for item in uploaded)
        output["nextAction"] = next_action(
            "WAIT_FOR_ASSETS", "All uploads were registered. Wait until every asset is ready before generation.",
            f"holycrab assets wait {ids} --timeout 600",
        )
    if failed and failed.get("interrupted"):
        raise CommandInterrupted(output)
    context["completed"] = list(uploaded)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return output


def command_upload_asset(args: argparse.Namespace) -> int:
    command_progress("Checking files and preparing the complete upload preview...")
    preview = prepare_upload_plan(args.file, group_uniq_id=args.real_human_group,
                                  duration_seconds=args.duration_seconds)
    print_result(preview)
    if not args.yes:
        if not sys.stdin.isatty():
            print("No files were uploaded. Re-run with --yes only after the user confirms the complete preview.", file=sys.stderr)
            return 2
        answer = read_confirmation(f"Upload these {len(args.file)} files to the displayed target? [y/N] ")
        if answer not in {"y", "yes"}:
            try:
                upload_plan_path(preview["uploadPlanId"]).unlink()
            except FileNotFoundError:
                pass
            print("Cancelled; no files were uploaded.", file=sys.stderr)
            return 2
    result = execute_upload_plan(preview["uploadPlanId"], confirmed=True, progress=command_progress)
    print_result(result)
    if not sys.stdout.isatty():
        command_progress(f"Upload ended: {len(result['uploaded'])} uploaded, {int(result['failedOrUnknown'] is not None)} failed or unknown, {len(result['notAttempted'])} not attempted.")
    return 0 if result["failedOrUnknown"] is None else 1


def strict_version(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value.strip())
    return tuple(map(int, match.groups())) if match else None


def read_update_state() -> dict[str, Any]:
    try:
        value = read_json_file(update_state_path(), {})
    except SystemExit:
        # The release cache is disposable. A truncated cache must never block a
        # business command or prevent an explicit refresh from repairing it.
        return {}
    return value if isinstance(value, dict) else {}


def release_update_info(release: Any) -> dict[str, Any]:
    current = strict_version(VERSION)
    tag = release.get("tag_name") if isinstance(release, dict) else None
    latest = strict_version(tag)
    if (
        current is None or latest is None or release.get("draft") is not False
        or release.get("prerelease") is not False
    ):
        raise ValueError("Latest GitHub release is not an eligible stable semantic version")
    page = release.get("html_url")
    expected_page = RELEASE_PAGE_PREFIX + str(tag)
    if not isinstance(page, str) or page != expected_page:
        raise ValueError("Latest GitHub release has an invalid release page")
    return {
        "checkedAt": utc_now(), "latestVersion": ".".join(map(str, latest)),
        "updateAvailable": latest > current, "releasePage": page,
        "release": release,
    }


def fetch_latest_release(timeout: float = UPDATE_CHECK_TIMEOUT) -> dict[str, Any]:
    request = urllib.request.Request(
        UPDATE_API_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"holycrab-cli/{VERSION}",
                 "X-GitHub-Api-Version": "2022-11-28"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("GitHub release response is too large")
    release = json.loads(raw.decode("utf-8"))
    return release_update_info(release)


def check_for_update(*, force: bool = False, timeout: float = UPDATE_CHECK_TIMEOUT) -> dict[str, Any]:
    cached = read_update_state()
    if os.environ.get("HOLYCRAB_NO_UPDATE_CHECK") == "1" and not force:
        return cached.get("update", {"checkedAt": None, "latestVersion": VERSION,
                                      "updateAvailable": False, "disabled": True})
    checked_epoch = cached.get("checkedAtEpoch", 0)
    if not force and isinstance(checked_epoch, (int, float)) and time.time() - checked_epoch < UPDATE_CHECK_SECONDS:
        update = cached.get("update")
        if isinstance(update, dict):
            return update
    try:
        update = fetch_latest_release(timeout)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as error:
        fallback = cached.get("update", {"checkedAt": None, "latestVersion": None,
                                         "updateAvailable": False})
        stored_failure: dict[str, Any] = {"checkedAtEpoch": time.time(), "update": fallback}
        if isinstance(cached.get("release"), dict):
            stored_failure["release"] = cached["release"]
        try:
            write_private_json(update_state_path(), stored_failure)
        except OSError:
            pass
        if force:
            return {"checkedAt": utc_now(), "latestVersion": None, "updateAvailable": False,
                    "error": sanitize_text_for_output(str(error))}
        return fallback
    stored = {key: value for key, value in update.items() if key != "release"}
    write_private_json(update_state_path(), {"checkedAtEpoch": time.time(), "update": stored,
                                             "release": update["release"]})
    return stored


def cached_update_notice() -> str | None:
    update = read_update_state().get("update")
    if isinstance(update, dict) and update.get("updateAvailable"):
        return (
            f"HolyCrab update available: {VERSION} -> {update.get('latestVersion')}\n"
            f"Release: {update.get('releasePage')}\n"
            "Next:\n  holycrab update"
        )
    return None


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
    path = installation_path()
    value = read_json_file(path, None)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise SystemExit("HolyCrab installation manifest must contain a JSON object")
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise SystemExit(f"Unsafe permissions on {path}; reinstall HolyCrab")
    return value


def run_update(release: dict[str, Any], *, progress: Callable[[str], None] | None = None) -> None:
    name, url, expected = release_installer(release)
    manifest = load_installation() or {}
    descriptor, temporary_name = tempfile.mkstemp(prefix="holycrab-update-", suffix=Path(name).suffix)
    temporary = Path(temporary_name)
    installing = False
    try:
        if progress:
            progress("Downloading the release installer...")
        digest = hashlib.sha256()
        total = 0
        with open_download(url) as remote, os.fdopen(descriptor, "wb") as local:
            descriptor = -1
            while chunk := remote.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_INSTALLER_BYTES:
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
        if isinstance(manifest.get("prefix"), str):
            environment["HOLYCRAB_INSTALL_PREFIX"] = manifest["prefix"]
        agents = manifest.get("agents")
        if isinstance(agents, list):
            environment["HOLYCRAB_INSTALL_AGENTS"] = ",".join(str(item) for item in agents)
        if isinstance(manifest.get("mcp"), bool):
            environment["HOLYCRAB_INSTALL_MCP"] = "1" if manifest["mcp"] else "0"
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
            raise SystemExit("HolyCrab installer failed. Run `holycrab doctor --json`; reinstall if necessary. Review local state before assuming restoration completed")
        if progress:
            progress("Update completed; the installer's version and health checks passed.")
    except KeyboardInterrupt:
        if installing:
            raise CommandInterrupted({"state": "unknown", "interrupted": True,
                "nextAction": next_action("CHECK_INSTALLATION", "Update interrupted during installation. Check local health; reinstall if the launcher no longer works. Do not assume rollback completed.", "holycrab doctor --json")}) from None
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def command_update(args: argparse.Namespace) -> int:
    command_progress("Checking GitHub for a stable HolyCrab update...")
    update = check_for_update(force=True, timeout=30.0)
    if update.get("error"):
        print_result(update)
        print("Current installation was not changed.\nNext:\n  holycrab update --check", file=sys.stderr)
        return 1
    print_result({key: value for key, value in update.items() if key != "release"})
    if not update.get("updateAvailable") or args.check:
        return 0
    state = read_update_state()
    release = state.get("release")
    if not isinstance(release, dict):
        raise SystemExit("Release metadata cache is missing; run `holycrab update --check` again")
    if not args.yes:
        if not sys.stdin.isatty():
            print("Update not installed. Re-run interactively or use --yes after reviewing the release.", file=sys.stderr)
            return 2
        if read_confirmation("Install this verified HolyCrab update now? [y/N] ") not in {"y", "yes"}:
            print("Update cancelled; the current installation was not changed.", file=sys.stderr)
            return 2
    run_update(release, progress=command_progress)
    return 0


def _normalized_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def validated_uninstall_manifest() -> tuple[dict[str, Any], Path, Path, Path]:
    manifest = load_installation()
    if manifest is None:
        raise SystemExit("This is not an installer-managed HolyCrab installation; nothing was removed")
    manager = manifest.get("managedBy")
    if manager not in {None, INSTALLATION_MANAGER}:
        raise SystemExit("This HolyCrab installation is managed by an unknown installer; nothing was removed")
    schema = manifest.get("schemaVersion")
    if schema not in {None, INSTALLATION_SCHEMA_VERSION}:
        raise SystemExit("This HolyCrab installation uses an unsupported manifest schema; nothing was removed")
    prefix_value = manifest.get("prefix")
    if not isinstance(prefix_value, str) or not prefix_value.strip():
        raise SystemExit("HolyCrab installation manifest has no valid prefix; nothing was removed")
    prefix = _normalized_path(prefix_value)
    library = prefix / "lib" / "holycrab"
    current_library = _normalized_path(installation_path().parent)
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
                           previous_path: str) -> None:
    """Internal installer entry point; no public command, credential or arbitrary Agent mutation."""
    manifest = load_installation() or {}
    previous = read_json_file(Path(previous_path), {}) if previous_path else {}
    if not isinstance(previous, dict) or previous.get("managedBy") != INSTALLATION_MANAGER or (
        previous.get("prefix") != manifest.get("prefix")
    ):
        previous = {}
    executable = find_agent_client(agent, previous, preferred)
    expected = str(installation_path().parent / "holycrab_cli.py") if os.name == "nt" else command
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
    try:
        if not executable:
            raise RuntimeError(f"{agent} is unavailable; its HolyCrab MCP registration was not checked")
        current, absent, _ = inspect_agent_registration(executable, agent)
        if current is not None and registration_matches(current, previous, agent, expected) and (
            previous.get("mcp") is True and agent in previous.get("agents", [])
            and (current["command"] != command or current["args"] != arguments)
        ):
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
        print(f"Warning: {sanitize_text_for_output(str(error))}", file=sys.stderr)
    manifest.setdefault("agentRegistrations", {})[agent] = record
    write_private_json(installation_path(), manifest)


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
                f"{sanitize_text_for_output(str(error))}"
            ) from error
        if removed.returncode != 0:
            raise RuntimeError(
                f"Could not remove the managed {agent} MCP registration; no program files were removed "
                f"(client exit code {removed.returncode})"
            )
    return warnings


def _manifest_hashes(manifest: dict[str, Any]) -> dict[Path, str]:
    result: dict[Path, str] = {}
    core_files = manifest.get("coreFiles")
    if not isinstance(core_files, list):
        return result
    for item in core_files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            continue
        expected = item.get("sha256")
        if isinstance(expected, str) and re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            result[_normalized_path(item["path"])] = expected.lower()
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
        for relative in MANAGED_SKILL_FILES:
            target = root / relative
            if not target.exists() and not target.is_symlink():
                continue
            expected = expected_hashes.get(_normalized_path(target))
            try:
                matches = target.is_file() and expected is not None and sha256_stream(target) == expected
            except OSError:
                matches = False
            if not matches:
                warnings.append(f"Modified or unverified Skill file was kept: {target}")
                continue
            try:
                target.unlink()
            except OSError as error:
                warnings.append(f"Could not remove Skill file {target}: {sanitize_text_for_output(str(error))}")
        for directory in (root / "references", root / "agents", root):
            try:
                directory.rmdir()
            except (FileNotFoundError, OSError):
                pass
    return warnings


def purge_known_local_state() -> list[str]:
    warnings: list[str] = []
    configured_root = config_dir().expanduser().absolute()
    root = _normalized_path(configured_root)
    dangerous = {_normalized_path(Path(root.anchor)), _normalized_path(Path.home())}
    if configured_root.is_symlink() or root in dangerous:
        return [f"Refused to purge unsafe configuration directory: {root}"]
    for name in ("config.json", "attempts.json", "attempts.lock", "update-state.json", "health-state.json"):
        try:
            (root / name).unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            warnings.append(f"Could not remove {root / name}: {sanitize_text_for_output(str(error))}")
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
        warnings.append(f"Could not remove all known state from {plans}: {sanitize_text_for_output(str(error))}")
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
        warnings.append(f"Could not remove all known state from {authorizations}: {sanitize_text_for_output(str(error))}")
    try:
        root.rmdir()
    except FileNotFoundError:
        pass
    except OSError as error:
        if error.errno == errno.ENOTEMPTY:
            warnings.append(f"Unknown files were kept in {root}")
        else:
            warnings.append(f"Could not remove empty configuration directory {root}: {sanitize_text_for_output(str(error))}")
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
    powershell = shutil.which("powershell")
    if powershell is None:
        raise RuntimeError("PowerShell is required to finish Windows self-uninstall")
    descriptor, helper_name = tempfile.mkstemp(prefix="holycrab-uninstall-", suffix=".ps1")
    helper = Path(helper_name)
    failure_log = helper.with_suffix(".log")
    program = r'''param([string]$Launcher,[string]$Library,[string]$SelfPath,[string]$FailureLog)
$deadline = [DateTime]::UtcNow.AddSeconds(8)
Start-Sleep -Milliseconds 750
do {
  Remove-Item -LiteralPath $Launcher -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $Library -Recurse -Force -ErrorAction SilentlyContinue
  if (-not (Test-Path -LiteralPath $Launcher) -and -not (Test-Path -LiteralPath $Library)) { break }
  Start-Sleep -Milliseconds 200
} while ([DateTime]::UtcNow -lt $deadline)
if ((Test-Path -LiteralPath $Launcher) -or (Test-Path -LiteralPath $Library)) {
  $message = "launcherExists=$([bool](Test-Path -LiteralPath $Launcher)); libraryExists=$([bool](Test-Path -LiteralPath $Library))"
  [IO.File]::WriteAllText($FailureLog, $message, [Text.UTF8Encoding]::new($false))
}
Remove-Item -LiteralPath $SelfPath -Force -ErrorAction SilentlyContinue
'''
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(program)
    creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        subprocess.Popen(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(helper),
             str(launcher), str(library), str(helper), str(failure_log)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, creationflags=creation_flags,
        )
    except OSError:
        try:
            helper.unlink()
        except FileNotFoundError:
            pass
        raise


def command_uninstall(args: argparse.Namespace) -> int:
    # Before confirmation an interrupt changes nothing; after confirmation the
    # cleanup may be partial, so never promise that files/registrations remain.
    args.cleanup_started = False
    try:
        return perform_uninstall(args)
    except KeyboardInterrupt:
        if not args.cleanup_started:
            raise
        raise CommandInterrupted({"state": "unknown", "interrupted": True,
            "nextAction": next_action("CHECK_LOCAL_CLEANUP", "Uninstall was interrupted after cleanup started. Some registrations or program files may have been removed. Check the installation or reinstall before continuing; local data purge may be incomplete.", "holycrab doctor --json")}) from None


def perform_uninstall(args: argparse.Namespace) -> int:
    manifest, prefix, library, launcher = validated_uninstall_manifest()
    config = _normalized_path(config_dir())
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
        if read_confirmation(prompt) not in {"y", "yes"}:
            print("Uninstall cancelled; nothing was removed.", file=sys.stderr)
            return 2

    args.cleanup_started = True
    command_progress("Removing matching installer-managed HolyCrab MCP registrations...")
    expected_command = str(prefix / ("lib/holycrab/holycrab_cli.py" if os.name == "nt" else "bin/holycrab"))
    try:
        warnings = remove_managed_mcp_registrations(manifest, expected_command)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    command_progress("Removing unchanged installer-managed Skill files...")
    warnings.extend(remove_managed_skill_files(manifest))
    command_progress("Removing program files and eligible PATH/profile entries...")
    hard_failures: list[str] = []

    if os.name == "nt":  # pragma: no cover - Windows CI
        warnings.extend(remove_managed_path_registration(manifest, prefix, launcher))
        try:
            schedule_windows_program_cleanup(launcher, library)
        except (OSError, RuntimeError) as error:
            print(f"Could not schedule Windows program cleanup: {sanitize_text_for_output(str(error))}", file=sys.stderr)
            return 1
        # Installed Python sources are normally removable while this process is
        # still finishing.  The batch launcher removes itself after Python
        # returns; the delayed PowerShell helper covers transient file locks.
        try:
            if library.is_symlink():
                library.unlink()
            elif library.exists():
                shutil.rmtree(library)
        except OSError:
            pass
    else:
        try:
            launcher.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            hard_failures.append(f"Could not remove launcher {launcher}: {sanitize_text_for_output(str(error))}")
        try:
            if library.is_symlink():
                library.unlink()
            elif library.exists():
                shutil.rmtree(library)
        except OSError as error:
            hard_failures.append(f"Could not remove program directory {library}: {sanitize_text_for_output(str(error))}")
        warnings.extend(remove_managed_path_registration(manifest, prefix, launcher))

    if hard_failures:
        for failure in hard_failures:
            print(f"Error: {failure}", file=sys.stderr)
        print("Local credentials and records were kept because program removal was incomplete.", file=sys.stderr)
        return 1
    if args.purge:
        command_progress("Purging known local HolyCrab credentials and records; unknown files will be kept...")
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
        print(f"Revoke the API Key separately if it must stop working:\n  {PUBLIC_ACCOUNT_URL}")
    else:
        print(f"Local HolyCrab credentials and records were preserved at:\n  {config}")
    if warnings:
        print("\nNotes:")
    for warning in warnings:
        informational = warning.startswith("Info:") or warning == "PATH registration was kept because the installer did not add it" or (
            warning.startswith("PATH registration was kept because") and warning.endswith("contains other programs")
        )
        message = warning if warning.startswith("Info:") else f"{'Info' if informational else 'Warning'}: {warning}"
        print(terminal_paragraph(message))
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
    return 0


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
            result["sha256"] = sha256_stream(path)
            if isinstance(hash_cache, dict):
                hash_cache[key] = {"size": info.st_size, "mtimeNs": info.st_mtime_ns,
                                   "expected": expected.lower(), "sha256": result["sha256"]}
        result["hashMatches"] = result["sha256"] == expected.lower()
    return result


def mcp_registration_check(agent: str, expected_command: str) -> dict[str, Any]:
    manifest = load_installation() or {}
    executable = find_agent_client(agent, manifest)
    if executable is None:
        return {"selected": True, "installed": False, "ok": False}
    try:
        registration, _, exit_code = inspect_agent_registration(executable, agent)
    except (OSError, subprocess.SubprocessError) as error:
        return {"selected": True, "installed": True, "ok": False,
                "error": sanitize_text_for_output(str(error))}
    matches = registration is not None and registration.get("scope") == "user" and mcp_points_to_cli(registration, expected_command)
    return {"selected": True, "installed": True, "ok": matches,
            "exitCode": exit_code}


def local_health_report(*, online: bool = False) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    repairs: list[str] = []
    checks["python"] = {"version": platform.python_version(), "ok": sys.version_info >= (3, 10)}
    try:
        checks["capabilities"] = file_check(capabilities_path())
        load_capabilities()
        checks["capabilities"]["valid"] = True
    except (SystemExit, OSError, ValueError) as error:
        checks["capabilities"] = {"ok": False, "error": str(error)}
    qr = Path(__file__).resolve().parent / "vendor" / "segno-1.6.6-py3-none-any.whl"
    checks["qrDependency"] = file_check(qr)
    try:
        manifest = load_installation()
        manifest_error = None
    except SystemExit as error:
        manifest = None
        manifest_error = str(error)
    try:
        health_cache = read_json_file(health_state_path(), {})
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
        source_checkout = any((parent / ".git").exists() for parent in Path(__file__).resolve().parents)
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
            and manifest.get("schemaVersion") in {None, INSTALLATION_SCHEMA_VERSION}
            and manifest.get("managedBy") in {None, INSTALLATION_MANAGER}
        )
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
                    candidate = installation_path().parent / candidate
                expected = item.get("sha256")
                if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
                    manifest_format_ok = False
                    expected = None
                core[item["path"]] = file_check(candidate, expected, health_cache)
        version_ok = manifest.get("version") == VERSION
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
            write_private_json(health_state_path(), health_cache)
        except OSError:
            pass
    try:
        config = load_config()
        checks["config"] = {"readable": True, "keyConfigured": bool(os.environ.get("HOLYCRAB_API_KEY") or config.get("apiKey"))}
    except SystemExit as error:
        checks["config"] = {"readable": False, "error": str(error)}
        repairs.append("holycrab setup")
        config = {}
    try:
        load_attempts()
        checks["attempts"] = {"readable": True}
    except SystemExit as error:
        checks["attempts"] = {"readable": False, "error": str(error)}
    if online:
        checks["apiKey"] = {"ok": False, "configured": bool(os.environ.get("HOLYCRAB_API_KEY") or config.get("apiKey"))}
        if checks["apiKey"]["configured"]:
            try:
                status, response = send("GET", "/api/user/me")
                checks["apiKey"]["ok"] = response_ok(status, response) and valid_account_payload(response_data(status, response))
                checks["apiKey"]["httpStatus"] = status
            except (SystemExit, OSError, urllib.error.URLError, http.client.HTTPException) as error:
                checks["apiKey"]["error"] = sanitize_text_for_output(str(error))
        if manifest is not None and manifest.get("mcp") is True:
            prefix = manifest.get("prefix")
            expected = str(
                Path(prefix) / ("lib/holycrab/holycrab_cli.py" if os.name == "nt" else "bin/holycrab")
            ) if isinstance(prefix, str) else ""
            registrations: dict[str, Any] = {}
            for agent in manifest.get("agents", []):
                if agent in {"codex", "claude"}:
                    registrations[agent] = mcp_registration_check(agent, expected)
                    if not registrations[agent]["ok"]:
                        repairs.append("holycrab update --yes")
            checks["mcpRegistrations"] = registrations
    update = (check_for_update(force=True, timeout=30.0) if online else
              read_update_state().get("update", {"checkedAt": None, "latestVersion": VERSION,
                                                   "updateAvailable": False}))
    ok = checks["python"]["ok"] and checks.get("capabilities", {}).get("valid") is True
    ok = ok and checks["qrDependency"].get("exists") is True and checks.get("config", {}).get("readable") is True
    if manifest_error:
        ok = False
    elif manifest is None and not checks["installation"].get("sourceCheckout"):
        ok = False
    elif manifest is not None:
        ok = ok and checks["installation"].get("formatValid") is True
        ok = ok and checks["installation"].get("versionMatches") is True
        ok = ok and all(row.get("exists") and row.get("hashMatches") is not False for row in checks["installation"]["coreFiles"].values())
    if online:
        ok = ok and checks.get("apiKey", {}).get("ok") is True and not update.get("error")
        ok = ok and all(row.get("ok") is True for row in checks.get("mcpRegistrations", {}).values())
    return {"ok": ok, "version": VERSION, "checks": checks, "update": update, "repairs": list(dict.fromkeys(repairs)),
            "onboarding": onboarding_guidance(configured=bool(checks.get("config", {}).get("keyConfigured")),
                                               verified=checks.get("apiKey", {}).get("ok") is True)}


def command_doctor(args: argparse.Namespace) -> int:
    if args.online:
        command_progress("Checking local health, refreshing version status, validating the API Key, and checking selected MCP registrations...")
    report = local_health_report(online=args.online)
    print_result(report, force_json=args.json)
    return 0 if report["ok"] else 1


ID_SCHEMA = {"type": "string", "pattern": "^[A-Za-z0-9]{1,64}$"}
ATTEMPT_ID_SCHEMA = {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,128}$"}


MCP_TOOLS = [
    {"name": "cli_status", "description": "Read local CLI health and cached update status without contacting HolyCrab.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "account_get", "description": "Check the current HolyCrab account and credit balance.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}},
    {"name": "capabilities_list", "description": "List the public generation capability snapshot bundled with this release.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "capability_get", "description": "Get limits for one public model.", "inputSchema": {"type": "object", "properties": {"model": {"type": "string"}}, "required": ["model"], "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "generation_estimate", "description": "Estimate credit without creating a task.", "inputSchema": {"type": "object", "properties": {"kind": {"enum": ["video", "image", "audio"]}, "request": {"type": "object"}}, "required": ["kind", "request"], "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}},
    {"name": "generation_create", "description": "Create exactly one billable generation after explicit user confirmation. Supply one stable attemptId per confirmed draw and reuse it only when reconciling a retry.", "inputSchema": {"type": "object", "properties": {"kind": {"enum": ["video", "image", "audio"]}, "request": {"type": "object"}, "confirmed": {"type": "boolean"}, "attemptId": {**ATTEMPT_ID_SCHEMA, "description": "Client-generated stable ID for this one confirmed draw."}}, "required": ["kind", "request", "confirmed", "attemptId"], "additionalProperties": False}, "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}},
    {"name": "generation_get", "description": "Get one generation task by ID.", "inputSchema": {"type": "object", "properties": {"taskId": {"type": "string"}}, "required": ["taskId"], "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}},
    {"name": "generation_list", "description": "List tasks for the current HolyCrab user.", "inputSchema": {"type": "object", "properties": {"page": {"type": "integer", "minimum": 1}, "pageSize": {"type": "integer", "minimum": 1, "maximum": 100}, "startDate": {"type": "string"}, "endDate": {"type": "string"}, "taskType": {"type": "string", "enum": ["IMAGE", "VIDEO", "AUDIO", "TEXT"]}}, "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}},
    {"name": "generation_attempt_list", "description": "List local one-shot generation submission attempts.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "generation_attempt_get", "description": "Get one local generation submission attempt.", "inputSchema": {"type": "object", "properties": {"attemptId": ATTEMPT_ID_SCHEMA}, "required": ["attemptId"], "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
]


def real_human_tool(name: str, description: str, properties: dict[str, Any],
                    required: tuple[str, ...] = (), *, read_only: bool = True,
                    destructive: bool = False) -> dict[str, Any]:
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties,
                            "required": list(required), "additionalProperties": False},
            "annotations": {"readOnlyHint": read_only, "destructiveHint": destructive,
                            "idempotentHint": read_only and not destructive, "openWorldHint": True}}


PAGE_PROPERTIES = {"page": {"type": "integer", "minimum": 1},
                   "pageSize": {"type": "integer", "minimum": 1, "maximum": 100}}
REAL_HUMAN_TOOLS = [
    real_human_tool("real_human_authorization_start",
                    "Create one user-requested authorization. Show its private temporary link and QR image to the person for manual verification. Do not retry automatically or treat verification as consent to paid generation.",
                    {"name": {"type": "string", "minLength": 1, "maxLength": 255}}, ("name",), read_only=False),
    real_human_tool("real_human_authorization_get", "Query authorization status by ID. Poll this tool, do not recreate the session.",
                    {"authorizationId": ID_SCHEMA}, ("authorizationId",)),
    real_human_tool("real_human_groups_list", "List the current account's authorized people, with pagination.", PAGE_PROPERTIES),
    real_human_tool("real_human_group_rename", "Rename one authorized person using the public group ID.",
                    {"groupUniqId": ID_SCHEMA, "name": {"type": "string", "minLength": 1, "maxLength": 255}},
                    ("groupUniqId", "name"), read_only=False),
    real_human_tool("real_human_group_delete", "Permanently delete an authorized person, every asset in the group, and upstream records. Set confirmed=true only after the user explicitly approves this exact deletion.",
                    {"groupUniqId": ID_SCHEMA, "confirmed": {"type": "boolean"}},
                    ("groupUniqId", "confirmed"), read_only=False, destructive=True),
    real_human_tool("real_human_assets_list", "List assets in one authorized person's group. Only ready assets can be used for generation.",
                    {"groupUniqId": ID_SCHEMA, **PAGE_PROPERTIES}, ("groupUniqId",)),
    real_human_tool("real_human_asset_delete", "Permanently delete one real-human asset from storage, upstream records, and the database. Set confirmed=true only after the user explicitly approves this exact deletion.",
                    {"groupUniqId": ID_SCHEMA, "assetId": ID_SCHEMA, "confirmed": {"type": "boolean"}},
                    ("groupUniqId", "assetId", "confirmed"), read_only=False, destructive=True),
    real_human_tool("asset_get", "Query one public asset ID. Only UPLOADED_TO_ARK yields ready=true; report failures and do not reupload automatically.",
                    {"assetId": ID_SCHEMA}, ("assetId",)),
    real_human_tool("asset_upload_prepare", "Inspect 1-10 local files and return a short-lived complete preview. This does not upload bytes.",
                    {"files": {"type": "array", "minItems": 1, "maxItems": 10, "items": {"type": "string"}},
                     "groupUniqId": ID_SCHEMA, "durationSeconds": {"type": "integer", "minimum": 1}},
                    ("files",), read_only=True),
    real_human_tool("asset_upload_execute", "Execute one prepared upload plan only after the user confirms the complete target and file list.",
                    {"uploadPlanId": ID_SCHEMA, "confirmed": {"type": "boolean"}},
                    ("uploadPlanId", "confirmed"), read_only=False),
]
MCP_TOOLS.extend(REAL_HUMAN_TOOLS)


def validate_tool_arguments(name: str, arguments: dict[str, Any]) -> None:
    tool = next((tool for tool in MCP_TOOLS if tool["name"] == name), None)
    if tool is None:
        return
    schema = tool["inputSchema"]
    if set(arguments) - schema["properties"].keys():
        raise ValueError("Unknown tool argument")
    if not set(schema.get("required", [])).issubset(arguments):
        raise ValueError("Missing required tool argument")
    for key, value in arguments.items():
        field = schema["properties"][key]
        expected = {"string": str, "integer": int, "boolean": bool,
                    "array": list, "object": dict}.get(field.get("type"))
        if expected is None and "enum" in field:
            if value not in field["enum"]:
                raise ValueError(f"{key} has an unsupported value")
            continue
        if type(value) is not expected:
            article = "an" if field["type"] == "object" else "a"
            raise ValueError(f"{key} must be {article} {field['type']}")
        if expected is str:
            if len(value) < field.get("minLength", 0) or len(value) > field.get("maxLength", math.inf):
                raise ValueError(f"{key} has an invalid length")
            if "pattern" in field and not re.fullmatch(field["pattern"], value):
                raise ValueError(f"{key} has an invalid format")
        elif expected is int and (value < field.get("minimum", -math.inf) or value > field.get("maximum", math.inf)):
            raise ValueError(f"{key} is out of range")
        elif expected is list:
            if len(value) < field.get("minItems", 0) or len(value) > field.get("maxItems", math.inf):
                raise ValueError(f"{key} has an invalid item count")
            item_type = field.get("items", {}).get("type")
            if item_type == "string" and not all(isinstance(item, str) and item for item in value):
                raise ValueError(f"{key} must contain non-empty strings")


def mcp_tool_call(name: str, arguments: dict[str, Any]) -> Any:
    validate_tool_arguments(name, arguments)
    if name == "real_human_authorization_start":
        return create_authorization(arguments["name"])
    if name == "real_human_authorization_get":
        return get_authorization(arguments["authorizationId"])
    if name == "real_human_groups_list":
        return list_real_human_groups(arguments.get("page", 1), arguments.get("pageSize", 20))
    if name == "real_human_group_rename":
        return rename_real_human_group(arguments["groupUniqId"], arguments["name"])
    if name == "real_human_group_delete":
        return delete_real_human_group(arguments["groupUniqId"], arguments["confirmed"])
    if name == "real_human_assets_list":
        return list_real_human_assets(arguments["groupUniqId"], arguments.get("page", 1), arguments.get("pageSize", 50))
    if name == "real_human_asset_delete":
        return delete_real_human_asset(arguments["groupUniqId"], arguments["assetId"], arguments["confirmed"])
    if name == "asset_get":
        return get_asset(arguments["assetId"])
    if name == "asset_upload_prepare":
        return prepare_upload_plan(arguments["files"], group_uniq_id=arguments.get("groupUniqId"),
                                   duration_seconds=arguments.get("durationSeconds"))
    if name == "asset_upload_execute":
        return execute_upload_plan(arguments["uploadPlanId"], confirmed=arguments["confirmed"])
    if name == "cli_status":
        return local_health_report(online=False)
    if name == "account_get":
        status, response = send("GET", "/api/user/me")
        account = response_data(status, response)
        if not valid_account_payload(account):
            raise ValueError("Account verification returned an incomplete response; check the active API Key locally")
        return {**public_account_data(account), "onboarding": onboarding_guidance(configured=True, verified=True)}
    if name == "capabilities_list":
        return capability_snapshot()
    if name == "capability_get":
        return find_model(str(arguments.get("model", "")))
    if name == "generation_estimate":
        return estimate_generation(str(arguments.get("kind")), arguments.get("request"))
    if name == "generation_create":
        attempt_id = arguments.get("attemptId")
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise SystemExit("attemptId is required for generation_create")
        return create_generation(
            str(arguments.get("kind")),
            arguments.get("request"),
            confirmed=arguments.get("confirmed") is True,
            attempt_id=attempt_id.strip(),
        )
    if name == "generation_get":
        task_id = public_id(arguments.get("taskId"), "taskId")
        status, response = send("GET", f"/api/tasks/{task_id}")
        return public_task_data(response_data(status, response))
    if name == "generation_attempt_list":
        return {"attempts": attempt_records()}
    if name == "generation_attempt_get":
        return attempt_record(arguments["attemptId"])
    if name == "generation_list":
        query = task_list_query(arguments.get("page", 1), arguments.get("pageSize", 20),
                                arguments.get("startDate"), arguments.get("endDate"), arguments.get("taskType"))
        status, response = send("GET", "/api/tasks", query=query)
        return public_task_data(response_data(status, response))
    raise SystemExit(f"Unknown MCP tool: {name}")


def mcp_result(identifier: Any, value: Any) -> dict[str, Any]:
    safe = sanitize_for_output(value)
    structured = safe if isinstance(safe, dict) else {"items": safe} if isinstance(safe, list) else {"value": safe}
    content = [{"type": "text", "text": json.dumps(safe, ensure_ascii=False)}]
    if isinstance(value, AuthorizationStartResult) and value.qr_bytes:
        content.append({"type": "image", "mimeType": "image/png",
                        "data": base64.b64encode(value.qr_bytes).decode("ascii")})
    return {
        "jsonrpc": "2.0",
        "id": identifier,
        "result": {
            "content": content,
            "structuredContent": structured,
            "isError": False,
        },
    }


def mcp_dispatch(message: dict[str, Any]) -> dict[str, Any] | None:
    identifier = message.get("id")
    method = message.get("method")
    if identifier is None and method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    if method == "initialize":
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return {"jsonrpc": "2.0", "id": identifier, "error": {"code": -32602, "message": "Invalid params"}}
        requested = params.get("protocolVersion")
        negotiated = requested if requested in SUPPORTED_INITIALIZE_PROTOCOLS else LATEST_INITIALIZE_PROTOCOL
        notice = cached_update_notice()
        instructions = (
            "Present user-facing results with short headings and lists, not raw JSON or long paragraphs. "
            "Before an operation explain what you will do in the user's current language. Afterward explain the result and the returned nextAction when one applies; do not invent extra steps for completed queries. "
            "Never treat authorization as upload consent or generation consent. Never retry an unknown mutation."
        )
        instructions += " " + ONBOARDING_AGENT_INSTRUCTION + " Read cli_status/account_get onboarding for the shared business examples."
        if notice:
            instructions += " " + notice
        return {
            "jsonrpc": "2.0",
            "id": identifier,
            "result": {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "holycrab-local", "version": VERSION},
                "instructions": instructions,
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": identifier, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": identifier, "result": {"tools": MCP_TOOLS}}
    if method == "tools/call":
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return {"jsonrpc": "2.0", "id": identifier, "error": {"code": -32602, "message": "Invalid params"}}
        try:
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be an object")
            warning = cleanup_authorization_qrs()
            value = mcp_tool_call(str(params.get("name", "")), arguments)
            if warning and isinstance(value, dict):
                value.setdefault("warning", warning)
            return mcp_result(identifier, value)
        except CommandInterrupted as error:
            result = mcp_result(identifier, error.result)
            result["result"]["isError"] = True
            return result
        except (SystemExit, OSError, http.client.HTTPException, ValueError, TypeError, AttributeError) as error:
            text = sanitize_text_for_output(str(error) or error.__class__.__name__)
            return {
                "jsonrpc": "2.0",
                "id": identifier,
                "result": {"content": [{"type": "text", "text": text}], "isError": True},
            }
    return {"jsonrpc": "2.0", "id": identifier, "error": {"code": -32601, "message": "Method not found"}}


def command_mcp_serve(args: argparse.Namespace) -> int:
    for raw in sys.stdin:
        try:
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise ValueError("JSON-RPC message must be an object")
            response = mcp_dispatch(message)
        except (json.JSONDecodeError, ValueError) as error:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(error)}}
        except Exception:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": "Internal MCP error"}}
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


def add_json_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", required=True, help="JSON object or @/path/to/request.json")


def add_key_input_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--stdin", action="store_true", help="Read the key from stdin; avoid shell history")
    parser.add_argument("--no-verify", action="store_true", help=argparse.SUPPRESS)


def add_wait_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=600.0)


def add_page_arguments(parser: argparse.ArgumentParser, page_size: int = 20) -> None:
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--page-size", type=int, default=page_size)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="holycrab", description="HolyCrab local CLI and MCP server")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="Configure and verify a HolyCrab API Key")
    add_key_input_arguments(setup)
    setup.set_defaults(func=command_set_key)

    auth = sub.add_parser("auth", help="Configure the local API Key")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    set_key = auth_sub.add_parser("set-key", help="Save and verify an API Key")
    add_key_input_arguments(set_key)
    set_key.set_defaults(func=command_set_key)
    auth_sub.add_parser("status", help="Check whether the configured API Key is valid").set_defaults(func=command_auth_status)
    auth_sub.add_parser("clear-key", help="Remove the saved API Key").set_defaults(func=command_clear_key)

    models = sub.add_parser("models", help="Inspect public model capabilities")
    models_sub = models.add_subparsers(dest="models_command", required=True)
    models_list = models_sub.add_parser("list")
    models_list.add_argument("--json", action="store_true")
    models_list.set_defaults(func=command_models_list)
    models_show = models_sub.add_parser("show")
    models_show.add_argument("model")
    models_show.set_defaults(func=command_models_show)

    credits = sub.add_parser("credits", help="Check the current credit balance")
    credits_sub = credits.add_subparsers(dest="credits_command", required=True, metavar="{balance}")
    credits_sub.add_parser("balance", help="Show the current credit balance").set_defaults(func=command_credits_balance)
    # Keep old scripts working without advertising a second estimate workflow.
    estimate = credits_sub.add_parser(
        "estimate", description="Compatibility alias. Use 'holycrab generate estimate' for credit estimates."
    )
    estimate.add_argument("--kind", required=True, choices=["video", "image", "audio"])
    add_json_argument(estimate)
    estimate.set_defaults(func=command_generation_estimate)

    generate = sub.add_parser("generate", help="Estimate or create generation tasks")
    generate_sub = generate.add_subparsers(dest="generate_command", required=True)
    gen_estimate = generate_sub.add_parser("estimate")
    gen_estimate.add_argument("--kind", required=True, choices=["video", "image", "audio"])
    add_json_argument(gen_estimate)
    gen_estimate.set_defaults(func=command_generation_estimate)
    gen_create = generate_sub.add_parser("create")
    gen_create.add_argument("--kind", required=True, choices=["video", "image", "audio"])
    add_json_argument(gen_create)
    gen_create.add_argument("--yes", action="store_true", help="Confirm exactly one billable task")
    gen_create.add_argument("--attempt-id", help="Local ID for this one submission attempt")
    gen_create.set_defaults(func=command_generation_create)
    attempts = generate_sub.add_parser("attempts", help="Inspect one-shot local submission records")
    attempts_sub = attempts.add_subparsers(dest="attempts_command", required=True)
    attempts_sub.add_parser("list", help="List local submission attempts").set_defaults(func=command_attempts_list)
    attempt_get = attempts_sub.add_parser("get", help="Get one local submission attempt")
    attempt_get.add_argument("attempt_id")
    attempt_get.set_defaults(func=command_attempts_get)

    tasks = sub.add_parser("tasks", help="Query generation tasks")
    tasks_sub = tasks.add_subparsers(dest="tasks_command", required=True)
    task_get = tasks_sub.add_parser("get")
    task_get.add_argument("uniq_id")
    task_get.set_defaults(func=command_task_get)
    task_list = tasks_sub.add_parser("list")
    task_list.add_argument("--page", type=int, default=1)
    task_list.add_argument("--page-size", type=int, default=20)
    task_list.add_argument("--start-date", help="Inclusive start date in YYYY-MM-DD; requires --end-date")
    task_list.add_argument("--end-date", help="Inclusive end date in YYYY-MM-DD; requires --start-date")
    task_list.add_argument("--type", choices=["IMAGE", "VIDEO", "AUDIO", "TEXT"], help="Filter by task type")
    task_list.set_defaults(func=command_task_list)
    task_wait = tasks_sub.add_parser("wait")
    task_wait.add_argument("uniq_id")
    task_wait.add_argument("--interval", type=float, default=5.0)
    task_wait.add_argument("--timeout", type=float, default=600.0)
    task_wait.set_defaults(func=command_poll_task)

    download = sub.add_parser("download", help="Download one completed task output")
    download.add_argument("uniq_id")
    download.add_argument("--output", required=True)
    download.add_argument("--index", type=int, default=0)
    download.add_argument("--force", action="store_true", help="Replace an existing output file")
    download.set_defaults(func=command_download)

    real_human = sub.add_parser("real-human", help="Authorize a real person and query their assets")
    human_sub = real_human.add_subparsers(dest="real_human_command", required=True)
    human_start = human_sub.add_parser("start", help="Create a private link and local QR image for manual verification")
    human_start.add_argument("--name", required=True)
    human_start.set_defaults(func=command_real_human_start)
    human_get = human_sub.add_parser("get", help="Query the same authorization ID without creating another")
    human_get.add_argument("authorization_id", help="Authorization ID returned by real-human start")
    human_get.set_defaults(func=command_real_human_get)
    human_wait = human_sub.add_parser("wait", help="Wait for authorization status changes; default timeout 600 seconds")
    human_wait.add_argument("authorization_id", help="Authorization ID returned by real-human start")
    add_wait_arguments(human_wait)
    human_wait.set_defaults(func=command_real_human_wait)
    groups = human_sub.add_parser("groups").add_subparsers(dest="groups_command", required=True)
    group_list = groups.add_parser("list")
    add_page_arguments(group_list)
    group_list.set_defaults(func=command_real_human_groups)
    group_rename = groups.add_parser("rename")
    group_rename.add_argument("group_id")
    group_rename.add_argument("--name", required=True)
    group_rename.set_defaults(func=command_real_human_group_rename)
    group_delete = groups.add_parser("delete")
    group_delete.add_argument("group_id")
    group_delete.add_argument("--yes", action="store_true", help="Use only after explicit approval of this permanent deletion")
    group_delete.set_defaults(func=command_real_human_group_delete)
    human_assets = human_sub.add_parser("assets").add_subparsers(dest="human_assets_command", required=True)
    human_asset_list = human_assets.add_parser("list")
    human_asset_list.add_argument("--group", required=True)
    add_page_arguments(human_asset_list, 50)
    human_asset_list.set_defaults(func=command_real_human_assets)
    human_asset_delete = human_assets.add_parser("delete")
    human_asset_delete.add_argument("asset_id")
    human_asset_delete.add_argument("--group", required=True)
    human_asset_delete.add_argument("--yes", action="store_true", help="Use only after explicit approval of this permanent deletion")
    human_asset_delete.set_defaults(func=command_real_human_asset_delete)

    assets = sub.add_parser("assets", help="Upload and query media assets")
    assets_sub = assets.add_subparsers(dest="assets_command", required=True)
    upload = assets_sub.add_parser("upload", help="Preview 1-10 files, confirm once, then upload in order")
    upload.add_argument("file", nargs="+", help="One to ten local image/video/audio files")
    upload.add_argument("--duration-seconds", type=int, help="Known media duration sent for server validation")
    upload.add_argument("--real-human-group", help="Authorized person's public group ID; real-human formats apply")
    upload.add_argument("--yes", action="store_true", help="Confirm the complete printed file list and target")
    upload.set_defaults(func=command_upload_asset)
    asset_get = assets_sub.add_parser("get", help="Query one asset and receive its next action")
    asset_get.add_argument("uniq_id", help="Public asset ID")
    asset_get.set_defaults(func=command_asset_get)
    asset_wait = assets_sub.add_parser("wait", help="Wait for one or more assets; default timeout 600 seconds each")
    asset_wait.add_argument("uniq_id", nargs="+", help="One or more public asset IDs")
    add_wait_arguments(asset_wait)
    asset_wait.set_defaults(func=command_asset_wait)

    mcp = sub.add_parser("mcp", help="Run the local stdio MCP server")
    mcp_sub = mcp.add_subparsers(dest="mcp_command", required=True)
    mcp_sub.add_parser("serve").set_defaults(func=command_mcp_serve)
    update = sub.add_parser("update", help="Check for or install a verified stable CLI release")
    update.add_argument("--check", action="store_true", help="Only check; never run an installer")
    update.add_argument("--yes", action="store_true", help="Install after release and digest verification")
    update.set_defaults(func=command_update)
    doctor = sub.add_parser("doctor", help="Check the local installation")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--online", action="store_true", help="Refresh release status and verify the API Key")
    doctor.set_defaults(func=command_doctor)
    uninstall = sub.add_parser("uninstall", help="Remove this installer-managed HolyCrab CLI")
    uninstall.add_argument("--purge", action="store_true", help="Also remove known local credentials and records")
    uninstall.add_argument("--yes", action="store_true", help="Confirm the complete uninstall preview")
    uninstall.set_defaults(func=command_uninstall)
    return parser


def startup_maintenance(raw_args: list[str]) -> None:
    if raw_args and raw_args[0] in {"doctor", "uninstall"}:
        return
    try:
        report = local_health_report(online=False)
        for repair in report.get("repairs", []):
            print(f"HolyCrab local check needs attention. Repair: {repair}", file=sys.stderr)
        if raw_args and raw_args[0] == "update":
            return
        check_for_update(force=False, timeout=UPDATE_CHECK_TIMEOUT)
        notice = cached_update_notice()
        if notice:
            print(notice, file=sys.stderr)
    except (Exception, SystemExit):
        # Maintenance can never block the requested business command.
        return


def command_failure_feedback(args: argparse.Namespace) -> None:
    if args.func in {command_set_key, command_auth_status, command_credits_balance}:
        command_progress("Check your connection and whether the API Key is enabled.\nNext:\n  holycrab setup\nDo not share your API Key in chat.")
    elif args.func in {command_task_get, command_poll_task, command_download}:
        identifier = args.uniq_id
        command = f"holycrab tasks get {identifier}" if re.fullmatch(r"[A-Za-z0-9]{1,64}", identifier) else "holycrab tasks list"
        command_progress(f"Check this existing task; do not submit a replacement.\nNext:\n  {command}")
    elif args.func in {command_generation_create, command_upload_asset, command_real_human_start,
                        command_real_human_group_rename, command_real_human_group_delete, command_real_human_asset_delete}:
        command_progress("Review the result and existing records before deciding.\nDo not automatically repeat a write with an uncertain result.")
    elif args.func in {command_generation_estimate, command_task_list, command_real_human_get,
                        command_real_human_wait, command_real_human_groups, command_real_human_assets,
                        command_asset_get, command_asset_wait}:
        command_progress("Check your connection and credentials, then query the same records.\nDo not create a new task, authorization or upload to resume a query.")
    elif args.func in {command_doctor, command_update}:
        command_progress("Review the checks; reinstall only if a repair is needed.\nNext:\n  holycrab doctor")


def main() -> int:
    raw_args = sys.argv[1:]
    args = build_parser().parse_args()
    try:
        # Semantic argument checks also precede housekeeping and version requests.
        if args.func in {command_poll_task, command_real_human_wait, command_asset_wait}:
            validate_wait_limits(args.timeout, args.interval)
        if args.func in {command_task_get, command_poll_task, command_download}:
            try:
                public_id(args.uniq_id, "taskId")
            except ValueError as error:
                raise CommandUsageError(str(error)) from error
        if args.func == command_task_list:
            task_list_query(args.page, args.page_size, args.start_date, args.end_date, args.type)
        if args.func in {command_real_human_groups, command_real_human_assets}:
            page_query(args.page, args.page_size)
        if raw_args[0] != "uninstall":
            warning = cleanup_authorization_qrs()
            if warning:
                print(warning, file=sys.stderr)
            cleanup_upload_plans()
        startup_maintenance(raw_args)
        code = args.func(args)
        if code == 1 and not (sys.stdout.isatty() and args.func in
                             {command_auth_status, command_generation_create, command_doctor, command_update}):
            command_failure_feedback(args)
        return code
    except CommandInterrupted as error:
        print_result(error.result)
        print("Stopped after the operation began. Review its state; do not retry automatically.", file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        print("Stopped. Ctrl+C ended this local operation; it does not cancel an existing online task or authorization.", file=sys.stderr)
        return 130
    except CommandUsageError as error:
        print(f"Invalid argument: {sanitize_text_for_output(str(error))}", file=sys.stderr)
        return 2
    except SystemExit as error:
        if isinstance(error.code, int):
            return error.code
        print(f"Error: {sanitize_text_for_output(str(error))}", file=sys.stderr)
        command_failure_feedback(args)
        return 1
    except json.JSONDecodeError as error:
        print(f"Invalid JSON: {error}", file=sys.stderr)
        return 2
    except urllib.error.URLError as error:
        print(f"Network error: {sanitize_text_for_output(str(error.reason))}", file=sys.stderr)
        command_failure_feedback(args)
        return 1
    except (ValueError, OSError, http.client.HTTPException) as error:
        print(f"Error: {sanitize_text_for_output(str(error))}", file=sys.stderr)
        command_failure_feedback(args)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
