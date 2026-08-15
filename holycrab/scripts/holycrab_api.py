#!/usr/bin/env python3
"""Small, dependency-free HolyCrab API client."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "https://abgzfc.holycrab.ai"


def credential() -> tuple[str, str]:
    value = os.environ.get("HOLYCRAB_API_KEY")
    if not value:
        raise SystemExit("HOLYCRAB_API_KEY is required")
    return "X-User-Token", value


def base_url() -> str:
    value = os.environ.get("HOLYCRAB_BASE_URL", DEFAULT_BASE_URL).strip()
    parsed = urllib.parse.urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise SystemExit(f"HOLYCRAB_BASE_URL must be a credential-free HTTPS origin: {error}") from error
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
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
    effective_port = port if port is not None else 443 if scheme == "https" else 80
    return scheme, parsed.hostname.lower(), effective_port


class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects only when an authenticated request stays on its origin."""

    def __init__(self, trusted_origin: str) -> None:
        super().__init__()
        self.trusted_origin = url_origin(trusted_origin)

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
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
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise SystemExit("Presigned upload URL must be a credential-free HTTPS URL")
    return value


def sanitize_for_output(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            normalized_key = "".join(character for character in str(key).lower() if character.isalnum())
            sanitized[key] = (
                "<redacted-presigned-url>"
                if normalized_key == "presignedurl"
                else sanitize_for_output(item)
            )
        return sanitized
    if isinstance(value, list):
        return [sanitize_for_output(item) for item in value]
    return value


def parse_pairs(pairs: list[str]) -> list[tuple[str, str]]:
    result = []
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"query must be KEY=VALUE: {pair}")
        result.append(tuple(pair.split("=", 1)))
    return result


def decode_json(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def send(
    method: str,
    path: str,
    *,
    query: list[tuple[str, str]] | None = None,
    payload: Any = None,
) -> tuple[int, Any]:
    method = method.upper()
    if not path.startswith("/"):
        raise SystemExit("API path must start with /")
    trusted_base_url = base_url()
    url = trusted_base_url + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    header_name, header_value = credential()
    headers = {header_name: header_value, "Accept": "application/json"}
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        opener = urllib.request.build_opener(SameOriginRedirectHandler(trusted_base_url))
        with opener.open(request, timeout=60) as response:
            return response.status, decode_json(response.read())
    except urllib.error.HTTPError as error:
        return error.code, decode_json(error.read())


def print_response(status: int, response: Any) -> int:
    output = sanitize_for_output({"httpStatus": status, "response": response})
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if status < 200 or status >= 300:
        return 1
    if isinstance(response, dict) and response.get("code", 0) != 0:
        return 1
    return 0


def command_request(args: argparse.Namespace) -> int:
    payload = json.loads(args.json) if args.json is not None else None
    status, response = send(
        args.method,
        args.path,
        query=parse_pairs(args.query),
        payload=payload,
    )
    return print_response(status, response)


def command_poll_task(args: argparse.Namespace) -> int:
    deadline = time.monotonic() + args.timeout
    while True:
        status, response = send("GET", f"/api/tasks/{urllib.parse.quote(args.uniq_id, safe='')}")
        print_response(status, response)
        if status < 200 or status >= 300:
            return 1
        data = response.get("data") if isinstance(response, dict) else None
        step = data.get("step") if isinstance(data, dict) else None
        if step == 2:
            return 0
        if step == 3:
            return 1
        if time.monotonic() >= deadline:
            print("Polling timed out.", file=sys.stderr)
            return 2
        time.sleep(args.interval)


def command_upload_asset(args: argparse.Namespace) -> int:
    path = Path(args.file).expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"file not found: {path}")
    content_type = args.content_type or mimetypes.guess_type(path.name)[0]
    if not content_type:
        raise SystemExit("Could not infer MIME type; pass --content-type")
    query = [("file_extension", path.suffix.lstrip(".")), ("content_type", content_type)]
    if args.duration_seconds is not None:
        query.append(("duration_seconds", str(args.duration_seconds)))
    status, response = send("GET", "/api/user-assets/pre-signed-download-url", query=query)
    if status < 200 or status >= 300 or not isinstance(response, dict) or response.get("code", 0) != 0:
        return print_response(status, response)
    data = response.get("data") or {}
    presigned_url = data.get("preSignedUrl")
    object_key = data.get("objectKey")
    uniq_id = data.get("uniqId")
    if not presigned_url or not object_key:
        print("Presign response omitted preSignedUrl or objectKey.", file=sys.stderr)
        return 1
    presigned_url = validate_presigned_upload_url(presigned_url)

    upload_request = urllib.request.Request(
        presigned_url,
        data=path.read_bytes(),
        headers={"Content-Type": content_type},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(upload_request, timeout=300) as upload_response:
            if upload_response.status < 200 or upload_response.status >= 300:
                print(f"Upload failed with HTTP {upload_response.status}", file=sys.stderr)
                return 1
    except urllib.error.HTTPError as error:
        print(f"Upload failed with HTTP {error.code}", file=sys.stderr)
        return 1

    payload = {"name": args.name or path.name, "object_key": object_key, "content_type": content_type}
    if args.duration_seconds is not None:
        payload["duration_seconds"] = args.duration_seconds
    register_status, register_response = send("POST", "/api/user-assets/upload", payload=payload)
    output = {
        "assetUniqId": uniq_id,
        "registrationHttpStatus": register_status,
        "response": register_response,
    }
    print(json.dumps(sanitize_for_output(output), ensure_ascii=False, indent=2))
    envelope_ok = not isinstance(register_response, dict) or register_response.get("code", 0) == 0
    return 0 if 200 <= register_status < 300 and envelope_ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Call the HolyCrab Generation API")
    subparsers = parser.add_subparsers(dest="command", required=True)

    request = subparsers.add_parser("request", help="Call an API endpoint")
    request.add_argument("method", choices=["GET", "POST", "PUT", "PATCH", "DELETE"])
    request.add_argument("path")
    request.add_argument("--query", action="append", default=[], metavar="KEY=VALUE")
    request.add_argument("--json", help="JSON request body")
    request.set_defaults(func=command_request)

    poll = subparsers.add_parser("poll-task", help="Poll until a task completes or fails")
    poll.add_argument("uniq_id")
    poll.add_argument("--interval", type=float, default=5.0)
    poll.add_argument("--timeout", type=float, default=600.0)
    poll.set_defaults(func=command_poll_task)

    upload = subparsers.add_parser("upload-asset", help="Upload and register a local asset")
    upload.add_argument("file")
    upload.add_argument("--content-type")
    upload.add_argument("--duration-seconds", type=int)
    upload.add_argument("--name")
    upload.set_defaults(func=command_upload_asset)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except json.JSONDecodeError as error:
        print(f"Invalid JSON: {error}", file=sys.stderr)
        return 2
    except urllib.error.URLError as error:
        print(f"Network error: {error.reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
