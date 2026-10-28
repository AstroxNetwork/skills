"""Local, revisioned production graphs. Every paid write uses generation's ledger."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from . import state, generation, capabilities, transport, results, presentation, uploads, real_human

KINDS = {"image", "video", "audio", "upload", "download", "compose"}
ACTIVE = {"running", "submitted", "processing"}
OUTPUTS = {"taskId", "output", "assetIds", "files"}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def directory() -> Path:
    return state.config_dir() / "workflows"


def identifier(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", value):
        raise ValueError("Workflow and step IDs must use 1-80 letters, digits, underscores or hyphens")
    return value


def path_for(workflow_id) -> Path:
    return directory() / (identifier(workflow_id) + ".json")


def lock(workflow_id):
    return state.file_lock(directory() / (identifier(workflow_id) + ".lock"), blocking=False)


def load(workflow_id) -> dict:
    value = state.read_json_file(path_for(workflow_id), None)
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise ValueError("Workflow not found or unreadable; preserve its records")
    return value


def save(value):
    value["updatedAt"] = state.utc_now()
    state.write_private_json(path_for(value["workflowId"]), value)


def references(value):
    if isinstance(value, dict):
        if "fromStep" in value:
            if set(value) - {"fromStep", "output", "index"} or value.get("output") not in OUTPUTS:
                raise ValueError("Invalid step reference")
            identifier(value["fromStep"])
            if "index" in value and (type(value["index"]) is not int or value["index"] < 0):
                raise ValueError("Reference index must be a nonnegative integer")
            yield value["fromStep"]
        else:
            for child in value.values():
                yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


def validate_plan(plan) -> tuple[dict, set]:
    if not isinstance(plan, dict) or set(plan) - {"title", "brief", "style", "steps", "rerunStepIds"}:
        raise ValueError("Plan must contain title, optional brief/style and steps")
    for key in ("title", "brief", "style"):
        if key in plan and (not isinstance(plan[key], str) or len(plan[key]) > 20000):
            raise ValueError(f"Invalid {key}")
    if not plan.get("title") or not isinstance(plan.get("steps"), list) or not 1 <= len(plan["steps"]) <= 100:
        raise ValueError("A workflow needs a title and 1-100 steps")
    clean = copy.deepcopy(plan)
    rerun = clean.pop("rerunStepIds", [])
    if not isinstance(rerun, list):
        raise ValueError("rerunStepIds must be an array")
    rerun = {identifier(item) for item in rerun}
    definitions = {}
    for step in clean["steps"]:
        if not isinstance(step, dict) or set(step) - {"id", "kind", "request", "dependsOn", "description"}:
            raise ValueError("Invalid workflow step")
        key = identifier(step.get("id"))
        if key in definitions or step.get("kind") not in KINDS or not isinstance(step.get("request"), dict):
            raise ValueError("Steps need unique IDs, a supported kind and an object request")
        dependencies = step.get("dependsOn", [])
        if not isinstance(dependencies, list):
            raise ValueError("dependsOn must be an array")
        step["dependsOn"] = sorted({identifier(item) for item in dependencies} | set(references(step["request"])))
        definitions[key] = step
    if not rerun.issubset(definitions):
        raise ValueError("Unknown rerun step")
    visited, visiting = set(), set()
    def visit(key):
        if key not in definitions:
            raise ValueError("Unknown dependency: " + key)
        if key in visiting:
            raise ValueError("Workflow dependencies must not contain a cycle")
        if key in visited:
            return
        visiting.add(key)
        for dependency in definitions[key]["dependsOn"]:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)
    for key in definitions:
        visit(key)
    digest(clean)  # reject non-JSON values and NaN before any state write
    return clean, rerun


def snapshot(value) -> dict:
    rows = [{**step, **value["steps"][step["id"]]} for step in value["plan"]["steps"]]
    states = {row["state"] for row in rows}
    status = ("succeeded" if states == {"succeeded"} else "needs_attention" if states & {"failed", "unknown"}
              else "waiting" if states & ACTIVE else "pending")
    code = {"succeeded": "DELIVER_RESULTS", "needs_attention": "REVIEW_WORKFLOW",
            "waiting": "QUERY_WORKFLOW", "pending": "ESTIMATE_WORKFLOW"}[status]
    instruction, command = {
        "succeeded": ("Open the saved results.", None),
        "pending": ("Estimate the next ready steps.", f"holycrab workflows estimate {value['workflowId']}"),
        "waiting": ("Check the running tasks.", f"holycrab workflows get {value['workflowId']} --refresh"),
        "needs_attention": ("Review the stopped step before continuing.", None),
    }[status]
    stopped = next((row for row in rows if row["state"] in {"failed", "unknown"}), None)
    if stopped:
        if stopped["state"] == "unknown":
            instruction = "Submission outcome is unknown. Check the existing attempt before retrying."
            command = f"holycrab workflows get {value['workflowId']} --refresh"
        elif stopped.get("result", {}).get("errorCode") == "API_TOKEN_QUOTA_EXCEEDED":
            code = "INCREASE_KEY_QUOTA"
            instruction = "API Key quota is insufficient. Increase this Key's quota, then revise the failed step to retry. No task was created."
    return {"workflowId": value["workflowId"], "revision": value["revision"], "title": value["plan"]["title"],
            "state": status, "steps": rows, "history": value.get("history", []),
            "nextAction": results.next_action(code, instruction, command)}


@state.protect_operation
def prepare(plan, *, workflow_id=None, expected_revision=None):
    clean, rerun = validate_plan(plan)
    key = identifier(workflow_id) if workflow_id else uuid.uuid4().hex
    with lock(key):
        if workflow_id:
            old = load(key)
            if type(expected_revision) is not int or old["revision"] != expected_revision:
                raise ValueError("Workflow revision changed; read it again before revising")
            old_definitions = {step["id"]: step for step in old["plan"]["steps"]}
            changed = {step["id"] for step in clean["steps"] if old_definitions.get(step["id"]) != step} | rerun
            removed = set(old_definitions) - {step["id"] for step in clean["steps"]}
            if any(old["plan"].get(k) != clean.get(k) for k in ("brief", "style")):
                changed.update(old_definitions)
            while True:
                impacted = {s["id"] for s in clean["steps"] if set(s["dependsOn"]) & changed}
                if impacted <= changed:
                    break
                changed |= impacted
            if any(old["steps"].get(k, {}).get("state") in ACTIVE for k in changed | removed):
                raise ValueError("Query running tasks before replacing or removing their steps")
            history = old.get("history", []) + [{"revision": old["revision"], "plan": old["plan"], "steps": old["steps"]}]
            steps = {}
            for step in clean["steps"]:
                prior = old["steps"].get(step["id"], {})
                steps[step["id"]] = copy.deepcopy(prior) if prior and step["id"] not in changed else {
                    "state": "pending", "outputs": {},
                    **({"priorAttempt": prior.get("attemptId"), "priorTask": prior.get("taskId"),
                        "duplicateRisk": prior.get("state") == "unknown"} if prior else {})}
            value = {**old, "plan": clean, "steps": steps, "history": history, "revision": old["revision"] + 1}
        else:
            if rerun:
                raise ValueError("rerunStepIds requires an existing workflow")
            value = {"schemaVersion": 1, "workflowId": key, "revision": 1, "createdAt": state.utc_now(),
                     "plan": clean, "steps": {s["id"]: {"state": "pending", "outputs": {}} for s in clean["steps"]}}
        value["approval"] = None
        save(value)
        return snapshot(value)


def bind_account(value):
    fingerprint = hashlib.sha256(state.credential()[1].encode()).hexdigest()
    if value.get("accountFingerprint") not in (None, fingerprint):
        raise ValueError("This workflow belongs to another credential; use the original account")
    value["accountFingerprint"] = fingerprint


def resolve(request, value):
    if isinstance(request, dict):
        if "fromStep" in request:
            record = value["steps"][request["fromStep"]]
            if record["state"] != "succeeded" or request["output"] not in record.get("outputs", {}):
                raise ValueError("Dependency is not ready")
            selected = copy.deepcopy(record["outputs"][request["output"]])
            if "index" in request:
                if not isinstance(selected, list) or request["index"] >= len(selected):
                    raise ValueError("Dependency output index is unavailable")
                selected = selected[request["index"]]
            return selected
        return {k: resolve(v, value) for k, v in request.items()}
    if isinstance(request, list):
        return [resolve(v, value) for v in request]
    return request


def ready_steps(value):
    return [step for step in value["plan"]["steps"] if value["steps"][step["id"]]["state"] == "pending"
            and all(value["steps"][dep]["state"] == "succeeded" for dep in step["dependsOn"])]


@state.protect_operation
def estimate(workflow_id):
    from . import media
    with lock(workflow_id):
        value = load(workflow_id)
        entries = []
        for step in ready_steps(value):
            request, kind = resolve(step["request"], value), step["kind"]
            item = {"stepId": step["id"], "kind": kind, "request": request,
                    "duplicateRisk": value["steps"][step["id"]].get("duplicateRisk", False)}
            if kind in {"image", "video", "audio"}:
                bind_account(value)
                request = capabilities.normalize_generation_request(kind, request)
                item["request"] = request
                item["credits"] = generation.estimate_generation(kind, request)["estimate"]["frozenCredit"]
            elif kind == "upload":
                bind_account(value)
                if set(request) - {"files", "groupUniqId", "durationSeconds"}:
                    raise ValueError("Unknown upload option")
                item["uploadPreview"] = uploads.prepare_upload_plan(request.get("files"),
                    group_uniq_id=request.get("groupUniqId"), duration_seconds=request.get("durationSeconds"))
            elif kind == "download":
                bind_account(value)
                if set(request) - {"taskId", "output", "index", "force"}:
                    raise ValueError("Unknown download option")
                presentation.public_id(request.get("taskId"), "taskId")
                if type(request.get("index", 0)) is not int or request.get("index", 0) < 0:
                    raise ValueError("Download index must be a nonnegative integer")
                item["destination"] = media.output_path(request.get("output"), force=request.get("force", False))
            else:
                item["editPreview"] = media.preview(request)
            item["requestHash"] = digest(item["request"])
            entries.append(item)
        approval = {"approvalId": uuid.uuid4().hex, "revision": value["revision"], "createdAt": time.time(),
                    "entries": entries, "consumed": []}
        value["approval"] = approval
        save(value)
        quoted_ids = {item["stepId"] for item in entries}
        return {"workflowId": workflow_id, "revision": value["revision"], "approvalId": approval["approvalId"],
                "steps": entries, "readyCredits": sum(item.get("credits", 0) for item in entries),
                "deferredSteps": [s["id"] for s in value["plan"]["steps"] if value["steps"][s["id"]]["state"] == "pending" and s["id"] not in quoted_ids],
                "instruction": "Confirm these exact requests, selected upload files, local outputs and costs. Deferred steps are not included in readyCredits."}


def _reconcile(value):
    for step in value["plan"]["steps"]:
        row = value["steps"][step["id"]]
        if row["state"] not in ACTIVE | {"unknown"}:
            continue
        if step["kind"] in {"image", "video", "audio"}:
            bind_account(value)
            if not row.get("taskId") and row.get("attemptId") and state.attempt_exists(row["attemptId"]):
                attempt = state.attempt_record(row["attemptId"])
                row.update({k: attempt[k] for k in ("taskId",) if k in attempt})
                if not row.get("taskId"):
                    row["state"] = "failed" if attempt.get("state") == "failed" else "unknown"
            if row.get("taskId"):
                status, response = transport.send("GET", "/api/tasks/" + presentation.public_id(row["taskId"], "taskId"))
                task = transport.response_data(status, response)
                row["task"] = presentation.public_task_data(task)
                stage = task.get("step") if isinstance(task, dict) else None
                row["state"] = "succeeded" if stage == 2 else "failed" if stage == 3 else "submitted"
                if stage == 2:
                    row["outputs"] = {"taskId": row["taskId"]}
            elif row["state"] == "running":
                row["state"] = "unknown"
        elif step["kind"] == "upload":
            bind_account(value)
            if row.get("uploadPlanId"):
                record = uploads.upload_plan_record(row["uploadPlanId"])
                row["uploadResult"] = record
                if record.get("state") == "completed" and not record.get("failedOrUnknown") and not record.get("notAttempted"):
                    row["outputs"] = {"assetIds": [x["assetUniqId"] for x in record.get("uploaded", [])]}
                else:
                    row["state"] = "failed" if record.get("state") == "failed" else "unknown"
                    continue
            ids = row.get("outputs", {}).get("assetIds", [])
            if ids:
                assets = [real_human.get_asset(asset_id) for asset_id in ids]
                row["assets"] = assets
                row["state"] = "succeeded" if all(x.get("ready") is True for x in assets) else "processing"
                if any(x.get("step") == "FAILED" for x in assets):
                    row["state"] = "failed"
            else:
                row["state"] = "unknown"
        else:
            # A completed local output is never inferred from an existing filename.
            row["state"] = "unknown"


@state.protect_operation
def list_workflows(*, limit=20, offset=0):
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
        raise ValueError("Use limit 1-100 and a nonnegative offset")
    records, unreadable = [], []
    for path in directory().glob("*.json"):
        try:
            value = load(path.stem)
            summary = snapshot(value)
            records.append({key: summary[key] for key in ("workflowId", "revision", "title", "state")} | {"updatedAt": value.get("updatedAt", "")})
        except (OSError, ValueError, KeyError, TypeError, SystemExit):
            unreadable.append(path.name)
    records.sort(key=lambda row: (row["updatedAt"], row["workflowId"]), reverse=True)
    end = offset + limit
    return {"workflows": records[offset:end], "total": len(records), "nextOffset": end if end < len(records) else None,
            "unreadableRecords": unreadable}


@state.protect_operation
def get(workflow_id, *, refresh=False):
    with lock(workflow_id):
        value = load(workflow_id)
        if refresh:
            _reconcile(value)
            save(value)
        return snapshot(value)


@state.protect_operation
def run_next(workflow_id, *, approval_id, confirmed, progress=None):
    from . import media, downloads
    if confirmed is not True:
        raise ValueError("Confirm the complete workflow estimate before execution")
    with lock(workflow_id):
        value = load(workflow_id)
        approval = value.get("approval")
        if (not approval or approval.get("approvalId") != approval_id or approval["revision"] != value["revision"]
                or time.time() - approval["createdAt"] > 900):
            raise ValueError("Workflow approval is missing, stale or expired; estimate and confirm again")
        if any(row["state"] in {"failed", "unknown", "running"} for row in value["steps"].values()):
            raise ValueError("Reconcile or revise failed/uncertain steps before continuing")
        ready = {s["id"]: s for s in ready_steps(value)}
        item = next((e for e in approval["entries"] if e["stepId"] not in approval["consumed"] and e["stepId"] in ready), None)
        if item is None:
            return {**snapshot(value), "instruction": "No unconsumed ready step in this approval. Query running tasks or estimate the next batch."}
        step = ready[item["stepId"]]
        request, kind = resolve(step["request"], value), step["kind"]
        if kind in {"image", "video", "audio"}:
            request = capabilities.normalize_generation_request(kind, request)
        if digest(request) != item["requestHash"]:
            raise ValueError("Workflow inputs changed; estimate and confirm again")
        if kind != "compose":
            bind_account(value)
        row = value["steps"][step["id"]]
        row["state"] = "running"
        if kind in {"image", "video", "audio"}:
            row["attemptId"] = uuid.uuid4().hex
        if kind == "upload":
            row["uploadPlanId"] = item["uploadPreview"]["uploadPlanId"]
        approval["consumed"].append(step["id"])
        save(value)  # reserve before any external write
        try:
            if progress:
                progress({"compose": "Rendering video...", "download": "Downloading result...",
                          "upload": "Uploading selected files..."}.get(kind, "Submitting generation..."))
            if kind in {"image", "video", "audio"}:
                outcome = generation.create_generation(kind, request, confirmed=True,
                    attempt_id=row["attemptId"], approved_credits=item["credits"])
                row["result"] = dict(outcome)
                row["state"] = "submitted" if outcome.get("taskId") else outcome.get("state", "unknown")
                if outcome.get("taskId"):
                    row["taskId"] = outcome["taskId"]
            elif kind == "upload":
                outcome = uploads.execute_upload_plan(row["uploadPlanId"], confirmed=True)
                row["uploadResult"] = dict(outcome)
                row["outputs"] = {"assetIds": [x["assetUniqId"] for x in outcome.get("uploaded", [])]}
                row["state"] = "unknown" if outcome.get("failedOrUnknown") or outcome.get("notAttempted") else "processing"
            elif kind == "download":
                outcome = downloads.download_task(request["taskId"], request["output"],
                    index=request.get("index", 0), force=request.get("force", False))
                row.update(state="succeeded", outputs={"output": outcome["output"], "files": [outcome["output"]]})
            else:
                outcome = media.compose(request, confirmed=True, approval_hash=item["editPreview"]["approvalHash"])
                row.update(state="succeeded", outputs={"output": outcome["output"], "files": [outcome["output"]]}, result=outcome)
        except (ValueError, OSError, SystemExit, KeyboardInterrupt, results.CommandInterrupted) as error:
            # Even pre-submit failures are explicit stops; a new approved revision is required.
            row["state"] = "unknown" if kind == "upload" or (row.get("attemptId") and state.attempt_exists(row["attemptId"])) else "failed"
            row["error"] = presentation.sanitize_text_for_output(str(error))
            if isinstance(error, (results.CommandInterrupted, results.ApiError)):
                row["result"] = error.result
                if error.result.get("taskId"):
                    row["taskId"] = error.result["taskId"]
                if isinstance(error, results.CommandInterrupted):
                    row["interrupted"] = True
        try:
            save(value)
        except (OSError, SystemExit):
            return results.OperationResult({**snapshot(value), "localStateSaved": False,
                "instruction": "Retain these task/attempt IDs; query the workflow and ledger, never resubmit."}, "unknown")
        return results.OperationResult(snapshot(value), "failed" if row["state"] in {"failed", "unknown"} else "success")
