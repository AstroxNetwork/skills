"""HolyCrab real human responsibilities."""
from __future__ import annotations

import http.client
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from . import presentation as _presentation
from . import results as _results
from . import settings as _settings
from . import state as _state
from . import tasks as _tasks
from . import transport as _transport

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




def page_query(page: int = 1, page_size: int = 20) -> list[tuple[str, str]]:
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 100:
        raise _results.CommandUsageError("page must be a positive integer and pageSize must be an integer from 1 to 100")
    return [("page", str(page)), ("pageSize", str(page_size))]


def authorization_cache_dir(*, create: bool = True) -> Path:
    root = _state.config_dir().absolute() / "real-human"
    if root.is_symlink():
        raise ValueError("Authorization cache must not be a symbolic link")
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.chmod(0o700)
    return root


def remove_authorization_qr(authorization_id: str) -> None:
    directory = authorization_cache_dir(create=False) / _presentation.public_id(authorization_id, "authorizationId")
    if directory.is_symlink() or not directory.is_dir():
        return
    for name in ("qr.png", "metadata.json"):
        (directory / name).unlink(missing_ok=True)
    try:
        directory.rmdir()
    except OSError:
        pass


def cleanup_authorization_qrs() -> str | None:
    try:
        key = str(_state.config_dir().resolve())
        now = time.monotonic()
        previous = _settings._QR_CLEANUP_AT.get(key)
        if previous is not None and 0 <= now - previous < 5:
            return None
        if len(_settings._QR_CLEANUP_AT) >= 128 and key not in _settings._QR_CLEANUP_AT:
            oldest = min(_settings._QR_CLEANUP_AT, key=_settings._QR_CLEANUP_AT.get)
            _settings._QR_CLEANUP_AT.pop(oldest)
        _settings._QR_CLEANUP_AT[key] = now
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
    wheel = Path(str(_settings.RUNTIME_ROOT / "holycrab_cli.py")).resolve().parent / "vendor" / "segno-1.6.6-py3-none-any.whl"
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
    directory = authorization_cache_dir() / _presentation.public_id(authorization_id, "authorizationId")
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
    _state.write_private_json(directory / "metadata.json", {"deleteAfter": deadline})
    raw = qr_png(link)
    path = directory / "qr.png"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
    return str(path), raw


def mutation_response_data(status: int, response: Any, context: str) -> Any:
    _presentation.remember_private_fields(response)
    code = response.get("code") if isinstance(response, dict) else None
    uncertain = status >= 500 or status == 408 or (type(code) is int and code >= 500)
    malformed = 200 <= status < 300 and (not isinstance(response, dict) or code is None)
    if uncertain or malformed:
        raise ValueError(f"{context} outcome is uncertain (HTTP {status}); query existing records, do not retry automatically")
    return _transport.response_data(status, response)


def authorization_next_action(status: str, authorization_id: str, group_id: str | None = None) -> dict[str, Any]:
    if status == "CREATED":
        return _results.next_action(
            "WAIT_FOR_AUTHORIZATION",
            "The person must open the private link or scan the QR code on their phone, complete verification, and confirm it themselves. Query this same authorization ID; do not create another one.",
            f"holycrab real-human wait {authorization_id} --timeout 600",
        )
    if status == "SUCCEEDED" and group_id:
        return _results.next_action(
            "UPLOAD_ASSETS",
            "Authorization succeeded and the person group was created. Authorization does not create assets. Select this person's files, preview an upload to this group, and confirm it separately. Paid generation also requires separate confirmation.",
            None,
        )
    if status == "EXPIRED":
        return _results.next_action(
            "ASK_BEFORE_NEW_AUTHORIZATION",
            "The private link expired. Ask the user before creating a new authorization.",
            None,
        )
    return _results.next_action(
        "ASK_BEFORE_NEW_AUTHORIZATION",
        "This authorization did not complete. Explain the failure and ask the user before creating a new authorization.",
        None,
    )


def asset_next_action(asset_id: str, step: str | None, error: str | None = None) -> dict[str, Any]:
    if step == "UPLOADED_TO_ARK":
        return _results.next_action(
            "ESTIMATE_GENERATION",
            "The asset is ready. Put its asset ID into the generation request and estimate credits before asking for paid-generation confirmation.",
            None,
        )
    if step == "FAILED":
        detail = f" Public error: {error}" if error else ""
        return _results.next_action(
            "DO_NOT_REUPLOAD_AUTOMATICALLY",
            "Asset processing failed." + detail + " Explain it and ask the user before choosing another file.",
            f"holycrab assets get {asset_id}",
        )
    if step in {"UPLOADED", "UPLOADING_TO_ARK", "GETTING_UPLOADED_RESULT", "PROCESSING", "CREATED"}:
        return _results.next_action(
            "WAIT_FOR_ASSET",
            "Upload finished, but online processing is still running. Keep this asset ID and wait; do not upload it again.",
            f"holycrab assets wait {asset_id} --timeout 600",
        )
    return _results.next_action(
        "QUERY_ASSET",
        "The asset result is not clear. Keep the asset ID and query it; do not upload the file again.",
        f"holycrab assets get {asset_id}",
    )


def create_authorization(name: str) -> _results.AuthorizationStartResult:
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 255:
        raise ValueError("name must contain 1-255 characters")
    cleanup_authorization_qrs()
    try:
        status, response = _transport.send("POST", "/api/real-human-authorizations/sessions",
                                payload={"name": name.strip(), "callbackUrl": _settings.REAL_HUMAN_CALLBACK_URL})
    except KeyboardInterrupt:
        raise _results.interrupted_mutation("Check existing authorizations on the HolyCrab website before deciding whether another authorization is needed.") from None
    except (OSError, http.client.HTTPException) as error:
        raise ValueError("Authorization outcome is uncertain; do not retry automatically. Check the website.") from error
    data = mutation_response_data(status, response, "Authorization")
    if not isinstance(data, dict):
        raise ValueError("Authorization response is incomplete; do not retry automatically. Check the website.")
    try:
        identifier = _presentation.public_id(data.get("authorizationId"), "authorizationId")
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
    _settings.SENSITIVE_OUTPUT_VALUES.add(link)
    result = _results.AuthorizationStartResult(
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
        raise _results.CommandInterrupted(result) from None
    except (OSError, ValueError, ImportError, SystemExit):
        result["warning"] = "QR image could not be saved. Use the authorization link; do not create another session."
    return result


def get_authorization(authorization_id: str) -> dict[str, Any]:
    identifier = _presentation.public_id(authorization_id, "authorizationId")
    warning = cleanup_authorization_qrs()
    data = _transport.response_data(*_transport.send("GET", f"/api/real-human-authorizations/{identifier}"))
    output = _presentation.public_fields(data, ("authorizationId", "status", "completedAt"))
    if warning:
        output["warning"] = warning
    if output.get("authorizationId") != identifier or output.get("status") not in _settings.AUTHORIZATION_TERMINAL | {"CREATED"}:
        raise ValueError("API returned an invalid authorization status")
    if isinstance(data.get("group"), dict):
        output["group"] = _presentation.public_fields(data["group"], ("uniqId", "name"))
    if output["status"] == "SUCCEEDED" and not output.get("group", {}).get("uniqId"):
        raise ValueError("Successful authorization is missing its group; query again, do not create another session")
    output["nextAction"] = authorization_next_action(
        str(output["status"]), identifier, output.get("group", {}).get("uniqId")
    )
    if output["status"] in _settings.AUTHORIZATION_TERMINAL:
        try:
            remove_authorization_qr(identifier)
        except (OSError, ValueError):
            output["warning"] = "Could not remove the local QR image; delete it manually."
    return output


def list_real_human_groups(page: int = 1, page_size: int = 20) -> dict[str, Any]:
    query = page_query(page, page_size)
    cleanup_authorization_qrs()
    output = _presentation.public_page(_transport.response_data(*_transport.send("GET", "/api/real-human-groups", query=query)), _presentation.public_group)
    if output.get("total") == 0:
        output["nextAction"] = _results.next_action("ASK_BEFORE_AUTHORIZATION", "No authorized people were found. Ask the user whether to start a new authorization for the intended person.", None)
    return output


def list_real_human_assets(group_id: str, page: int = 1, page_size: int = 50) -> dict[str, Any]:
    identifier = _presentation.public_id(group_id, "groupUniqId")
    query = page_query(page, page_size)
    cleanup_authorization_qrs()
    output = _presentation.public_page(_transport.response_data(*_transport.send("GET", f"/api/real-human-groups/{identifier}/assets", query=query)), _presentation.public_asset)
    if output.get("total") == 0:
        output["nextAction"] = _results.next_action("SELECT_FILES_TO_UPLOAD", "This person group has no uploaded assets. Select this person's local files, preview the complete upload to this group, and confirm it separately.", None)
    return output


def find_real_human_group(group_id: str) -> dict[str, Any]:
    identifier = _presentation.public_id(group_id, "groupUniqId")
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
    identifier = _presentation.public_id(group_id, "groupUniqId")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 255:
        raise ValueError("name must contain 1-255 characters")
    try:
        status, response = _transport.send("PATCH", f"/api/real-human-groups/{identifier}",
                                payload={"name": name.strip()})
    except KeyboardInterrupt:
        raise _results.interrupted_mutation("Query the person groups to check the current name.", command="holycrab real-human groups list", groupUniqId=identifier) from None
    except (OSError, http.client.HTTPException) as error:
        detail = reconcile_group(identifier)
        raise ValueError(f"Rename outcome is uncertain. {detail} Do not retry automatically") from error
    try:
        data = mutation_response_data(status, response, "Rename")
    except ValueError as error:
        detail = reconcile_group(identifier)
        raise ValueError(f"Rename outcome is uncertain. {detail} Do not retry automatically") from error
    group = _presentation.public_fields(data, ("uniqId", "name"))
    if group.get("uniqId") != identifier or group.get("name") != name.strip():
        raise ValueError("Rename outcome is uncertain; query the group, do not retry automatically")
    return group


def delete_real_human_group(group_id: str, confirmed: bool,
                            target: dict[str, Any] | None = None) -> dict[str, Any]:
    if confirmed is not True:
        raise ValueError("confirmed must be true after the user explicitly approves permanent deletion")
    identifier = _presentation.public_id(group_id, "groupUniqId")
    target = target or find_real_human_group(identifier)
    if target.get("uniqId") != identifier:
        raise ValueError("Real-human group not found")
    try:
        status, response = _transport.send("DELETE", f"/api/real-human-groups/{identifier}")
    except KeyboardInterrupt:
        raise _results.interrupted_mutation("Query the person groups to check whether this group remains.", command="holycrab real-human groups list", groupUniqId=identifier) from None
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
    group_identifier = _presentation.public_id(group_id, "groupUniqId")
    asset_identifier = _presentation.public_id(asset_id, "assetId")
    group = group or find_real_human_group(group_identifier)
    asset = asset or get_asset(asset_identifier)
    if group.get("uniqId") != group_identifier or asset.get("uniqId") != asset_identifier:
        raise ValueError("Real-human asset not found")
    endpoint = f"/api/real-human-groups/{group_identifier}/assets/{asset_identifier}"
    try:
        status, response = _transport.send("DELETE", endpoint)
    except KeyboardInterrupt:
        raise _results.interrupted_mutation("Query this person's assets to check whether the asset remains.", command=f"holycrab real-human assets list --group {group_identifier}", groupUniqId=group_identifier, assetUniqId=asset_identifier) from None
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
    identifier = _presentation.public_id(asset_id, "assetId")
    data = _presentation.public_asset(_transport.response_data(*_transport.send("GET", f"/api/user-assets/{identifier}")))
    if data.get("uniqId") != identifier:
        raise ValueError("API returned an unexpected asset ID")
    data["nextAction"] = asset_next_action(identifier, data.get("step"), data.get("error"))
    return data


def poll_resource(identifier: str, timeout: float, interval: float, *, authorization: bool) -> int:
    _tasks.validate_wait_limits(timeout, interval)
    deadline = time.monotonic() + timeout
    previous_state: Any = object()
    data: dict[str, Any] = {}
    while True:
        data = get_authorization(identifier) if authorization else get_asset(identifier)
        state = data.get("status") if authorization else data.get("step")
        if state != previous_state:
            _presentation.print_result(data)
            previous_state = state
        if state == ("SUCCEEDED" if authorization else "UPLOADED_TO_ARK"):
            if not sys.stdout.isatty():
                _presentation.command_progress("Authorization completed; select files to upload next." if authorization else "Asset is ready.")
            return 0
        if state in ({"FAILED", "EXPIRED"} if authorization else {"FAILED", "DELETING"}):
            if not sys.stdout.isatty():
                _presentation.command_progress("Waiting ended without success. Review the returned status and nextAction.")
            return 1
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timeout_action = _results.next_action(
                "CONTINUE_QUERYING",
                "The wait timed out; this does not mean failure. Keep the same ID and query again. Nothing was resubmitted.",
                (f"holycrab real-human get {identifier}" if authorization else f"holycrab assets get {identifier}"),
            )
            _presentation.print_result({"timedOut": True, "id": identifier, "lastStatus": state, "nextAction": timeout_action})
            if not sys.stdout.isatty():
                print("Polling timed out; keep the ID and query again. Nothing was resubmitted.", file=sys.stderr)
            return 2
        time.sleep(min(interval, remaining))
