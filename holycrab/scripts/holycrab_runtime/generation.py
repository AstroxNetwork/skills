"""HolyCrab generation responsibilities."""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Callable
from . import capabilities as _capabilities
from . import presentation as _presentation
from . import results as _results
from . import settings as _settings
from . import state as _state
from . import transport as _transport

def generation_endpoints(kind: str, payload: dict[str, Any]) -> tuple[str, str]:
    if kind not in {"video", "image", "audio"}:
        raise SystemExit("Generation kind must be video, image, or audio")
    if kind == "audio":
        return _settings.GENERATION_ROUTES["audioGeneration"]
    model_id = payload.get("model")
    if not isinstance(model_id, str):
        raise SystemExit("Generation request must include model")
    model = _capabilities.find_model(model_id)
    if model["kind"] != kind:
        raise SystemExit(f"Model {model_id} is a {model['kind']} model, not {kind}")
    route = _settings.MODEL_GENERATION_ROUTES.get(model_id)
    if route is None:
        raise SystemExit(f"No generation route for public model: {model_id}")
    return _settings.GENERATION_ROUTES[route]


def canonical_request_hash(kind: str, payload: dict[str, Any]) -> str:
    raw = json.dumps(
        {"kind": kind, "request": payload}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def extract_task_id(response: Any) -> str | None:
    data = response.get("data") if isinstance(response, dict) else None
    if isinstance(data, dict):
        value = data.get("uniqId") or data.get("taskId")
        return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9]{1,64}", value) else None
    return None


def persist_outcome(attempt_id: str, **changes: Any) -> bool:
    """An already submitted outcome must remain reportable if local storage fails."""
    try:
        _state.update_attempt(attempt_id, **changes)
        return True
    except (OSError, SystemExit, KeyboardInterrupt):
        return False


def generation_unknown(attempt_id: str, *, http_status: int | None = None, note: str) -> dict[str, Any]:
    changes: dict[str, Any] = {"state": "unknown", "note": note}
    if http_status is not None:
        changes["httpStatus"] = http_status
    saved = persist_outcome(attempt_id, **changes)
    return {
        **({} if saved else {"localStateSaved": False, "warning": "Keep this attempt ID. Its outcome could not be saved; do not resubmit."}),
        "attemptId": attempt_id,
        "state": "unknown",
        "message": "The result is uncertain. Do not submit this attempt again.",
        "nextAction": {
            "code": "QUERY_RECENT_TASKS",
            "instruction": "Query recent tasks. Finding no result cannot prove that the online task was not created.",
            "command": "holycrab tasks list --page 1 --page-size 20",
        },
    }


def validate_estimate_data(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or type(data.get("frozenCredit")) is not int or data["frozenCredit"] < 0:
        raise ValueError("The service did not return a valid credit estimate; no task was submitted")
    return data


def estimate_generation(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _capabilities.normalize_generation_request(kind, payload)
    freeze_endpoint, _ = generation_endpoints(kind, normalized)
    status, response = _transport.send("POST", freeze_endpoint, payload=normalized)
    if not _results.response_ok(status, response):
        _transport.response_data(status, response)
    if not isinstance(response, dict) or type(response.get("code")) is not int or response["code"] not in (0, 200):
        raise ValueError("Invalid estimate response; no task was submitted")
    data = validate_estimate_data(response.get("data"))
    return _results.ApprovedEstimate({"kind": kind, "estimate": data,
            "nextAction": _results.next_action("REVIEW_ESTIMATE", "Review this estimate and confirm the exact request before creating one billable task.", None)},
            canonical_request_hash(kind, normalized))


@_state.protect_operation
def create_generation(
    kind: str,
    payload: dict[str, Any],
    *,
    confirmed: bool,
    attempt_id: str | None = None,
    approved_estimate: dict[str, Any] | None = None,
    approved_credits: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    selected_attempt = attempt_id or uuid.uuid4().hex
    context: dict[str, Any] = {}
    try:
        result = _create_generation(kind, payload, confirmed=confirmed, attempt_id=selected_attempt,
                                    approved_estimate=approved_estimate, approved_credits=approved_credits, progress=progress, context=context)
        if isinstance(result, _results.ApprovedEstimate):
            return result
        return _results.OperationResult(result, result.get("state") if result.get("state") in {"failed", "unknown"} else "success")
    except KeyboardInterrupt:
        if not context.get("reserved"):
            raise
        result = generation_unknown(selected_attempt, note="Interrupted while submitting or recording the result; do not resubmit.")
        if context.get("taskId"):
            if not persist_outcome(selected_attempt, taskId=context["taskId"]):
                result["localStateSaved"] = False
            result["taskId"] = context["taskId"]
            result["nextAction"] = _results.next_action("QUERY_TASK", "A valid task ID was received before interruption. Query this same task; do not resubmit.", f"holycrab tasks get {context['taskId']}")
        raise _results.CommandInterrupted(result) from None


def _create_generation(kind: str, payload: dict[str, Any], *, confirmed: bool, attempt_id: str,
                       approved_estimate: dict[str, Any] | None, approved_credits: int | None, progress: Callable[[str], None] | None,
                       context: dict[str, Any]) -> dict[str, Any]:
    payload = _capabilities.normalize_generation_request(kind, payload)
    if approved_credits is not None and (type(approved_credits) is not int or approved_credits < 0):
        raise ValueError("approvedCredits must be a nonnegative integer")
    selected_attempt = attempt_id
    if confirmed and _state.attempt_exists(selected_attempt):
        raise SystemExit(
            f"Local submission attempt {selected_attempt} already exists; query it instead of submitting again"
        )
    if not approved_estimate and progress:
        progress("Estimating credits; no task has been submitted...")
    estimate = approved_estimate or estimate_generation(kind, payload)
    validate_estimate_data(estimate.get("estimate"))
    if approved_estimate is not None and (not isinstance(approved_estimate, _results.ApprovedEstimate)
            or approved_estimate.request_hash != canonical_request_hash(kind, payload)):
        raise ValueError("The approved estimate does not match this request; estimate again")
    if confirmed:
        if approved_estimate is not None:
            approved_credits = estimate['estimate']['frozenCredit']
            estimate = estimate_generation(kind, payload)
        if approved_credits is not None and estimate['estimate']['frozenCredit'] != approved_credits:
            raise ValueError(f"Credit estimate changed from {approved_credits} to {estimate['estimate']['frozenCredit']}; no task was submitted. Review the new estimate and confirm again.")
    if not confirmed:
        return _results.ApprovedEstimate({
            **estimate,
            "confirmationRequired": True,
            "message": "Confirm once before creating this billable task.",
        }, canonical_request_hash(kind, payload))
    _, create_endpoint = generation_endpoints(kind, payload)
    _state.reserve_attempt(
        selected_attempt,
        {
            "attemptId": selected_attempt,
            "kind": kind,
            "requestHash": canonical_request_hash(kind, payload),
            "state": "prepared",
            "createdAt": _state.utc_now(),
            "endpoint": create_endpoint,
        },
    )
    context["reserved"] = True
    _state.update_attempt(selected_attempt, state="submitting")
    try:
        if progress:
            progress("Submitting exactly one billable task. If interrupted, query its attempt instead of submitting again...")
        status, response = _transport.send("POST", create_endpoint, payload=payload)
    except KeyboardInterrupt:
        raise _results.CommandInterrupted(generation_unknown(selected_attempt, note="Submission was interrupted. The online outcome is uncertain; do not resubmit.")) from None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        return generation_unknown(
            selected_attempt,
            note=f"Network outcome was ambiguous ({error.__class__.__name__}); do not resubmit.",
        )
    if not _results.response_ok(status, response):
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
        error_result = _presentation.public_api_error(status, response)
        saved = persist_outcome(selected_attempt, **error_result)
        return {
            **error_result,
            "attemptId": selected_attempt,
            **({} if saved else {"localStateSaved": False, "warning": "The rejection could not be saved. Keep the attempt ID."}),
            "nextAction": {
                "code": "FIX_REQUEST",
                "instruction": "Correct the rejected request and create a new attempt only after confirmation. Do not retry automatically.",
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
    saved = True
    try:
        _state.update_attempt(selected_attempt, state="created", taskId=task_id, httpStatus=status)
    except (OSError, SystemExit):
        saved = False
    result = {
        "attemptId": selected_attempt,
        "taskId": task_id,
        "state": "created",
        "estimate": estimate["estimate"],
        "response": _presentation.public_task_data(_transport.response_data(status, response)),
        "nextAction": _results.next_action("WAIT_FOR_TASK", "The task was created. Wait for this same task; do not submit it again.",
                                  f"holycrab tasks wait {task_id} --timeout 600"),
    }
    if not saved:
        result.update(localStateSaved=False, warning="The task was created but its local record could not be saved. Keep the task ID and query it; do not resubmit.")
    return result
