"""HolyCrab uploads responsibilities."""
from __future__ import annotations

import hashlib
import http.client
import os
import re
import stat
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Callable
from . import presentation as _presentation
from . import real_human as _real_human
from . import results as _results
from . import settings as _settings
from . import state as _state
from . import transport as _transport

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
    declared = _settings.UPLOAD_FORMATS.get(extension)
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
    return {
        "path": str(path), "name": path.name, "extension": extension, "mediaType": media_type,
        "contentType": mime, "size": info.st_size, "durationSeconds": resolved_duration,
        "mtimeNs": info.st_mtime_ns, "device": info.st_dev, "inode": info.st_ino,
        "sha256": sha256_stream(path),
    }


def upload_plan_path(plan_id: str) -> Path:
    return _state.upload_plans_dir() / f"{_presentation.public_id(plan_id, 'uploadPlanId')}.json"


def cleanup_upload_plans() -> None:
    directory = _state.upload_plans_dir()
    try:
        if not directory.is_dir() or directory.is_symlink():
            return
        now = time.time()
        for path in directory.iterdir():
            if path.is_symlink() or not path.is_file() or not re.fullmatch(r"[A-Za-z0-9]{1,64}\.json", path.name):
                continue
            plan = _state.read_json_file(path, None)
            if not isinstance(plan, dict) or plan.get("state") != "prepared":
                continue  # Unknown/corrupt records cannot prove that no upload occurred.
            if now > float(plan.get("expiresAtEpoch", 0)):
                try:
                    with _state.file_lock(path.with_suffix(".lock"), blocking=False):
                        latest = _state.read_json_file(path, None)
                        if isinstance(latest, dict) and latest.get("state") == "prepared":
                            path.unlink(missing_ok=True)
                except (OSError, SystemExit):
                    continue
    except (SystemExit, OSError, TypeError, ValueError):
        return


def prepare_upload_plan(files: list[str], *, group_uniq_id: str | None = None,
                        duration_seconds: int | None = None) -> dict[str, Any]:
    if not isinstance(files, list) or not files or len(files) > _settings.MAX_UPLOAD_FILES:
        raise SystemExit(f"Choose between 1 and {_settings.MAX_UPLOAD_FILES} files")
    if not all(isinstance(value, str) and value for value in files):
        raise SystemExit("Upload file paths must be nonempty strings")
    group = _real_human.find_real_human_group(_presentation.public_id(group_uniq_id, "groupUniqId")) if group_uniq_id else None
    inspected = [inspect_upload_file(value, real_human=group is not None,
                                     duration_seconds=duration_seconds) for value in files]
    plan_id = uuid.uuid4().hex
    stored = {
        "uploadPlanId": plan_id, "state": "prepared", "createdAt": _state.utc_now(),
        "expiresAtEpoch": time.time() + _settings.UPLOAD_PLAN_SECONDS,
        "group": _presentation.public_fields(group, ("uniqId", "name")) if group else None, "files": inspected,
    }
    _state.write_private_json(upload_plan_path(plan_id), stored)
    return {
        "uploadPlanId": plan_id, "expiresInSeconds": _settings.UPLOAD_PLAN_SECONDS,
        "target": stored["group"] or {"type": "ordinary-assets"},
        "files": [{key: item[key] for key in ("path", "name", "mediaType", "contentType", "size", "durationSeconds")} for item in inspected],
        "onlineChecks": "Dimensions, aspect ratio, frame rate, duration, and codecs are still checked online; local preview does not guarantee acceptance.",
        "confirmationRequired": True,
        "nextAction": _results.next_action("CONFIRM_UPLOAD_PLAN", "Confirm once only if every file and the target person are correct.", None),
    }


def upload_prepared_file(item: dict[str, Any], group_id: str | None,
                         progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    query = [("file_extension", item["extension"]), ("content_type", item["contentType"])]
    if item.get("durationSeconds") is not None:
        query.append(("duration_seconds", str(item["durationSeconds"])))
    try:
        if progress:
            progress("Requesting an upload location...")
        status, response = _transport.send("GET", "/api/user-assets/pre-signed-download-url", query=query)
    except KeyboardInterrupt:
        return {"state": "unknown", "phase": "presign", "interrupted": True}
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        return {"state": "unknown", "phase": "presign", "message": str(error)}
    if not _results.response_ok(status, response):
        code = response.get("code") if isinstance(response, dict) else None
        explicit = (400 <= status < 500 and status != 408) or (
            200 <= status < 300 and type(code) is int and 400 <= code < 500 and code != 408
        )
        return {**_presentation.public_api_error(status, response), "state": "failed" if explicit else "unknown", "phase": "presign"}
    try:
        data = _transport.response_data(status, response)
    except SystemExit as error:
        return {"state": "unknown", "phase": "presign", "message": str(error)}
    if not isinstance(data, dict):
        return {"state": "unknown", "phase": "presign",
                "message": "Presign response is missing data"}
    try:
        asset_id = _presentation.public_id(data.get("uniqId"), "assetId")
    except (SystemExit, ValueError) as error:
        return {"state": "unknown", "phase": "presign", "message": str(error)}
    try:
        presigned_url = _transport.validate_presigned_upload_url(data.get("preSignedUrl"))
    except (SystemExit, ValueError) as error:
        return {"assetUniqId": asset_id, "state": "unknown", "phase": "presign", "message": str(error)}
    object_key = data.get("objectKey")
    if not isinstance(object_key, str):
        return {"assetUniqId": asset_id, "state": "unknown", "phase": "presign",
                "message": "Presign response omitted objectKey"}
    request = urllib.request.Request(
        presigned_url, data=_transport.FileChunks(item["_stream"]),
        headers={"Content-Type": item["contentType"], "Content-Length": str(item["size"])}, method="PUT",
    )
    try:
        if progress:
            progress(f"Uploading {item['size']} bytes...")
        with _transport.open_presigned_upload(request) as upload_response:
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
        register_status, register_response = _transport.send("POST", endpoint, form=form)
    except KeyboardInterrupt:
        return {"assetUniqId": asset_id, "state": "unknown", "phase": "registration", "interrupted": True}
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        return {"assetUniqId": asset_id, "state": "unknown", "message": str(error)}
    code = register_response.get("code") if isinstance(register_response, dict) else None
    if not _results.response_ok(register_status, register_response):
        explicit = (400 <= register_status < 500 and register_status != 408) or (
            200 <= register_status < 300 and type(code) is int and 400 <= code < 500 and code != 408
        )
        return {**_presentation.public_api_error(register_status, register_response), "assetUniqId": asset_id, "state": "failed" if explicit else "unknown"}
    if not isinstance(register_response, dict) or code is None:
        return {"assetUniqId": asset_id, "state": "unknown", "httpStatus": register_status}
    return {
        "assetUniqId": asset_id, "groupUniqId": group_id, "state": "uploaded", "ready": False,
        "nextAction": _real_human.asset_next_action(asset_id, "UPLOADED"),
    }


def snapshot_upload_files(plan: dict[str, Any], stack: ExitStack) -> list[Any]:
    streams = []
    for item in plan["files"]:
        current = inspect_upload_file(item["path"], real_human=bool(plan.get("group")),
                                      duration_seconds=item.get("durationSeconds"))
        for key in ("path", "size", "mtimeNs", "device", "inode", "sha256", "contentType"):
            if current.get(key) != item.get(key):
                raise SystemExit(f"File changed after confirmation: {item['path']}")
        snapshot = stack.enter_context(tempfile.TemporaryFile(mode="w+b"))
        digest = hashlib.sha256()
        size = 0
        with Path(item["path"]).open("rb") as source:
            before = os.fstat(source.fileno())
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > item["size"]:
                    raise SystemExit(f"File changed after confirmation: {item['path']}")
                digest.update(chunk)
                snapshot.write(chunk)
            after = os.fstat(source.fileno())
        expected = (item["size"], item["mtimeNs"], item["device"], item["inode"])
        for observed in (before, after):
            if (observed.st_size, observed.st_mtime_ns, observed.st_dev, observed.st_ino) != expected:
                raise SystemExit(f"File changed after confirmation: {item['path']}")
        if size != item["size"] or digest.hexdigest() != item["sha256"]:
            raise SystemExit(f"File changed after confirmation: {item['path']}")
        snapshot.flush()
        snapshot.seek(0)
        streams.append(snapshot)
    return streams


def upload_plan_record(plan_id: str) -> dict[str, Any]:
    path = upload_plan_path(plan_id)
    with _state.file_lock(path.with_suffix(".lock")):
        plan = _state.read_json_file(path, None)
    if not isinstance(plan, dict):
        raise SystemExit("Upload plan not found")
    result = {key: plan[key] for key in ("uploadPlanId", "state", "createdAt", "uploaded", "failedOrUnknown", "notAttempted", "warning") if key in plan}
    result["nextAction"] = _results.next_action("REVIEW_UPLOAD", "Review known assets. An executing or uncertain plan must never be repeated.", None)
    return result


def execute_upload_plan(plan_id: str, *, confirmed: bool,
                        progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    context: dict[str, Any] = {}
    try:
        with ExitStack() as stack:
            context["stack"] = stack
            result = _execute_upload_plan(plan_id, confirmed=confirmed, progress=progress, context=context)
            failure = result.get("failedOrUnknown")
            outcome = "unknown" if failure and failure.get("state") == "unknown" else "failed" if failure or result.get("notAttempted") else "success"
            return _results.OperationResult(result, outcome)
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
        output = {"uploadPlanId": plan_id, "uploaded": context.get("completed", []), "failedOrUnknown": failed,
                  "notAttempted": [{"file": item["path"]} for item in context.get("remaining", [])]}
        if failed.get("assetUniqId"):
            output["nextAction"] = _real_human.asset_next_action(failed["assetUniqId"], None)
        else:
            output["nextAction"] = _results.next_action("REVIEW_ASSETS", "Upload was interrupted. Keep this batch inventory and inspect existing assets before deciding what to do. Do not upload these files again automatically.",
                                               f"holycrab real-human assets list --group {group_id}" if group_id else None)
        plan.update(state="unknown", uploaded=output["uploaded"], failedOrUnknown=failed, notAttempted=output["notAttempted"])
        try:
            _state.write_private_json(upload_plan_path(plan_id), plan)
        except (OSError, SystemExit, KeyboardInterrupt):
            output["warning"] = "Could not finish recording the batch. Keep this result; the plan must not be executed again."
        raise _results.CommandInterrupted(output) from None


def _execute_upload_plan(plan_id: str, *, confirmed: bool,
                         progress: Callable[[str], None] | None, context: dict[str, Any]) -> dict[str, Any]:
    if confirmed is not True:
        raise ValueError("confirmed must be true after the user approves the complete upload preview")
    path = upload_plan_path(plan_id)
    with _state.file_lock(path.with_suffix(".lock"), blocking=False):
        plan = _state.read_json_file(path, None)
        if not isinstance(plan, dict) or plan.get("state") != "prepared":
            raise SystemExit("Upload plan is missing or has already been executed")
        if time.time() > plan.get("expiresAtEpoch", 0):
            raise SystemExit("Upload plan expired; prepare the files again")
        streams = snapshot_upload_files(plan, context["stack"])
        plan["state"] = "executing"
        _state.write_private_json(path, plan)
        context.update(started=True, plan=plan, remaining=plan["files"])
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
            prepared = {**item, "_stream": streams[index]}
            result = (upload_prepared_file(prepared, group_id, progress) if progress
                      else upload_prepared_file(prepared, group_id))
        except KeyboardInterrupt:
            result = {"state": "unknown", "interrupted": True}
        context["currentResult"] = result
        if result.get("state") != "uploaded":
            failed = {"file": item["path"], **result}
            if group_id:
                failed["groupUniqId"] = group_id
            not_attempted = [{"file": remaining["path"]} for remaining in files[index + 1:]]
            plan.update(uploaded=uploaded, failedOrUnknown=failed, notAttempted=not_attempted)
            try:
                _state.write_private_json(path, plan)
            except (OSError, SystemExit):
                context["recordingFailed"] = True
            break
        uploaded.append({"file": item["path"], **result})
        plan["uploaded"] = uploaded
        context["completed"] = list(uploaded)
        try:
            _state.write_private_json(path, plan)
        except (OSError, SystemExit):
            context["recordingFailed"] = True
            not_attempted = [{"file": remaining["path"]} for remaining in files[index + 1:]]
            break
    output: dict[str, Any] = {"uploaded": uploaded, "failedOrUnknown": failed, "notAttempted": not_attempted}
    if failed and failed.get("assetUniqId"):
        output["nextAction"] = _real_human.asset_next_action(
            failed["assetUniqId"], "FAILED" if failed.get("state") == "failed" else None
        )
    elif failed and group_id:
        output["nextAction"] = _results.next_action(
            "QUERY_GROUP_ASSETS",
            "The upload result is not clear and no asset ID was returned. Keep the group ID, inspect its assets, and do not upload these files again automatically.",
            f"holycrab real-human assets list --group {group_id}",
        )
    elif failed:
        output["nextAction"] = _results.next_action(
            "REVIEW_ASSETS",
            "The upload result is not clear and no asset ID was returned. Review the account's assets before deciding what to do; do not upload these files again automatically.",
            None,
        )
    elif uploaded:
        ids = " ".join(item["assetUniqId"] for item in uploaded)
        output["nextAction"] = _results.next_action(
            "WAIT_FOR_ASSETS", ("The registered files are listed above; remaining files were not attempted. " if not_attempted else "All uploads were registered. ") + "Wait until every registered asset is ready before generation.",
            f"holycrab assets wait {ids} --timeout 600",
        )
    context["completed"] = list(uploaded)
    output["uploadPlanId"] = plan_id
    if context.get("recordingFailed"):
        output.update(localStateSaved=False, warning="Known upload results could not be saved. Keep all asset IDs and query them; do not repeat this batch.")
    plan.update(output)
    plan["state"] = "unknown" if failed and failed.get("state") == "unknown" else "failed" if failed or not_attempted else "completed"
    try:
        _state.write_private_json(path, plan)
    except (OSError, SystemExit):
        output.update(localStateSaved=False, warning="Keep this upload result; its local record could not be saved. Do not repeat this batch.")
    if failed and failed.get("interrupted"):
        raise _results.CommandInterrupted(output)
    return output
