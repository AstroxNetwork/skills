"""HolyCrab presentation responsibilities."""
from __future__ import annotations
from . import results as _results

import html
import json
import os
import re
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from . import settings as _settings

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


def normalized_name(value: Any) -> str:
    return "".join(character for character in str(value).lower() if character.isalnum())


def is_signed_url(value: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(html.unescape(value))
    except ValueError:
        # Untrusted server text can contain malformed URLs. Redact rather than
        # losing the original operation result while trying to format it.
        return True
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or not parsed.query:
        return False
    return any(
        normalized_name(key) in _settings.SENSITIVE_URL_QUERY_KEYS
        for key, _ in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    )


def sanitize_text_for_output(value: str) -> str:
    def replace_url(match: re.Match[str]) -> str:
        candidate = match.group(0)
        return "<redacted-signed-url>" if is_signed_url(candidate) else candidate

    sanitized = _settings.URL_IN_TEXT_PATTERN.sub(replace_url, value)
    sanitized = _settings.PRIVATE_FIELD_IN_TEXT.sub(r"\1<redacted>", sanitized)
    for secret in sorted(_settings.SENSITIVE_OUTPUT_VALUES, key=len, reverse=True):
        sanitized = sanitized.replace(secret, "<redacted-secret>")
    return sanitized


def sanitize_for_output(value: Any) -> Any:
    if isinstance(value, dict):
        output: dict[Any, Any] = {}
        for key, item in value.items():
            normalized = normalized_name(key)
            # Only the locally constructed start result may disclose its one-time
            # verification link. An arbitrary API response cannot opt into this.
            if isinstance(value, _results.AuthorizationStartResult) and key == "h5Link":
                output[key] = item
            else:
                output[key] = "<redacted>" if normalized in _settings.SENSITIVE_OUTPUT_KEYS else sanitize_for_output(item)
        return output
    if isinstance(value, list):
        return [sanitize_for_output(item) for item in value]
    if isinstance(value, str):
        return sanitize_text_for_output(value)
    return value


def public_api_error(status: int, response: Any) -> dict[str, Any]:
    """Project the public error contract; never expose arbitrary response data."""
    remember_private_fields(response)
    response = response if isinstance(response, dict) else {}
    raw_message = response.get("message")
    message = f"HolyCrab API request failed with HTTP {status}"
    if isinstance(raw_message, str) and raw_message:
        message += ": " + raw_message
    output: dict[str, Any] = {"state": "failed", "httpStatus": status, "message": message}
    output.update(public_fields(response, ("code", "errorCode", "requestId")))
    issues = []
    for item in response.get("errors", []) if isinstance(response.get("errors"), list) else []:
        if not isinstance(item, dict):
            continue
        issue = public_fields(item, ("code", "scope", "assetId", "role", "requestPath", "field", "message", "retryable", "actual"))
        if isinstance(item.get("assetIds"), list):
            issue["assetIds"] = [value for value in item["assetIds"] if isinstance(value, str)]
        rule = item.get("rule")
        if isinstance(rule, dict):
            issue["rule"] = public_fields(rule, ("min", "max", "minInclusive", "maxInclusive", "unit", "constraint", "required"))
            if isinstance(rule.get("allowedValues"), list):
                issue["rule"]["allowedValues"] = [value for value in rule["allowedValues"] if value is None or type(value) in (str, int, float, bool)]
        issues.append(issue)
    if "errors" in response and isinstance(response["errors"], list):
        output["errors"] = issues
    return sanitize_for_output(output)


def terminal_api_errors(value: dict[str, Any]) -> list[str]:
    lines = []
    for issue in value.get("errors", []):
        if not isinstance(issue, dict):
            continue
        identity = issue.get("assetId") or ", ".join(issue.get("assetIds", [])) or issue.get("scope", "request")
        location = issue.get("requestPath", "request")
        lines += ["", terminal_scalar(identity) + " | " + terminal_scalar(location) + " | " + terminal_scalar(issue.get("field"))]
        if issue.get("message"):
            lines += terminal_paragraph(issue["message"]).splitlines()
        if "actual" in issue:
            lines += ["Actual: " + ("null (unknown)" if issue["actual"] is None else terminal_scalar(issue["actual"]))]
        if "rule" in issue:
            lines += ["Required: " + json.dumps(issue["rule"], ensure_ascii=False)]
        if "retryable" in issue:
            lines += ["Retryable: " + terminal_scalar(issue["retryable"]) + " (no automatic retry)"]
    return lines


def public_envelope(response: Any, projector: Any) -> Any:
    if not isinstance(response, dict):
        return response
    output = {
        key: response[key]
        for key in ("code", "message", "errorCode", "requestId")
        if key in response
    }
    if isinstance(response.get("errors"), list):
        output["errors"] = public_api_error(400, response)["errors"]
    if "data" in response:
        output["data"] = projector(response.get("data"))
    return output


def public_account_data(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    output = {key: value[key] for key in _settings.PUBLIC_ACCOUNT_FIELDS if key in value and key != "disabled_models"}
    disabled = value.get("disabled_models")
    if isinstance(disabled, list) and all(isinstance(item, str) and item.strip() for item in disabled):
        output["disabled_models"] = list(dict.fromkeys(item.strip() for item in disabled))
    return output


def onboarding_guidance(*, configured: bool, verified: bool = False, environment_override: bool = False,
                        local_only: bool = False) -> dict[str, Any]:
    if not configured:
        state, instruction, command = "CONNECT_ACCOUNT", "Connect your HolyCrab account locally. Never send your API Key in chat.", "holycrab setup"
    elif local_only:
        state, instruction, command = "CONFIGURED", "An API Key is configured. Local checks do not verify account validity.", None
    elif environment_override:
        state, instruction, command = (
            "VERIFY_ACCOUNT", "HOLYCRAB_API_KEY overrides the saved login. Clear the override, then check the active account.",
            _results.clear_environment_command("HOLYCRAB_API_KEY") + ("; " if os.name == "nt" else " && ") + "holycrab auth status",
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
        "examples": [dict(example) for example in _settings.ONBOARDING_EXAMPLES],
        "question": "What would you like to work on first?",
        "agentInstruction": _settings.ONBOARDING_AGENT_INSTRUCTION,
    }


def format_onboarding(guidance: dict[str, Any], *, installed: bool = False,
                      introduce: bool = False, upgrading: bool = False) -> str:
    """One user-facing summary shared by both installers and account commands."""
    lines = [f"HolyCrab {_settings.VERSION} installed", ""] if installed else []
    state = guidance.get("state")
    if state == "CONFIGURED" and not installed:
        return "API Key: configured"
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
        command = "holycrab auth status" if state == "CONFIGURED" and installed else guidance.get("command")
        if command:
            # Commands stay on one line, including the PowerShell override fix.
            lines += ["  " + str(command)]
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
    output = {key: value[key] for key in _settings.PUBLIC_TASK_FIELDS if key in value}
    output["audioUrls"] = _results.parse_audio_urls(value.get("audioIds"))
    identifier = value.get("uniqId") or value.get("taskId")
    if isinstance(identifier, str) and re.fullmatch(r"[A-Za-z0-9]{1,64}", identifier):
        output["nextAction"] = _results.task_next_action(identifier, value.get("step"), output)
    return output


def public_task_response(response: Any) -> Any:
    return public_envelope(response, public_task_data)


def print_json(value: Any) -> None:
    print(json.dumps(sanitize_for_output(value), ensure_ascii=False, indent=2))


def terminal_next_action(action: Any) -> list[str]:
    if not isinstance(action, dict):
        return []
    instruction = _settings.TERMINAL_ACTION_COPY.get(action.get("code"), action.get("instruction"))
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
    if view == "materials":
        for item in records:
            metadata = {key: item[key] for key in ("durationSeconds", "mediaFormat", "width", "height", "sizeBytes", "frameRate", "videoCodec", "audioCodec", "metadataStatus", "metadataErrorCode", "metadataUpdatedAt") if key in item}
            if metadata:
                lines += ["", "Material " + terminal_scalar(item.get("uniqId")), *terminal_fields(metadata)]
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
    if value.get("uploadPlanId"):
        lines += ["  Upload plan ID: " + str(value["uploadPlanId"])]
    sections = [("Registered (wait for processing)", value["uploaded"]),
                ("Failed or unknown", [failed] if failed else []), ("Not attempted", value["notAttempted"])]
    for title, items in sections:
        if not items:
            continue
        lines += ["", title + ":"]
        for item in items:
            lines += terminal_fields({key: item[key] for key in
                                     ("file", "assetUniqId", "groupUniqId", "state", "phase", "error", "message", "httpStatus", "errorCode", "requestId", "warning")
                                     if key in item}, indent="  ")
            lines += terminal_api_errors(item)
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
        if not _results.response_ok(value["httpStatus"], response):
            return f"Request failed (HTTP {value['httpStatus']})\n" + "\n".join(terminal_fields(response))
        value = response.get("data", response) if isinstance(response, dict) else response
        return format_terminal_result(value, view=view)
    if "checks" in value and "ok" in value:
        lines = ["Local checks passed" if value["ok"] else "Local checks need attention",
                 f"Version: {value.get('version', _settings.VERSION)}"]
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
            details = public_account_data(account)
            if details:
                lines += [""] + terminal_fields(details)
            if "disabled_models" not in details:
                lines += ["Model permissions: unknown; check a generation estimate before submitting."]
        if value.get("nextAction") and (account_failed or
                value["nextAction"].get("command") != value["onboarding"].get("command")):
            lines += terminal_next_action(value["nextAction"])
        return "\n".join(lines)
    if isinstance(value.get("files"), list) and "uploadPlanId" in value:
        target = value.get("target", {})
        person = target.get("name") or "Personal materials"
        lines = ["Upload preview", "", "Upload plan ID: " + terminal_scalar(value["uploadPlanId"]), "Target: " + terminal_scalar(person)]
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
        lines = [title, "Current version: " + _settings.VERSION]
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
    if value.get("state") == "failed" and "httpStatus" in value and "attemptId" not in value:
        lines = ["API request rejected", *terminal_fields({key: value[key] for key in ("httpStatus", "errorCode", "requestId") if key in value})]
        lines += terminal_paragraph(value.get("message", "Request failed")).splitlines()
        return "\n".join(lines + terminal_api_errors(value))
    if "attemptId" in value and "state" in value:
        titles = {"created": "Task created", "failed": "Submission rejected", "unknown": "Submission outcome unknown",
                  "submitting": "Submission not yet confirmed", "prepared": "Submission not yet confirmed"}
        lines = [titles.get(value["state"], "Submission status")]
        lines += terminal_fields({key: value[key] for key in
                                 ("attemptId", "taskId", "kind", "httpStatus", "note", "warning") if key in value})
        if value["state"] == "failed" and value.get("message"):
            lines += terminal_paragraph("Error: " + value["message"]).splitlines()
        return "\n".join(lines + terminal_api_errors(value) + terminal_next_action(value.get("nextAction")))
    if "kind" in value and "estimate" in value:
        return "\n".join(["Credit estimate (no task created)", *terminal_fields({"kind": value["kind"]}),
                          *terminal_fields(value["estimate"]), *terminal_next_action(value.get("nextAction"))])
    if "uniqId" in value and "step" in value:
        lines = ["Material status" if "ready" in value else "Task status"]
        details = {key: value[key] for key in
                   ("uniqId", "name", "taskType", "assetType", "model", "progress", "duration", "resolution",
                    "ratio", "generateAudio", "frozenCredit", "textResult", "error", "durationSeconds",
                    "mediaFormat", "width", "height", "sizeBytes", "frameRate", "videoCodec", "audioCodec",
                    "metadataStatus", "metadataErrorCode", "metadataUpdatedAt") if key in value}
        details["step"] = terminal_status(value["step"])
        lines += terminal_fields(details)
        if "ready" not in value:
            outputs = _results.task_output_urls(value)
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
    if not _results.response_ok(status, response):
        print_result(public_api_error(status, response))
        return 1
    print_result({"httpStatus": status, "response": response}, view=view)
    return 0


def remember_private_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if normalized_name(key) in _settings.SENSITIVE_OUTPUT_KEYS and isinstance(item, str) and item:
                _settings.SENSITIVE_OUTPUT_VALUES.add(item)
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
                                  "duration", "durationSeconds", "mediaFormat", "width", "height", "sizeBytes",
                                  "frameRate", "videoCodec", "audioCodec", "metadataStatus",
                                  "metadataErrorCode", "metadataUpdatedAt", "url", "createTime", "updateTime"))
    output["ready"] = output.get("step") == "UPLOADED_TO_ARK"
    return output


def public_page(value: Any, projector: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("records"), list):
        raise ValueError("API response is missing its records array")
    output = public_fields(value, ("total", "current", "size", "pages"))
    output["records"] = [projector(item) for item in value["records"]]
    return output


def read_confirmation(prompt: str) -> str:
    print(prompt, end="", file=sys.stderr, flush=True)
    return input().strip().lower()



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
