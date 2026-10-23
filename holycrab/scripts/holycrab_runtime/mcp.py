"""HolyCrab mcp responsibilities."""
from __future__ import annotations

import argparse
import base64
import http.client
import json
import math
import re
import sys
from typing import Any
from . import capabilities as _capabilities
from . import generation as _generation
from . import lifecycle as _lifecycle
from . import presentation as _presentation
from . import real_human as _real_human
from . import results as _results
from . import settings as _settings
from . import state as _state
from . import tasks as _tasks
from . import transport as _transport
from . import uploads as _uploads


_update_versions_notified: set[tuple[int, int, int]] = set()
_UPDATE_AGENT_INSTRUCTION = (
    "When a tool result includes a HolyCrab update notice, briefly tell the user in their language "
    "that a newer CLI version is available and they can run holycrab update. "
    "Mention each version at most once per conversation, keep the requested task primary, "
    "and never install an update without the user's request."
)


def _pending_update_notification() -> dict[str, str] | None:
    try:
        _lifecycle.check_for_update(force=False, timeout=_settings.UPDATE_CHECK_TIMEOUT)
        notification = _lifecycle.cached_update_notification()
        latest = _lifecycle.strict_version(notification.get("latestVersion")) if notification else None
        if latest and (not _update_versions_notified or latest > max(_update_versions_notified)):
            return notification
    except (Exception, SystemExit):
        # Discovery, cache permissions and malformed cache data are advisory;
        # none may turn an already authorized business operation into a failure.
        pass
    return None


def _attach_update_notification(response: dict[str, Any], notification: dict[str, str] | None) -> dict[str, Any]:
    if notification:
        result = response["result"]
        result["content"].append({"type": "text", "text": notification["message"] + "\n" + _UPDATE_AGENT_INSTRUCTION})
        result.setdefault("_meta", {})["holycrab/update"] = notification
        latest = _lifecycle.strict_version(notification["latestVersion"])
        if latest:
            _update_versions_notified.add(latest)
    return response





MCP_TOOLS = [
    {"name": "asset_upload_plan_get", "description": "Read a local upload record; never retry an executing or uncertain upload.", "inputSchema": {"type": "object", "properties": {"uploadPlanId": _settings.ID_SCHEMA}, "required": ["uploadPlanId"], "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "cli_status", "description": "Read local CLI health and cached update status without contacting HolyCrab.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "account_get", "description": "Check the current HolyCrab account and credit balance.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}},
    {"name": "capabilities_list", "description": "List the public generation capability snapshot bundled with this release.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "capability_get", "description": "Get limits for one public model.", "inputSchema": {"type": "object", "properties": {"model": {"type": "string"}}, "required": ["model"], "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "generation_estimate", "description": "Estimate credit without creating a task.", "inputSchema": {"type": "object", "properties": {"kind": {"enum": ["video", "image", "audio"]}, "request": {"type": "object"}}, "required": ["kind", "request"], "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}},
    {"name": "generation_create", "description": "Create exactly one billable generation after explicit user confirmation. Supply one stable attemptId per confirmed draw. Query generation_attempt_get to reconcile it; never submit the same attempt again.", "inputSchema": {"type": "object", "properties": {"kind": {"enum": ["video", "image", "audio"]}, "request": {"type": "object"}, "confirmed": {"type": "boolean"}, "attemptId": {**_settings.ATTEMPT_ID_SCHEMA, "description": "Client-generated stable ID for this one confirmed draw."}}, "required": ["kind", "request", "confirmed", "attemptId"], "additionalProperties": False}, "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}},
    {"name": "generation_get", "description": "Get one generation task by ID.", "inputSchema": {"type": "object", "properties": {"taskId": {"type": "string"}}, "required": ["taskId"], "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}},
    {"name": "generation_list", "description": "List tasks for the current HolyCrab user.", "inputSchema": {"type": "object", "properties": {"page": {"type": "integer", "minimum": 1}, "pageSize": {"type": "integer", "minimum": 1, "maximum": 100}, "startDate": {"type": "string"}, "endDate": {"type": "string"}, "taskType": {"type": "string", "enum": ["IMAGE", "VIDEO", "AUDIO", "TEXT"]}}, "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}},
    {"name": "generation_attempt_list", "description": "List local one-shot generation submission attempts.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "generation_attempt_get", "description": "Get one local generation submission attempt.", "inputSchema": {"type": "object", "properties": {"attemptId": _settings.ATTEMPT_ID_SCHEMA}, "required": ["attemptId"], "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
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
                    {"authorizationId": _settings.ID_SCHEMA}, ("authorizationId",)),
    real_human_tool("real_human_groups_list", "List the current account's authorized people, with pagination.", PAGE_PROPERTIES),
    real_human_tool("real_human_group_rename", "Rename one authorized person using the public group ID.",
                    {"groupUniqId": _settings.ID_SCHEMA, "name": {"type": "string", "minLength": 1, "maxLength": 255}},
                    ("groupUniqId", "name"), read_only=False),
    real_human_tool("real_human_group_delete", "Permanently delete an authorized person, every asset in the group, and upstream records. Set confirmed=true only after the user explicitly approves this exact deletion.",
                    {"groupUniqId": _settings.ID_SCHEMA, "confirmed": {"type": "boolean"}},
                    ("groupUniqId", "confirmed"), read_only=False, destructive=True),
    real_human_tool("real_human_assets_list", "List assets in one authorized person's group. Processing readiness is independent of metadata and model material validation.",
                    {"groupUniqId": _settings.ID_SCHEMA, **PAGE_PROPERTIES}, ("groupUniqId",)),
    real_human_tool("real_human_asset_delete", "Permanently delete one real-human asset from storage, upstream records, and the database. Set confirmed=true only after the user explicitly approves this exact deletion.",
                    {"groupUniqId": _settings.ID_SCHEMA, "assetId": _settings.ID_SCHEMA, "confirmed": {"type": "boolean"}},
                    ("groupUniqId", "assetId", "confirmed"), read_only=False, destructive=True),
    real_human_tool("asset_get", "Query one public asset ID. Only UPLOADED_TO_ARK yields ready=true; report failures and do not reupload automatically.",
                    {"assetId": _settings.ID_SCHEMA}, ("assetId",)),
    real_human_tool("asset_metadata_retry", "Explicitly request metadata processing once for an existing asset. Return the real backend state or cooldown error; do not loop or reupload.",
                    {"assetId": _settings.ID_SCHEMA}, ("assetId",), read_only=False),
    real_human_tool("asset_upload_prepare", "Inspect 1-10 local files and return a short-lived complete preview. This does not upload bytes.",
                    {"files": {"type": "array", "minItems": 1, "maxItems": 10, "items": {"type": "string"}},
                     "groupUniqId": _settings.ID_SCHEMA, "durationSeconds": {"type": "integer", "minimum": 1}},
                    ("files",), read_only=True),
    real_human_tool("asset_upload_execute", "Execute one prepared upload plan only after the user confirms the complete target and file list.",
                    {"uploadPlanId": _settings.ID_SCHEMA, "confirmed": {"type": "boolean"}},
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
    with _capabilities.capability_operation():
        return _mcp_tool_call(name, arguments)


def _mcp_tool_call(name: str, arguments: dict[str, Any]) -> Any:
    validate_tool_arguments(name, arguments)
    if name == "real_human_authorization_start":
        return _real_human.create_authorization(arguments["name"])
    if name == "real_human_authorization_get":
        return _real_human.get_authorization(arguments["authorizationId"])
    if name == "real_human_groups_list":
        return _real_human.list_real_human_groups(arguments.get("page", 1), arguments.get("pageSize", 20))
    if name == "real_human_group_rename":
        return _real_human.rename_real_human_group(arguments["groupUniqId"], arguments["name"])
    if name == "real_human_group_delete":
        return _real_human.delete_real_human_group(arguments["groupUniqId"], arguments["confirmed"])
    if name == "real_human_assets_list":
        return _real_human.list_real_human_assets(arguments["groupUniqId"], arguments.get("page", 1), arguments.get("pageSize", 50))
    if name == "real_human_asset_delete":
        return _real_human.delete_real_human_asset(arguments["groupUniqId"], arguments["assetId"], arguments["confirmed"])
    if name == "asset_get":
        return _real_human.get_asset(arguments["assetId"])
    if name == "asset_metadata_retry":
        return _real_human.retry_asset_metadata(arguments["assetId"])
    if name == "asset_upload_prepare":
        return _uploads.prepare_upload_plan(arguments["files"], group_uniq_id=arguments.get("groupUniqId"),
                                   duration_seconds=arguments.get("durationSeconds"))
    if name == "asset_upload_plan_get":
        return _uploads.upload_plan_record(arguments["uploadPlanId"])
    if name == "asset_upload_execute":
        return _uploads.execute_upload_plan(arguments["uploadPlanId"], confirmed=arguments["confirmed"])
    if name == "cli_status":
        return _lifecycle.local_health_report(online=False)
    if name == "account_get":
        status, response, account = _tasks.query_account()
        if not _results.response_ok(status, response):
            _transport.response_data(status, response)
        if account is None:
            raise ValueError("Account verification returned an incomplete response; check the active API Key locally")
        return {**_presentation.public_account_data(account), "onboarding": _presentation.onboarding_guidance(configured=True, verified=True)}
    if name == "capabilities_list":
        return _capabilities.capability_snapshot()
    if name == "capability_get":
        return _capabilities.find_model(str(arguments.get("model", "")))
    if name == "generation_estimate":
        return _generation.estimate_generation(str(arguments.get("kind")), arguments.get("request"))
    if name == "generation_create":
        attempt_id = arguments.get("attemptId")
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise SystemExit("attemptId is required for generation_create")
        return _generation.create_generation(
            str(arguments.get("kind")),
            arguments.get("request"),
            confirmed=arguments.get("confirmed") is True,
            attempt_id=attempt_id.strip(),
        )
    if name == "generation_get":
        task_id = _presentation.public_id(arguments.get("taskId"), "taskId")
        status, response = _transport.send("GET", f"/api/tasks/{task_id}")
        return _presentation.public_task_data(_transport.response_data(status, response))
    if name == "generation_attempt_list":
        return {"attempts": _state.attempt_records()}
    if name == "generation_attempt_get":
        return _state.attempt_record(arguments["attemptId"])
    if name == "generation_list":
        query = _tasks.task_list_query(arguments.get("page", 1), arguments.get("pageSize", 20),
                                arguments.get("startDate"), arguments.get("endDate"), arguments.get("taskType"))
        status, response = _transport.send("GET", "/api/tasks", query=query)
        return _presentation.public_task_data(_transport.response_data(status, response))
    raise SystemExit(f"Unknown MCP tool: {name}")


def mcp_result(identifier: Any, value: Any) -> dict[str, Any]:
    safe = _presentation.sanitize_for_output(value)
    structured = safe if isinstance(safe, dict) else {"items": safe} if isinstance(safe, list) else {"value": safe}
    content = [{"type": "text", "text": json.dumps(safe, ensure_ascii=False)}]
    if isinstance(value, _results.AuthorizationStartResult) and value.qr_bytes:
        content.append({"type": "image", "mimeType": "image/png",
                        "data": base64.b64encode(value.qr_bytes).decode("ascii")})
    return {
        "jsonrpc": "2.0",
        "id": identifier,
        "result": {
            "content": content,
            "structuredContent": structured,
            "isError": isinstance(value, _results.OperationResult) and value.outcome != "success",
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
        negotiated = requested if requested in _settings.SUPPORTED_INITIALIZE_PROTOCOLS else _settings.LATEST_INITIALIZE_PROTOCOL
        notice = _lifecycle.cached_update_notice()
        instructions = (_settings.ONBOARDING_AGENT_INSTRUCTION
                        + " Read cli_status/account_get onboarding for the shared business examples. "
                        + _UPDATE_AGENT_INSTRUCTION)
        if notice:
            instructions += " " + notice
        return {
            "jsonrpc": "2.0",
            "id": identifier,
            "result": {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "holycrab-local", "version": _settings.VERSION},
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
        notification = None
        try:
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be an object")
            notification = _pending_update_notification()
            warning = _real_human.cleanup_authorization_qrs()
            value = mcp_tool_call(str(params.get("name", "")), arguments)
            if warning and isinstance(value, dict):
                value.setdefault("warning", warning)
            response = mcp_result(identifier, value)
        except _results.ApiError as error:
            response = mcp_result(identifier, error.result)
        except _results.CommandInterrupted as error:
            response = mcp_result(identifier, error.result)
            response["result"]["isError"] = True
        except (SystemExit, OSError, http.client.HTTPException, ValueError, TypeError, AttributeError) as error:
            text = _presentation.sanitize_text_for_output(str(error) or error.__class__.__name__)
            response = {
                "jsonrpc": "2.0",
                "id": identifier,
                "result": {"content": [{"type": "text", "text": text}], "isError": True},
            }
        return _attach_update_notification(response, notification)
    return {"jsonrpc": "2.0", "id": identifier, "error": {"code": -32601, "message": "Method not found"}}


def command_mcp_serve(args: argparse.Namespace) -> int:
    _update_versions_notified.clear()
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
