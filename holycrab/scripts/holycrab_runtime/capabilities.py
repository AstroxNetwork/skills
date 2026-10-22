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
        if len(value) < rule.get("minLength", 0) or len(value) > rule.get("maxLength", math.inf):
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
    for name, value in payload.items():
        rule = allowed.get(name)
        if isinstance(rule, dict):
            _validate_schema_value(name, value, rule)

    if kind == "audio":
        audio_fields = {"speaker", "audio_data", "audio_url"}
        image_fields = {"image_data", "image_url"}
        audio_count = 0
        image_count = 0
        for reference in payload.get("references", []) or []:
            active = {
                key for key, value in reference.items()
                if key in audio_fields | image_fields and isinstance(value, str) and value.strip()
            }
            if len(active) != 1:
                raise SystemExit("Each audio generation reference must contain exactly one supported source")
            if active & audio_fields:
                audio_count += 1
            else:
                image_count += 1
        limits = model.get("referenceLimits", {})
        if audio_count and image_count:
            raise SystemExit("Audio generation cannot mix image and audio references")
        if audio_count > limits.get("audio", 0) or image_count > limits.get("images", 0):
            raise SystemExit("Too many references for audio generation")
        prompt = payload.get("textPrompt", "")
        if re.search(r"(?:@图片[1-9]\d*|@Image[1-9]\d*(?![A-Za-z0-9_]))", prompt):
            raise SystemExit("Image mentions are not supported in audio generation prompts")
        mentions = [
            int(match.group(1) or match.group(2))
            for match in re.finditer(r"(?:@音频([1-9]\d*)|@Audio([1-9]\d*)(?![A-Za-z0-9_]))", prompt)
        ]
        if not audio_count and mentions:
            raise SystemExit("Audio generation prompts cannot mention audio without audio references")
        if audio_count and set(mentions) != set(range(1, audio_count + 1)):
            raise SystemExit("Audio generation prompts must mention every audio reference and no out-of-range reference")

    if kind == "image":
        image_urls = payload.get("imageUrls", [])
        if isinstance(image_urls, list) and len(image_urls) > model.get("referenceLimits", {}).get("images", 0):
            raise SystemExit("Too many reference images for the selected model")
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
        images = payload.get("imageAssetIds", []) or []
        videos = payload.get("videoAssetIds", []) or []
        audios = payload.get("audioAssetIds", []) or []
        first_frame = bool(payload.get("firstFrameAssetId"))
        last_frame = bool(payload.get("lastFrameAssetId"))
        frame_mode = first_frame or last_frame
        references = bool(images or videos or audios)
        rules = model.get("rules", {})
        if last_frame and not first_frame and not rules.get("lastFrameWithoutFirstFrame", False):
            raise SystemExit("lastFrameAssetId requires firstFrameAssetId")
        if frame_mode and references:
            raise SystemExit("Frame assets and reference assets cannot be combined")
        if task_type == "frames" and not first_frame and not rules.get("lastFrameWithoutFirstFrame", False):
            raise SystemExit("Video frame requests require firstFrameAssetId")
        limits = model.get("referenceLimits", {})
        for label, values, key in (("image", images, "images"), ("video", videos, "videos"), ("audio", audios, "audio")):
            maximum = limits.get(key)
            if type(maximum) is int and len(set(values)) > maximum:
                raise SystemExit(f"Too many {label} references for {model['id']}")
        if rules.get("textOnlyRequiresConcreteRatio", False):
            ratio = payload.get("ratio")
            if frame_mode:
                payload["ratio"] = "adaptive"
            if not frame_mode and not references and (ratio is None or ratio == "adaptive"):
                raise SystemExit(f"{model['label']} text-only requests require a concrete ratio")
            if references and ratio is None:
                payload["ratio"] = "adaptive"
            if audios and not images and not videos:
                raise SystemExit(f"{model['label']} cannot use audio-only references")
        elif audios and not images and not videos and not rules.get("audioOnlyReference", False) and model["id"] != "dreamina-seedance-2-5-260628":
            raise SystemExit("Audio references require at least one visual reference")
        if rules.get("size1KRequiresSquareRatio") and resolution == "1K" and payload.get("ratio") != "1:1":
            raise SystemExit("Agnes Video 2.5 size 1K requires ratio 1:1")
        source = bool(payload.get("sourceVideoAssetId"))
        if (task_type in {"edit", "extend"}) != source:
            raise SystemExit("Seedance edit/extend requests require sourceVideoAssetId")
        if source and (first_frame or last_frame):
            raise SystemExit("Seedance edit/extend requests cannot include frame assets")
        if model["id"] == "dreamina-seedance-2-5-260628":
            if videos and explicit_task_type is None:
                raise SystemExit("Seedance 2.5 video references require videoTaskType")
            video_ids = set(videos)
            if source:
                video_ids.add(payload["sourceVideoAssetId"])
            if len(video_ids) > limits.get("videos", math.inf):
                raise SystemExit("Too many video references for Seedance 2.5")
            if len(set(images)) + len(video_ids) + len(set(audios)) > limits.get("total", math.inf):
                raise SystemExit("Too many total references for Seedance 2.5")
            if task_type in {"frames", "edit", "extend"}:
                payload["ratio"] = "adaptive"


def normalize_generation_request(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SystemExit("Generation request must be an object")
    normalized = dict(payload)
    if kind == "video":
        model_id = normalized.get("model")
        if not isinstance(model_id, str):
            raise SystemExit("Generation request must include model")
        model = find_model(model_id)
        if model.get("generateAudioSupported") is True and model.get("generateAudioDefault") is True:
            normalized.setdefault("generateAudio", True)
    validate_generation_request(kind, normalized)
    return normalized
