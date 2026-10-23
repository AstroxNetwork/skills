"""HolyCrab capabilities responsibilities."""
from __future__ import annotations
from . import results as _results

import copy
import json
import math
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from . import settings as _settings
from . import state as _state

def capabilities_path() -> Path:
    if "HOLYCRAB_CAPABILITIES_PATH" in os.environ:
        raise SystemExit(
            "Capability overrides are not supported. Run `" + _results.clear_environment_command("HOLYCRAB_CAPABILITIES_PATH") + "` and try again."
        )
    candidates = [
        Path(str(_settings.RUNTIME_ROOT / "holycrab_cli.py")).resolve().parents[1] / "references" / "capabilities.json",
        Path(str(_settings.RUNTIME_ROOT / "holycrab_cli.py")).resolve().parent / "references" / "capabilities.json",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    raise SystemExit("HolyCrab capability manifest is missing; reinstall the CLI")


@contextmanager
def capability_operation():
    """Release-local snapshot, shared only inside this CLI command or MCP call."""
    token = _settings._CAPABILITY_OPERATION.set({})
    try:
        yield
    finally:
        _settings._CAPABILITY_OPERATION.reset(token)


def _capability_manifest() -> dict[str, Any]:
    cache = _settings._CAPABILITY_OPERATION.get()
    if cache is not None and "manifest" in cache:
        return cache["manifest"]
    value = _state.read_json_file(capabilities_path(), {})
    if not isinstance(value, dict):
        raise SystemExit("HolyCrab capability manifest is invalid")
    if cache is not None:
        cache["manifest"] = value
    return value


def load_capabilities() -> dict[str, Any]:
    return copy.deepcopy(_capability_manifest())


def all_models() -> list[dict[str, Any]]:
    cache = _settings._CAPABILITY_OPERATION.get()
    if cache is not None and "models" in cache:
        return copy.deepcopy(cache["models"])
    manifest = _capability_manifest()
    source = manifest.get("source")
    if not isinstance(source, dict):
        raise SystemExit("HolyCrab capability snapshot metadata is missing")
    snapshot_version = source.get("version")
    published_at = source.get("publishedAt")
    if not isinstance(snapshot_version, str) or not isinstance(published_at, str):
        raise SystemExit("HolyCrab capability snapshot version is missing")
    schemas = manifest.get("requestSchemas")
    if not isinstance(schemas, dict):
        raise SystemExit("HolyCrab capability request schemas are missing")
    output = []
    for kind, group in (("video", "videoModels"), ("image", "imageModels"), ("audio", "audioModels")):
        for model in manifest.get(group, []):
            resolved = {
                "kind": kind,
                "capabilitySnapshotVersion": snapshot_version,
                "capabilitySnapshotPublishedAt": published_at,
                **model,
            }
            schema_ref = resolved.pop("requestSchemaRef", None)
            schema = schemas.get(schema_ref)
            if not isinstance(schema, dict):
                raise SystemExit(f"HolyCrab capability schema is missing: {schema_ref}")
            resolved["requestSchema"] = schema
            output.append(resolved)
    if cache is not None:
        cache["models"] = output
        cache["modelIndex"] = {model["id"]: model for model in output}
    return copy.deepcopy(output)


def capability_snapshot() -> dict[str, Any]:
    models = all_models()
    first = models[0] if models else {}
    return {
        "snapshotVersion": first.get("capabilitySnapshotVersion"),
        "publishedAt": first.get("capabilitySnapshotPublishedAt"),
        "models": models,
    }


def find_model(model_id: str) -> dict[str, Any]:
    cache = _settings._CAPABILITY_OPERATION.get()
    if cache is not None:
        if "modelIndex" not in cache:
            all_models()
        if model_id in cache["modelIndex"]:
            return copy.deepcopy(cache["modelIndex"][model_id])
        raise SystemExit(f"Unknown public model: {model_id}. Run `holycrab models list`.")
    for model in all_models():
        if model.get("id") == model_id:
            return model
    raise SystemExit(f"Unknown public model: {model_id}. Run `holycrab models list`.")


def _validate_schema_value(name: str, value: Any, rule: dict[str, Any]) -> None:
    expected = rule.get("type")
    matches = {
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "boolean": type(value) is bool,
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }.get(expected, True)
    if not matches:
        raise SystemExit(f"Generation field {name} must be {expected}")
    if "enum" in rule and value not in rule["enum"]:
        raise SystemExit(f"Generation field {name} has an unsupported value")
    if "const" in rule and value != rule["const"]:
        raise SystemExit(f"Generation field {name} must be {rule['const']}")
    if isinstance(value, str):
        length = len(value.encode("utf-16-le", errors="surrogatepass")) // 2 if rule.get("lengthUnit") == "utf16" else len(value)
        if length < rule.get("minLength", 0) or length > rule.get("maxLength", math.inf):
            raise SystemExit(f"Generation field {name} has an invalid length")
    if type(value) is int and (value < rule.get("minimum", -math.inf) or value > rule.get("maximum", math.inf)):
        raise SystemExit(f"Generation field {name} is out of range")
    if isinstance(value, list):
        if len(value) > rule.get("maxItems", math.inf):
            raise SystemExit(f"Generation field {name} contains too many items")
        if rule.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            raise SystemExit(f"Generation field {name} must not contain duplicates")
        item_rule = rule.get("items")
        if isinstance(item_rule, dict):
            for index, item in enumerate(value):
                _validate_schema_value(f"{name}[{index}]", item, item_rule)
    if isinstance(value, dict):
        properties = rule.get("properties", {})
        if rule.get("additionalProperties") is False and set(value) - set(properties):
            raise SystemExit(f"Generation field {name} contains an unsupported property")
        for required in rule.get("required", []):
            if required not in value:
                raise SystemExit(f"Generation field {name} is missing {required}")
        for key, item in value.items():
            if isinstance(properties.get(key), dict):
                _validate_schema_value(f"{name}.{key}", item, properties[key])
        one_of = rule.get("oneOf")
        if isinstance(one_of, list) and not any(
            set(option.get("required", [])).issubset(value)
            for option in one_of if isinstance(option, dict)
        ):
            raise SystemExit(f"Generation field {name} does not match a supported form")


def validate_generation_request(kind: str, payload: dict[str, Any]) -> None:
    if kind == "audio":
        model = next((item for item in all_models() if item.get("kind") == "audio"), None)
    else:
        model_id = payload.get("model")
        model = find_model(model_id) if isinstance(model_id, str) else None
    if not isinstance(model, dict) or model.get("kind") != kind:
        raise SystemExit(f"Generation request does not select a {kind} model")
    schema = model.get("requestSchema")
    if not isinstance(schema, dict):
        raise SystemExit("Generation capability schema is missing")
    allowed = schema.get("properties", {})
    unknown = set(payload) - set(allowed)
    if schema.get("additionalProperties") is False and unknown:
        raise SystemExit(f"Unsupported generation field: {sorted(unknown)[0]}")
    missing = [name for name in schema.get("required", []) if name not in payload]
    if missing:
        raise SystemExit(f"Missing required generation field: {missing[0]}")
    if kind == "audio" and isinstance(payload.get("references"), list):
        if any(isinstance(item, dict) and set(item) & {"speaker", "audio_data", "image_data"} for item in payload["references"]):
            raise SystemExit("Legacy audio sources are no longer supported; upload the reference and use {assetId, type}, or provide an audio_url/image_url URL")
    for name, value in payload.items():
        rule = allowed.get(name)
        if isinstance(rule, dict):
            _validate_schema_value(name, value, rule)

    if kind == "audio":
        for reference in payload.get("references", []) or []:
            sources = [name for name in ("assetId", "audio_url", "image_url") if name in reference]
            if len(sources) != 1 or not isinstance(reference[sources[0]], str) or not reference[sources[0]].strip():
                raise SystemExit("Each audio reference must provide exactly one nonempty assetId or URL")
            if sources[0] == "assetId" and reference.get("type") not in {"audio", "image"}:
                raise SystemExit("Audio assetId references require type audio or image")
            if sources[0] != "assetId" and "type" in reference:
                raise SystemExit("Use type only with an audio reference assetId")

    if kind == "image":
        size = payload.get("size")
        if isinstance(size, str) and size:
            named = {str(item).lower() for item in model.get("sizes", [])}
            if size.lower() not in named:
                match = re.fullmatch(r"(\d+)\s*[xX×]\s*(\d+)", size)
                if not match:
                    raise SystemExit("Image size must be a supported named size or WIDTHxHEIGHT")
                width, height = int(match.group(1)), int(match.group(2))
                custom = model.get("customSize", {})
                pixels = width * height
                if pixels < custom.get("minPixels", 1) or pixels > custom.get("maxPixels", 0):
                    raise SystemExit("Image custom size pixel count is outside the selected model limit")
                if width <= 0 or height <= 0 or not 1 / 16 <= width / height <= 16:
                    raise SystemExit("Image custom size aspect ratio must be between 1:16 and 16:1")
                multiple = custom.get("dimensionMultiple")
                if type(multiple) is int and multiple > 1 and (width % multiple or height % multiple):
                    raise SystemExit(f"Image width and height must be multiples of {multiple}")
        if payload.get("outputFormat") is not None and not model.get("outputFormatSupported"):
            raise SystemExit(f"Model {model['id']} does not support outputFormat")
        resolution = payload.get("resolution")
        if resolution is not None and resolution.upper() not in model.get("sizes", []):
            raise SystemExit(f"Image resolution {resolution} is not supported by {model['id']}")

    if kind == "video":
        resolution = payload.get(model.get("resolutionFieldInRequest", "resolution"))
        if resolution not in model.get("resolutions", []):
            raise SystemExit(f"Resolution {resolution} is not supported by {model['id']}")
        duration = payload.get(model.get("durationFieldInRequest", "duration"))
        explicit_task_type = payload.get("videoTaskType")
        task_type = explicit_task_type or ("frames" if payload.get("firstFrameAssetId") else "reference")
        if task_type not in model.get("taskTypes", ["reference", "frames"]):
            raise SystemExit(f"Video task type {task_type} is not supported by {model['id']}")
        if task_type == "edit":
            if duration != -1:
                raise SystemExit("Seedance edit requests require duration -1")
        else:
            limits = model.get("durationSeconds", {})
            if type(duration) is not int or duration < limits.get("min", 0) or duration > limits.get("max", 0):
                raise SystemExit("Video duration is outside the selected model limit")
        if model.get("rules", {}).get("size1KRequiresSquareRatio") and resolution == "1K" and payload.get("ratio") != "1:1":
            raise SystemExit("Agnes Video 2.5 size 1K requires ratio 1:1")
        if model["id"].startswith("berry-") and not payload.get("prompt", "").strip():
            raise SystemExit("Berry prompt must not be blank")


def normalize_generation_request(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SystemExit("Generation request must be an object")
    normalized = dict(payload)
    if kind == "video":
        model_id = normalized.get("model")
        if isinstance(model_id, str) and model_id.strip().lower().startswith("berry-"):
            model_id = normalized["model"] = model_id.strip().lower()
            if isinstance(normalized.get("resolution"), str):
                normalized["resolution"] = normalized["resolution"].strip().lower()
        if not isinstance(model_id, str):
            raise SystemExit("Generation request must include model")
        model = find_model(model_id)
        if model_id.startswith("berry-"):
            for name, default in {"duration": 5, "resolution": "1080p", "ratio": "adaptive", "audio": True}.items():
                normalized.setdefault(name, default)
        if model.get("generateAudioSupported") is True and model.get("generateAudioDefault") is True:
            normalized.setdefault("generateAudio", True)
    validate_generation_request(kind, normalized)
    return normalized
