"""HolyCrab results responsibilities."""
from __future__ import annotations
import re
import json
import os
from typing import Any


class CommandInterrupted(Exception):
    """A stopped operation whose known outcome must still be reported safely."""

    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__("Stopped by user; review the operation result before taking another action.")
        self.result = result


class ApiError(SystemExit):
    """A definite API error with a projected, safe machine-readable response."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = OperationResult(result, "failed")
        super().__init__(result["message"])


class CommandUsageError(ValueError):
    """A locally rejected argument, before any operation was attempted."""


class OperationResult(dict):
    """Business outcome, separate from its backwards-compatible public payload."""
    def __init__(self, payload: dict[str, Any], outcome: str = "success") -> None:
        super().__init__(payload)
        self.outcome = outcome
        self.retryable = False


class ApprovedEstimate(dict):
    def __init__(self, payload: dict[str, Any], request_hash: str) -> None:
        super().__init__(payload)
        self.request_hash = request_hash


def next_action(code: str, instruction: str, command: str | None) -> dict[str, Any]:
    return {"code": code, "instruction": instruction, "command": command}


def interrupted_mutation(instruction: str, *, command: str | None = None, **identifiers: Any) -> CommandInterrupted:
    return CommandInterrupted({"state": "unknown", "interrupted": True, **identifiers,
                               "message": "The operation was interrupted after submission began; its online outcome is uncertain.",
                               "nextAction": next_action("QUERY_EXISTING_RECORDS", instruction + " Do not retry automatically.", command)})


def attempt_next_action(record: dict[str, Any]) -> dict[str, Any]:
    task_id = record.get("taskId")
    if isinstance(task_id, str) and re.fullmatch(r"[A-Za-z0-9]{1,64}", task_id):
        return next_action("QUERY_TASK", "A task ID was recorded. Query that same task. Do not submit this attempt again.", f"holycrab tasks get {task_id}")
    if record.get("state") == "failed":
        return next_action("REVIEW_REJECTED_REQUEST", "The service rejected this request. Correct it, estimate again, and obtain new confirmation before using a new attempt.", None)
    return next_action("QUERY_RECENT_TASKS", "Do not submit this attempt again. Query recent tasks; finding no task cannot prove that the online task was not created.",
                       "holycrab tasks list --page 1 --page-size 20")



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



def clear_environment_command(name: str) -> str:
    if not re.fullmatch(r"HOLYCRAB_[A-Z_]+", name):
        raise ValueError("Unsupported environment variable")
    return f"Remove-Item Env:{name} -ErrorAction SilentlyContinue" if os.name == "nt" else f"unset {name}"



def response_ok(status: int, response: Any) -> bool:
    return 200 <= status < 300 and (
        not isinstance(response, dict) or response.get("code", 0) in (0, 200)
    )



class AuthorizationStartResult(dict):
    """Explicit disclosure of a user-requested verification link, never a raw response."""

    qr_bytes: bytes | None = None
