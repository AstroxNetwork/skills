#!/usr/bin/env python3
"""Dependency-free HolyCrab CLI and local stdio MCP server."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import html
import importlib.util
import json
import mimetypes
import os
import re
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
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


VERSION = "0.2.1"
DEFAULT_BASE_URL = "https://abgzfc.holycrab.ai"
PUBLIC_ACCOUNT_URL = "https://generate.holycrab.ai/user-tokens"
LATEST_INITIALIZE_PROTOCOL = "2025-11-25"
SUPPORTED_INITIALIZE_PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
SENSITIVE_OUTPUT_KEYS = {
    "presignedurl", "authorization", "xusertoken", "apikey", "accesstoken", "refreshtoken"
}
SENSITIVE_OUTPUT_VALUES: set[str] = set()
SENSITIVE_URL_QUERY_KEYS = {
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
PUBLIC_ACCOUNT_FIELDS = ("username", "email", "nickname", "credit", "inviteCode")
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
    "videoIds",
    "imageIds",
    "cdnUrl",
    "textResult",
    "createTime",
    "updateTime",
)


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
    return value


def save_config(value: dict[str, Any]) -> None:
    write_private_json(config_path(), value)


def load_attempts() -> dict[str, Any]:
    value = read_json_file(attempts_path(), {})
    if not isinstance(value, dict):
        raise SystemExit("HolyCrab attempts ledger must contain a JSON object")
    return value


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
    configured = os.environ.get("HOLYCRAB_BASE_URL") or load_config().get("baseUrl") or DEFAULT_BASE_URL
    value = str(configured).strip()
    parsed = urllib.parse.urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise SystemExit(f"HOLYCRAB_BASE_URL must be a credential-free HTTPS origin: {error}") from error
    if (
        parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username is not None
        or parsed.password is not None or parsed.path not in ("", "/") or parsed.query or parsed.fragment
    ):
        raise SystemExit("HOLYCRAB_BASE_URL must be a credential-free HTTPS origin")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return f"https://{host}{f':{port}' if port is not None else ''}"


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
    for secret in sorted(SENSITIVE_OUTPUT_VALUES, key=len, reverse=True):
        sanitized = sanitized.replace(secret, "<redacted-secret>")
    return sanitized


def sanitize_for_output(value: Any) -> Any:
    if isinstance(value, dict):
        output: dict[Any, Any] = {}
        for key, item in value.items():
            normalized = normalized_name(key)
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
    return {key: value[key] for key in PUBLIC_TASK_FIELDS if key in value}


def public_task_response(response: Any) -> Any:
    return public_envelope(response, public_task_data)


def print_json(value: Any) -> None:
    print(json.dumps(sanitize_for_output(value), ensure_ascii=False, indent=2))


def print_response(status: int, response: Any) -> int:
    print_json({"httpStatus": status, "response": response})
    return 0 if response_ok(status, response) else 1


def capabilities_path() -> Path:
    override = os.environ.get("HOLYCRAB_CAPABILITIES_PATH")
    candidates = [
        Path(override).expanduser() if override else None,
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
    output = []
    for kind, group in (("video", "videoModels"), ("image", "imageModels"), ("audio", "audioModels")):
        for model in manifest.get(group, []):
            output.append({"kind": kind, **model})
    return output


def find_model(model_id: str) -> dict[str, Any]:
    for model in all_models():
        if model.get("id") == model_id:
            return model
    raise SystemExit(f"Unknown public model: {model_id}. Run `holycrab models list`.")


def generation_endpoints(kind: str, payload: dict[str, Any]) -> tuple[str, str]:
    if kind not in {"video", "image", "audio"}:
        raise SystemExit("Generation kind must be video, image, or audio")
    if kind == "audio":
        return "/api/tasks/audio-generation/freeze-credit", "/api/tasks/audio-generation"
    model_id = payload.get("model")
    if not isinstance(model_id, str):
        raise SystemExit("Generation request must include model")
    model = find_model(model_id)
    if model["kind"] != kind:
        raise SystemExit(f"Model {model_id} is a {model['kind']} model, not {kind}")
    endpoints = model["endpoints"]
    return endpoints["freezeCredit"], endpoints["create"]


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
    return normalized


def canonical_request_hash(kind: str, payload: dict[str, Any]) -> str:
    raw = json.dumps(
        {"kind": kind, "request": payload}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def update_attempt(attempt_id: str, **changes: Any) -> None:
    with attempts_lock():
        attempts = load_attempts()
        record = attempts.get(attempt_id, {})
        record.update(changes)
        record["updatedAt"] = utc_now()
        attempts[attempt_id] = record
        save_attempts(attempts)


def attempt_exists(attempt_id: str) -> bool:
    with attempts_lock():
        return attempt_id in load_attempts()


def reserve_attempt(attempt_id: str, record: dict[str, Any]) -> None:
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
        return value if isinstance(value, str) else None
    return None


def estimate_generation(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_generation_request(kind, payload)
    freeze_endpoint, _ = generation_endpoints(kind, normalized)
    status, response = send("POST", freeze_endpoint, payload=None if kind == "audio" else normalized)
    return {"kind": kind, "endpoint": freeze_endpoint, "estimate": response_data(status, response)}


def create_generation(
    kind: str,
    payload: dict[str, Any],
    *,
    confirmed: bool,
    attempt_id: str | None = None,
    approved_estimate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = normalize_generation_request(kind, payload)
    selected_attempt = attempt_id or uuid.uuid4().hex
    if confirmed and attempt_exists(selected_attempt):
        raise SystemExit(
            f"Local submission attempt {selected_attempt} already exists; query it instead of submitting again"
        )
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
            "state": "submitting",
            "createdAt": utc_now(),
            "endpoint": create_endpoint,
        },
    )
    try:
        status, response = send("POST", create_endpoint, payload=payload)
    except (urllib.error.URLError, TimeoutError):
        update_attempt(
            selected_attempt,
            state="unknown",
            note="Network outcome was ambiguous; do not resubmit this attempt.",
        )
        raise
    if not response_ok(status, response):
        update_attempt(selected_attempt, state="failed", httpStatus=status)
        response_data(status, response)
    task_id = extract_task_id(response)
    update_attempt(selected_attempt, state="created", taskId=task_id, httpStatus=status)
    return {
        "attemptId": selected_attempt,
        "taskId": task_id,
        "state": "created",
        "estimate": estimate["estimate"],
        "response": public_task_data(response_data(status, response)),
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
        print(f"Create or copy an API Key at: {PUBLIC_ACCOUNT_URL}")
        api_key = getpass.getpass("Paste API Key (input hidden): ").strip()
    if not api_key:
        raise SystemExit("API Key cannot be empty")
    account = None
    if not args.no_verify:
        status, response = send("GET", "/api/user/me", api_key=api_key)
        account = response_data(status, response)
    config = load_config()
    config["apiKey"] = api_key
    save_config(config)
    print("HolyCrab API Key saved locally with user-only permissions.")
    if os.environ.get("HOLYCRAB_API_KEY"):
        print("Warning: HOLYCRAB_API_KEY is still set and overrides the saved login.")
        print("Run: unset HOLYCRAB_API_KEY")
    if isinstance(account, dict):
        visible = {key: account.get(key) for key in ("email", "nickname", "credit") if key in account}
        if visible:
            print_json(visible)
    return 0


def command_auth_status(args: argparse.Namespace) -> int:
    source = (
        "environment" if os.environ.get("HOLYCRAB_API_KEY")
        else "local config" if load_config().get("apiKey") else None
    )
    if not source:
        print_json({"configured": False, "valid": False, "next": "Run `holycrab setup`."})
        return 1
    status, response = send("GET", "/api/user/me")
    if not response_ok(status, response):
        print_json({"configured": True, "valid": False, "credentialSource": source, "httpStatus": status})
        return 1
    print_json({"configured": True, "valid": True, "credentialSource": source, "account": public_account_data(response_data(status, response))})
    return 0


def command_clear_key(args: argparse.Namespace) -> int:
    config = load_config()
    config.pop("apiKey", None)
    save_config(config)
    print("Saved HolyCrab API Key cleared. Environment variables were not changed.")
    return 0


def command_models_list(args: argparse.Namespace) -> int:
    models = all_models()
    brief = [{"id": item["id"], "label": item.get("label"), "kind": item["kind"]} for item in models]
    print_json(models if args.json else brief)
    return 0


def command_models_show(args: argparse.Namespace) -> int:
    print_json(find_model(args.model))
    return 0


def command_credits_balance(args: argparse.Namespace) -> int:
    status, response = send("GET", "/api/user/me")
    return print_response(status, public_account_response(response))


def command_generation_estimate(args: argparse.Namespace) -> int:
    print_json(estimate_generation(args.kind, parse_json_argument(args.json)))
    return 0


def command_generation_create(args: argparse.Namespace) -> int:
    payload = parse_json_argument(args.json)
    approved_estimate = None
    if not args.yes:
        approved_estimate = create_generation(args.kind, payload, confirmed=False)
        print_json(approved_estimate)
        if not sys.stdin.isatty():
            print("Not submitted. Re-run with --yes only after the user confirms the estimate.", file=sys.stderr)
            return 2
        answer = input("Create one billable task with this request? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Cancelled; no generation task was created.")
            return 2
    print_json(
        create_generation(
            args.kind,
            payload,
            confirmed=True,
            attempt_id=args.attempt_id,
            approved_estimate=approved_estimate,
        )
    )
    return 0


def command_task_get(args: argparse.Namespace) -> int:
    status, response = send("GET", f"/api/tasks/{urllib.parse.quote(args.uniq_id, safe='')}")
    return print_response(status, public_task_response(response))


def command_task_list(args: argparse.Namespace) -> int:
    status, response = send(
        "GET", "/api/tasks", query=[("page", str(args.page)), ("pageSize", str(args.page_size))]
    )
    return print_response(status, public_task_response(response))


def poll_task(uniq_id: str, timeout: float, interval: float, *, emit: bool = False) -> tuple[int, Any]:
    deadline = time.monotonic() + timeout
    latest: Any = None
    while True:
        status, latest = send("GET", f"/api/tasks/{urllib.parse.quote(uniq_id, safe='')}")
        if emit:
            print_response(status, public_task_response(latest))
        if not response_ok(status, latest):
            return 1, latest
        data = response_data(status, latest)
        step = data.get("step") if isinstance(data, dict) else None
        if step == 2:
            return 0, latest
        if step == 3:
            return 1, latest
        if time.monotonic() >= deadline:
            return 2, latest
        time.sleep(interval)


def command_poll_task(args: argparse.Namespace) -> int:
    code, _ = poll_task(args.uniq_id, args.timeout, args.interval, emit=True)
    if code == 2:
        print("Polling timed out; the task was not resubmitted.", file=sys.stderr)
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
    return values


def validate_download_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise SystemExit("Task output must be a credential-free HTTPS URL")
    return value


def command_download(args: argparse.Namespace) -> int:
    status, response = send("GET", f"/api/tasks/{urllib.parse.quote(args.uniq_id, safe='')}")
    data = response_data(status, response)
    urls = task_output_urls(data)
    if not urls:
        raise SystemExit("This task has no downloadable URL yet")
    if args.index < 0 or args.index >= len(urls):
        raise SystemExit(f"Output index must be between 0 and {len(urls) - 1}")
    url = validate_download_url(urls[args.index])
    destination = Path(args.output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, partial_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".part", dir=destination.parent
    )
    partial = Path(partial_name)
    total = 0
    try:
        with urllib.request.urlopen(url, timeout=300) as remote, os.fdopen(descriptor, "wb") as local:
            descriptor = -1
            while True:
                chunk = remote.read(1024 * 1024)
                if not chunk:
                    break
                local.write(chunk)
                total += len(chunk)
        os.replace(partial, destination)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            partial.unlink()
        except FileNotFoundError:
            pass
    print_json({"taskId": args.uniq_id, "output": str(destination), "bytes": total})
    return 0


def upload_asset(
    file: str,
    content_type: str | None = None,
    duration_seconds: int | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    path = Path(file).expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"file not found: {path}")
    mime = content_type or mimetypes.guess_type(path.name)[0]
    if not mime:
        raise SystemExit("Could not infer MIME type; pass --content-type")
    query = [("file_extension", path.suffix.lstrip(".")), ("content_type", mime)]
    if duration_seconds is not None:
        query.append(("duration_seconds", str(duration_seconds)))
    status, response = send("GET", "/api/user-assets/pre-signed-download-url", query=query)
    data = response_data(status, response)
    if not isinstance(data, dict):
        raise SystemExit("Presign response is missing data")
    presigned_url = validate_presigned_upload_url(data.get("preSignedUrl"))
    object_key = data.get("objectKey")
    if not isinstance(object_key, str):
        raise SystemExit("Presign response omitted objectKey")
    upload_request = urllib.request.Request(
        presigned_url, data=path.read_bytes(), headers={"Content-Type": mime}, method="PUT"
    )
    with urllib.request.urlopen(upload_request, timeout=300) as upload_response:
        if not 200 <= upload_response.status < 300:
            raise SystemExit(f"Upload failed with HTTP {upload_response.status}")
    payload: dict[str, Any] = {
        "name": name or path.name,
        "object_key": object_key,
        "content_type": mime,
    }
    if duration_seconds is not None:
        payload["duration_seconds"] = duration_seconds
    register_status, register_response = send("POST", "/api/user-assets/upload", form=payload)
    return {
        "assetUniqId": data.get("uniqId"),
        "registration": response_data(register_status, register_response),
    }


def command_upload_asset(args: argparse.Namespace) -> int:
    print_json(upload_asset(args.file, args.content_type, args.duration_seconds, args.name))
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    report = {
        "ok": True,
        "version": VERSION,
        "python": sys.version.split()[0],
        "apiOrigin": base_url(),
        "capabilities": str(capabilities_path()),
        "config": str(config_path()),
        "keyConfigured": bool(os.environ.get("HOLYCRAB_API_KEY") or load_config().get("apiKey")),
        "mcpCommand": "holycrab mcp serve",
    }
    print_json(report)
    return 0


MCP_TOOLS = [
    {"name": "account_get", "description": "Check the current HolyCrab account and credit balance.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}},
    {"name": "capabilities_list", "description": "List current public generation capabilities.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "capability_get", "description": "Get limits for one public model.", "inputSchema": {"type": "object", "properties": {"model": {"type": "string"}}, "required": ["model"], "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "generation_estimate", "description": "Estimate credit without creating a task.", "inputSchema": {"type": "object", "properties": {"kind": {"enum": ["video", "image", "audio"]}, "request": {"type": "object"}}, "required": ["kind", "request"], "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}},
    {"name": "generation_create", "description": "Create exactly one billable generation after explicit user confirmation. Supply one stable attemptId per confirmed draw and reuse it only when reconciling a retry.", "inputSchema": {"type": "object", "properties": {"kind": {"enum": ["video", "image", "audio"]}, "request": {"type": "object"}, "confirmed": {"type": "boolean"}, "attemptId": {"type": "string", "minLength": 1, "description": "Client-generated stable ID for this one confirmed draw."}}, "required": ["kind", "request", "confirmed", "attemptId"], "additionalProperties": False}, "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}},
    {"name": "generation_get", "description": "Get one generation task by ID.", "inputSchema": {"type": "object", "properties": {"taskId": {"type": "string"}}, "required": ["taskId"], "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}},
    {"name": "generation_list", "description": "List recent generation tasks.", "inputSchema": {"type": "object", "properties": {"page": {"type": "integer", "minimum": 1}, "pageSize": {"type": "integer", "minimum": 1, "maximum": 100}}, "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}},
]


def mcp_tool_call(name: str, arguments: dict[str, Any]) -> Any:
    if name == "account_get":
        status, response = send("GET", "/api/user/me")
        return public_account_data(response_data(status, response))
    if name == "capabilities_list":
        return all_models()
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
        task_id = urllib.parse.quote(str(arguments.get("taskId", "")), safe="")
        status, response = send("GET", f"/api/tasks/{task_id}")
        return public_task_data(response_data(status, response))
    if name == "generation_list":
        query = [("page", str(arguments.get("page", 1))), ("pageSize", str(arguments.get("pageSize", 20)))]
        status, response = send("GET", "/api/tasks", query=query)
        return public_task_data(response_data(status, response))
    raise SystemExit(f"Unknown MCP tool: {name}")


def mcp_result(identifier: Any, value: Any) -> dict[str, Any]:
    safe = sanitize_for_output(value)
    structured = safe if isinstance(safe, dict) else {"items": safe} if isinstance(safe, list) else {"value": safe}
    return {
        "jsonrpc": "2.0",
        "id": identifier,
        "result": {
            "content": [{"type": "text", "text": json.dumps(safe, ensure_ascii=False)}],
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
        return {
            "jsonrpc": "2.0",
            "id": identifier,
            "result": {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "holycrab-local", "version": VERSION},
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
            value = mcp_tool_call(str(params.get("name", "")), arguments)
            return mcp_result(identifier, value)
        except (SystemExit, urllib.error.URLError, TimeoutError, ValueError, TypeError, AttributeError) as error:
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

    credits = sub.add_parser("credits", help="Check balance or estimate a request")
    credits_sub = credits.add_subparsers(dest="credits_command", required=True)
    credits_sub.add_parser("balance").set_defaults(func=command_credits_balance)
    estimate = credits_sub.add_parser("estimate")
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

    tasks = sub.add_parser("tasks", help="Query generation tasks")
    tasks_sub = tasks.add_subparsers(dest="tasks_command", required=True)
    task_get = tasks_sub.add_parser("get")
    task_get.add_argument("uniq_id")
    task_get.set_defaults(func=command_task_get)
    task_list = tasks_sub.add_parser("list")
    task_list.add_argument("--page", type=int, default=1)
    task_list.add_argument("--page-size", type=int, default=20)
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
    download.set_defaults(func=command_download)

    assets = sub.add_parser("assets", help="Manage local media assets")
    assets_sub = assets.add_subparsers(dest="assets_command", required=True)
    upload = assets_sub.add_parser("upload")
    upload.add_argument("file")
    upload.add_argument("--content-type")
    upload.add_argument("--duration-seconds", type=int)
    upload.add_argument("--name")
    upload.set_defaults(func=command_upload_asset)

    mcp = sub.add_parser("mcp", help="Run the local stdio MCP server")
    mcp_sub = mcp.add_subparsers(dest="mcp_command", required=True)
    mcp_sub.add_parser("serve").set_defaults(func=command_mcp_serve)
    doctor = sub.add_parser("doctor", help="Check the local installation")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=command_doctor)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except json.JSONDecodeError as error:
        print(f"Invalid JSON: {error}", file=sys.stderr)
        return 2
    except urllib.error.URLError as error:
        print(f"Network error: {sanitize_text_for_output(str(error.reason))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
