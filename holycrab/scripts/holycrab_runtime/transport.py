"""HolyCrab transport responsibilities."""
from __future__ import annotations
from . import results as _results

import ipaddress
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any
from . import presentation as _presentation
from . import settings as _settings
from . import state as _state

def base_url() -> str:
    if "HOLYCRAB_BASE_URL" in os.environ:
        raise SystemExit(
            "Custom API origins are not supported. Run `" + _results.clear_environment_command("HOLYCRAB_BASE_URL") + "` and try again."
        )
    return _settings.DEFAULT_BASE_URL


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
    header_name, header_value = _state.credential(api_key)
    headers = {
        header_name: header_value,
        "Accept": "application/json",
        "User-Agent": f"holycrab-cli/{_settings.VERSION}",
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




def response_data(status: int, response: Any) -> Any:
    _presentation.remember_private_fields(response)
    if not _results.response_ok(status, response):
        message = response.get("message") if isinstance(response, dict) else None
        request_id = response.get("requestId") if isinstance(response, dict) else None
        detail = f": {_presentation.sanitize_text_for_output(str(message))}" if message else ""
        if request_id:
            detail += f" (requestId: {_presentation.sanitize_text_for_output(str(request_id))})"
        raise SystemExit(f"HolyCrab API request failed with HTTP {status}{detail}")
    return response.get("data") if isinstance(response, dict) and "data" in response else response


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
        if count > _settings.MAX_DOWNLOAD_REDIRECTS:
            raise urllib.error.HTTPError(newurl, code, "too many download redirects", headers, fp)
        validated = validate_download_url(urllib.parse.urljoin(req.full_url, newurl))
        redirected = super().redirect_request(req, fp, code, msg, headers, validated)
        if redirected is not None:
            setattr(redirected, "holycrab_redirects", count)
        return redirected


def open_download(url: str) -> Any:
    request = urllib.request.Request(validate_download_url(url), headers={"User-Agent": f"holycrab-cli/{_settings.VERSION}"})
    return urllib.request.build_opener(SecureDownloadRedirectHandler()).open(request, timeout=300)


class FileChunks:
    def __init__(self, stream: Any) -> None:
        self.stream = stream

    def __iter__(self):
        self.stream.seek(0)
        while chunk := self.stream.read(1024 * 1024):
            yield chunk
