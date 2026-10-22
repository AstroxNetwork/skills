#!/usr/bin/env python3
"""Fail closed when the read-only Generate main checkout drifts from the CLI snapshot."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def require(text: str, pattern: str, label: str, errors: list[str]) -> None:
    if re.search(pattern, text, re.MULTILINE | re.DOTALL) is None:
        errors.append(f"Generate contract drift: {label}")


def quoted_values(text: str) -> list[str]:
    return re.findall(r"['\"]([^'\"]+)['\"]", text)


def typescript_array(text: str, name: str) -> list[str]:
    match = re.search(rf"(?:export\s+)?const\s+{re.escape(name)}\s*=\s*\[(.*?)\]\s*as\s+const", text, re.DOTALL)
    return quoted_values(match.group(1)) if match else []


def typescript_constants(text: str) -> dict[str, str]:
    return dict(re.findall(r"export const\s+([A-Z0-9_]+)\s*=\s*['\"]([^'\"]+)['\"]", text))


def option_model_ids(body: str, constants: dict[str, str], errors: list[str]) -> set[str]:
    values = re.findall(r"\bvalue:\s*(['\"][^'\"]+['\"]|[A-Z][A-Z0-9_]*)", body)
    ids = set()
    for value in values:
        if value[0] in "\"'":
            ids.add(value[1:-1])
        elif value in constants:
            ids.add(constants[value])
        else:
            errors.append("Generate contract drift: unresolved option constant: " + value)
    return ids


def java_string_set(text: str, name: str) -> set[str]:
    match = re.search(rf"\b{name}\s*=\s*Set\.of\((.*?)\);", text, re.DOTALL)
    if not match:
        return set()
    body = match.group(1)
    values = set(quoted_values(body))
    constants = dict(re.findall(r"(?:private\s+)?static\s+final\s+String\s+(\w+)\s*=\s*['\"]([^'\"]+)['\"]", text))
    values.update(constants[name] for name in re.findall(r"\b[A-Z][A-Z0-9_]+\b", body) if name in constants)
    return values


def validate_new_model_snapshot(manifest: dict) -> list[str]:
    """Check the CLI side of the independently verified public contract."""
    errors: list[str] = []
    models = {row["id"]: row for group in ("videoModels", "imageModels") for row in manifest[group]}
    cases = (
        ("MiniMax-H3-Max", "minimaxMaxVideo", "resolution", "duration", 5, 15, {"images": 9, "videos": 3, "audio": 3},
         {"textOnlyRequiresConcreteRatio": True, "frameModeForcesAdaptive": True, "lastFrameWithoutFirstFrame": True,
          "framesAndReferencesMutuallyExclusive": True, "audioOnlyReference": False, "generateAudioSupported": False}),
        ("agnes-video-2.5", "agnesVideo", "size", "seconds", 4, 12, {"images": 8, "videos": 1, "audio": 3},
         {"lastFrameWithoutFirstFrame": True, "framesAndReferencesMutuallyExclusive": True,
          "audioOnlyReference": True, "generateAudioSupported": False, "size1KRequiresSquareRatio": True}),
        ("agnes-image-2.5-flash", "agnesImage", "size", None, None, None, {"images": 14}, {}),
    )
    for model_id, schema_name, size_key, duration_key, minimum, maximum, limits, rules in cases:
        model = models.get(model_id, {})
        schema = manifest["requestSchemas"].get(schema_name, {})
        properties = schema.get("properties", {})
        if model.get("requestSchemaRef") != schema_name or schema.get("additionalProperties") is not False:
            errors.append(f"CLI snapshot drift: {model_id} independent schema")
        required = {"model", "prompt", size_key}
        if model_id != "MiniMax-H3-Max":
            required.add("ratio")
        if duration_key:
            required.add(duration_key)
            if (properties.get(duration_key, {}).get("type") != "integer"
                    or model.get("durationSeconds") != {"min": minimum, "max": maximum}
                    or model.get("durationFieldInRequest", "duration") != duration_key
                    or model.get("resolutionFieldInRequest", "resolution") != size_key):
                errors.append(f"CLI snapshot drift: {model_id} duration or request field metadata")
        if set(schema.get("required", [])) != required:
            errors.append(f"CLI snapshot drift: {model_id} required fields")
        for field, limit in limits.items():
            schema_field = {"images": "imageAssetIds", "videos": "videoAssetIds", "audio": "audioAssetIds"}[field]
            if (properties.get(schema_field, {}).get("maxItems") != limit
                    or model.get("referenceLimits", {}).get(field) != limit):
                errors.append(f"CLI snapshot drift: {model_id} {field} reference limit")
        for rule, expected in rules.items():
            if model.get("rules", {}).get(rule) is not expected:
                errors.append(f"CLI snapshot drift: {model_id} {rule}")
        if model_id == "agnes-image-2.5-flash":
            if set(properties) != required | {"imageAssetIds"} or model.get("referenceFieldInRequest") != "imageAssetIds":
                errors.append("CLI snapshot drift: Agnes image request fields")
            if model.get("outputFormatSupported") is not False or model.get("customSizeSupported") is not False:
                errors.append("CLI snapshot drift: Agnes image output settings")
        elif model_id == "agnes-video-2.5":
            expected_fields = required | {"imageAssetIds", "videoAssetIds", "audioAssetIds", "firstFrameAssetId", "lastFrameAssetId"}
            if set(properties) != expected_fields:
                errors.append("CLI snapshot drift: Agnes video request fields")
        values_key = "resolutions" if duration_key else "sizes"
        if set(model.get(values_key, [])) != set(properties.get(size_key, {}).get("enum", [])) or set(model.get("ratios", [])) != set(properties.get("ratio", {}).get("enum", [])):
            errors.append(f"CLI snapshot drift: {model_id} capability enums")
    return errors


def validate_central_asset_rules(rules: str, errors: list[str]) -> None:
    """Check the shared server rules after asset validation moved out of the UI."""
    for pattern, label in (
        (r'maxImages\s*=\s*SEEDANCE_25.equals\(model\)\s*\?\s*30[^;]*"agnes-image-2.5-flash".equals\(model\)\s*\?\s*14\s*:\s*agnes\(model\)\s*\?\s*8', "Seedance and Agnes image reference limits"),
        (r'maxVideos\s*=\s*SEEDANCE_25.equals\(model\)\s*\?\s*10[^;]*"agnes-image-2.5-flash".equals\(model\)\s*\?\s*0\s*:\s*agnes\(model\)\s*\?\s*1', "Seedance and Agnes video reference limits"),
        (r'maxAudios\s*=\s*SEEDANCE_25.equals\(model\)\s*\?\s*10[^;]*"agnes-image-2.5-flash".equals\(model\)\s*\?\s*0\s*:\s*3', "Seedance and Agnes audio reference limits"),
        (r'if\s*\(\(first\s*\|\|\s*last\)\s*&&\s*references\).*?throw combination', "frame/reference exclusivity"),
        (r'if\s*\(SEEDANCE_25.equals\(model\)\)\s*range\([^;]*0,\s*50,\s*true,\s*true', "Seedance combined reference limit"),
    ):
        require(rules, pattern, label, errors)
    for name in ("maxImages", "maxVideos", "maxAudios"):
        require(rules, rf'range\(errors,[^;]*\b{name},\s*true,\s*true', name + " inclusive validation", errors)


def validate(generate_root: Path, cli_root: Path) -> list[str]:
    errors: list[str] = []
    manifest = json.loads((cli_root / "holycrab/references/capabilities.json").read_text(encoding="utf-8"))
    errors.extend(validate_new_model_snapshot(manifest))
    image = (generate_root / "client/user-front/src/utils/imageGenerationParams.ts").read_text(encoding="utf-8")
    video_options = (generate_root / "client/user-front/src/utils/generationOptions.ts").read_text(encoding="utf-8")
    task_api = (generate_root / "client/user-front/src/api/tasks.ts").read_text(encoding="utf-8")
    seed_audio = (generate_root / "client/user-front/src/utils/seedAudioRules.ts").read_text(encoding="utf-8")
    seedance = (generate_root / "server/user/src/main/java/com/astrox/seedance/user/service/GenerationRequestValidator.java").read_text(encoding="utf-8")
    minimax = (generate_root / "server/user/src/main/java/com/astrox/seedance/user/service/MinimaxGenerationRequestValidator.java").read_text(encoding="utf-8")
    agnes_options = (generate_root / "client/user-front/src/utils/agnesModels.ts").read_text(encoding="utf-8")
    agnes = (generate_root / "server/user/src/main/java/com/astrox/seedance/user/service/AgnesGenerationRequestValidator.java").read_text(encoding="utf-8")
    controller = (generate_root / "server/user/src/main/java/com/astrox/seedance/user/controller/UserTaskController.java").read_text(encoding="utf-8")
    upload = (generate_root / "client/user-front/src/utils/assetUploadLimits.ts").read_text(encoding="utf-8")
    old_upload_rules = generate_root / "client/user-front/src/utils/assetUploadValidation.ts"
    if old_upload_rules.exists():
        upload += "\n" + old_upload_rules.read_text(encoding="utf-8")
    else:
        # Upload constraints now belong to the server. Still compare the exact
        # size and inclusive boundary; a missing/moved rule must fail closed.
        rules = (generate_root / "server/user/src/main/java/com/astrox/seedance/user/service/TaskAssetValidator.java").read_text(encoding="utf-8")
        constants = (generate_root / "server/user/src/main/java/com/astrox/seedance/user/service/AssetRuleValidator.java").read_text(encoding="utf-8")
        require(rules, r'if\s*\(real\s*&&\s*video\)\s*\{\s*maxBytes\s*=\s*50\s*\*\s*MIB;\s*inclusive\s*=\s*true;', "real-human video size and inclusive boundary", errors)
        require(constants, r'MIB\s*=\s*1024L?\s*\*\s*1024', "asset size unit", errors)

    central_rules = generate_root / "server/user/src/main/java/com/astrox/seedance/user/service/TaskAssetValidator.java"
    centralized = "TaskAssetValidator.validateSelection" in seedance and "TaskAssetValidator.validateSelection" in agnes
    if centralized:
        validate_central_asset_rules(central_rules.read_text(encoding="utf-8"), errors)

    model_ids = {row["id"] for group in ("videoModels", "imageModels", "audioModels") for row in manifest[group]}
    for model in model_ids - {"seed-audio-1.0"}:
        if model.lower() not in (image + agnes_options + seedance + minimax + agnes + controller).lower():
            errors.append(f"Generate contract drift: model id missing: {model}")

    constants = typescript_constants(video_options + agnes_options)
    options_match = re.search(r"VIDEO_MODEL_OPTIONS\s*=\s*\[(.*?)\]\s*as\s+const", video_options, re.DOTALL)
    generate_video_ids = option_model_ids(options_match.group(1), constants, errors) if options_match else set()
    snapshot_video_ids = {row["id"] for row in manifest["videoModels"]}
    if generate_video_ids != snapshot_video_ids:
        errors.append("CLI snapshot drift: video model ids (missing in CLI: " + ", ".join(sorted(generate_video_ids - snapshot_video_ids)) + "; absent from Generate: " + ", ".join(sorted(snapshot_video_ids - generate_video_ids)) + ")")

    image_options_match = re.search(r"IMAGE_MODEL_OPTIONS.*?=\s*\[(.*?)\]\s*\n\s*export const DEFAULT_IMAGE_MODEL", image, re.DOTALL)
    generate_image_ids = option_model_ids(image_options_match.group(1), constants, errors) if image_options_match else set()
    snapshot_image_ids = {row["id"] for row in manifest["imageModels"]}
    if generate_image_ids != snapshot_image_ids:
        errors.append("CLI snapshot drift: image model ids")

    require(image, r"seedream-5-0-lite-260128[\s\S]*?requiresMultipleOf16:\s*false", "Lite multiple-of-16 rule", errors)
    require(image, r"seedream-4-5-251128[\s\S]*?requiresMultipleOf16:\s*false", "4.5 multiple-of-16 rule", errors)
    require(task_api, r"'wav'\s*\|\s*'mp3'\s*\|\s*'pcm'\s*\|\s*'ogg_opus'", "Seed Audio formats", errors)
    require(task_api, r"startDate\?:\s*string[\s\S]*endDate\?:\s*string[\s\S]*taskType\?:\s*TaskType", "task list filters", errors)
    require(task_api, r"audioIds\?:\s*string", "string audioIds response", errors)
    require(task_api, r"interface ImageGenerationParams[\s\S]*prompt:\s*string[\s\S]*model:\s*string[\s\S]*size:\s*string[\s\S]*aspectRatio:\s*string[\s\S]*resolution:\s*string[\s\S]*outputFormat:\s*string[\s\S]*imageUrls\?:\s*string\[\]", "image request fields", errors)
    require(minimax, r"ratio\s*=\s*references\s*\?\s*\"adaptive\"\s*:\s*null", "H3 reference ratio default", errors)
    require(minimax, r"!frames\s*&&\s*!references\s*&&\s*\"adaptive\"", "H3 text-only ratio restriction", errors)
    if not centralized:
        require(seedance, r"imageCount\s*>\s*30\s*\|\|\s*videoCount\s*>\s*10\s*\|\|\s*audioCount\s*>\s*10", "Seedance 2.5 reference limits", errors)
    require(upload, r"ASSET_UPLOAD_BATCH_LIMIT\s*=\s*10", "asset batch limit", errors)
    if old_upload_rules.exists():
        require(upload, r"MAX_REAL_HUMAN_VIDEO_UPLOAD_BYTES\s*=\s*50\s*\*\s*1024\s*\*\s*1024", "real-human video size", errors)
    require(controller, r"TaskType\.parseNullable\(taskType\)", "task type parsing", errors)

    seedance_schema = manifest["requestSchemas"]["seedanceVideo"]["properties"]
    enum_pairs = (
        (set(seedance_schema["model"]["enum"]), java_string_set(seedance, "MODELS"), "Seedance model enum"),
        (set(seedance_schema["resolution"]["enum"]), java_string_set(seedance, "RESOLUTIONS"), "Seedance resolution enum"),
        (set(seedance_schema["ratio"]["enum"]), java_string_set(seedance, "RATIOS"), "Seedance ratio enum"),
        (set(seedance_schema["videoTaskType"]["enum"]), java_string_set(seedance, "VIDEO_TASK_TYPES"), "Seedance task type enum"),
        (set(manifest["requestSchemas"]["minimaxVideo"]["properties"]["resolution"]["enum"]),
         {"768P", "2K"}, "MiniMax resolution enum"),
        (set(manifest["requestSchemas"]["minimaxVideo"]["properties"]["ratio"]["enum"]),
         java_string_set(minimax, "RATIOS"), "MiniMax ratio enum"),
    )
    for snapshot, generate, label in enum_pairs:
        if snapshot != generate:
            errors.append(f"CLI snapshot drift: {label}")

    if set(typescript_array(video_options, "MINIMAX_H3_RESOLUTION_OPTIONS")) != {"768P", "2K"}:
        errors.append("Generate contract drift: MiniMax frontend resolutions")
    if set(typescript_array(seed_audio, "SEED_AUDIO_FORMATS")) != set(manifest["audioModels"][0]["formats"]):
        errors.append("CLI snapshot drift: Seed Audio format enum")
    require(seed_audio, r"SEED_AUDIO_REFERENCE_LIMITS\s*=\s*\{\s*audio:\s*3,\s*image:\s*1\s*\}", "Seed Audio reference limits", errors)
    require(seed_audio, r"audio_mention_required[\s\S]*audio_mention_missing[\s\S]*audio_mention_out_of_range", "Seed Audio prompt mention rules", errors)

    image_models = {row["id"]: row for row in manifest["imageModels"]}
    for model in ("seedream-5-0-lite-260128", "seedream-4-5-251128"):
        if "dimensionMultiple" in image_models[model]["customSize"]:
            errors.append(f"CLI snapshot drift: {model} must not require a dimension multiple")
    audio = manifest["audioModels"][0]
    if audio["formats"] != ["wav", "mp3", "pcm", "ogg_opus"]:
        errors.append("CLI snapshot drift: Seed Audio formats")
    image_fields = set(manifest["requestSchemas"]["imageGeneration"]["properties"])
    expected_image_fields = {"prompt", "model", "size", "aspectRatio", "resolution", "outputFormat", "imageUrls"}
    if image_fields != expected_image_fields:
        errors.append("CLI snapshot drift: image request fields")
    # Compare independent schemas and cross-field rules, rather than broadening the H3 schema.
    expected_schemas = {
        "minimaxMaxVideo": ("MiniMax-H3-Max", {"480P", "768P"}, "duration", 5, 15),
        "agnesVideo": ("agnes-video-2.5", {"720P", "1080P", "1K", "2K"}, "seconds", 4, 12),
        "agnesImage": ("agnes-image-2.5-flash", {"1K", "2K", "3K", "4K"}, None, None, None),
    }
    for name, (model_id, sizes, duration_key, minimum, maximum) in expected_schemas.items():
        schema = manifest["requestSchemas"].get(name, {})
        properties = schema.get("properties", {})
        size_key = "resolution" if name == "minimaxMaxVideo" else "size"
        if properties.get("model", {}).get("const") != model_id or set(properties.get(size_key, {}).get("enum", [])) != sizes:
            errors.append("CLI snapshot drift: " + name + " model or size")
        if duration_key and (properties.get(duration_key, {}).get("minimum"), properties.get(duration_key, {}).get("maximum")) != (minimum, maximum):
            errors.append("CLI snapshot drift: " + name + " duration")
    max_schema = manifest["requestSchemas"].get("minimaxMaxVideo", {}).get("properties", {})
    if set(max_schema.get("ratio", {}).get("enum", [])) != java_string_set(minimax, "RATIOS"):
        errors.append("CLI snapshot drift: MiniMax Max ratios")
    if set(typescript_array(video_options, "MINIMAX_H3_MAX_RESOLUTION_OPTIONS")) != {"480P", "768P"}:
        errors.append("Generate contract drift: MiniMax Max frontend resolutions")
    require(minimax, r'"480P",\s*"768P"', "Max backend resolutions", errors)
    require(minimax, r'getDuration\(\)\s*<\s*\(max\s*\?\s*5\s*:\s*4\)', "Max minimum duration", errors)
    require(minimax, r'getDuration\(\)\s*>\s*15', "MiniMax maximum duration", errors)
    for name, ratio_set in (("agnesVideo", "VIDEO_RATIOS"), ("agnesImage", "RATIOS")):
        properties = manifest["requestSchemas"].get(name, {}).get("properties", {})
        if set(properties.get("ratio", {}).get("enum", [])) != java_string_set(agnes, ratio_set):
            errors.append("CLI snapshot drift: " + name + " ratios")
    for pattern, label in (
        (r'getSeconds\(\)\s*<\s*4.*?getSeconds\(\)\s*>\s*12', "Agnes video duration"),
        (r'"1K"\.equals\(size\).*?"1:1"\.equals\(ratio\)', "Agnes 1K square restriction"),
        (r'clean\(request\.getImageAssetIds\(\),\s*video\s*\?\s*8\s*:\s*14', "Agnes image reference limits"),
        (r'clean\(request\.getVideoAssetIds\(\),\s*video\s*\?\s*1\s*:\s*0', "Agnes video reference limit"),
        (r'clean\(request\.getAudioAssetIds\(\),\s*video\s*\?\s*3\s*:\s*0', "Agnes audio reference limit"),
        (r'frames\s*&&\s*references', "Agnes frame/reference exclusivity"),
    ):
        if not centralized or label in {"Agnes video duration", "Agnes 1K square restriction"}:
            require(agnes, pattern, label, errors)
    require(controller, r'@PostMapping\("/agnes-generation"\)', "Agnes submit route", errors)
    require(controller, r'@PostMapping\("/agnes-generation/freeze-credit"\)', "Agnes estimate route", errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("generate_root", type=Path)
    args = parser.parse_args()
    cli_root = Path(__file__).resolve().parents[1]
    try:
        errors = validate(args.generate_root.resolve(), cli_root)
    except (OSError, KeyError, ValueError) as error:
        print(f"ERROR: Could not verify the Generate source contract: {error}", file=sys.stderr)
        return 1
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return 1
    print("HolyCrab CLI capability snapshot matches the read-only Generate contract.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
