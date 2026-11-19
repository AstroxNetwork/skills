#!/usr/bin/env python3
"""Fail closed when the read-only Generate main checkout drifts from the CLI snapshot."""

from __future__ import annotations

import argparse
import ast
import hashlib
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import re
import sys
from pathlib import Path


try:
    from tools.material_contract import SOURCE_FILES, validate_material_contract, wan_model_aliases
except ModuleNotFoundError:
    from material_contract import SOURCE_FILES, validate_material_contract, wan_model_aliases

# Only Seedance 4K implementation is overlaid on the independently pinned main.
# No other branch capabilities or frontend-only changes become CLI features.
SEEDANCE_4K_FILES = (
    "server/user/src/main/java/com/astrox/seedance/user/service/SeedanceGenerationRequestValidator.java",
    "server/user/src/main/java/com/astrox/seedance/user/service/DraftFinalGenerationService.java",
    "server/user/src/main/java/com/astrox/seedance/user/service/UserTaskService.java",
    "server/user/src/main/java/com/astrox/seedance/user/config/SeedanceCreditProperties.java",
    "server/common/src/main/java/com/astrox/seedance/common/integration/ark/SeedanceService.java",
    "server/user/src/main/resources/application.yml",
)


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
            if (properties.get(schema_field, {}).get("type") != "array"
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
        (r'if\s*\(SEEDANCE_25.equals\(model\)(?:\s*\|\|\s*SEEDANCE_25_FLASH.equals\(model\))?\)\s*range\([^;]*0,\s*(?:SEEDANCE_25_FLASH.equals\(model\)\s*\?\s*20\s*:\s*)?50,\s*true,\s*true', "Seedance combined reference limit"),
    ):
        require(rules, pattern, label, errors)
    for name in ("maxImages", "maxVideos", "maxAudios"):
        require(rules, rf'range\(errors,[^;]*\b{name},\s*true,\s*true', name + " inclusive validation", errors)


def validate_minimax_v071(generate_root: Path, manifest: dict) -> list[str]:
    """Check new model declarations and their independently owned server rules."""
    errors = []
    source = (generate_root / "server/user/src/main/java/com/astrox/seedance/user/service/MinimaxGenerationRequestValidator.java").read_text(encoding="utf-8")
    for pattern, label in (
        (r'getPrompt\(\).length\(\)\s*>\s*7000', 'H3 UTF-16 prompt bound'),
        (r'"MiniMax-H3-Highspeed".equals\(model\)\s*\?\s*Set.of\("768P"\)\s*:\s*max\s*\|\|\s*lite\s*\?\s*Set.of\("480P",\s*"768P"\)', 'new H3 resolutions'),
        (r'getDuration\(\)\s*<\s*\(max\s*\?\s*5\s*:\s*4\).*?getDuration\(\)\s*>\s*15', 'new H3 duration'),
        (r'if\s*\(frames\)\s*ratio\s*=\s*"adaptive"', 'H3 frame ratio'),
        (r'!frames\s*&&\s*\(!references\s*\|\|\s*lite\)\s*&&\s*"adaptive"', 'Lite concrete ratio'),
        (r'boolean lite\s*=\s*"MiniMax-H3-Lite".equals\(model\)', 'Lite model selection'),
    ):
        require(source, pattern, label, errors)
    models = {row['id']: row for row in manifest['videoModels']}
    for suffix, resolutions in [('Highspeed', ['768P']), ('Lite', ['480P', '768P'])]:
        model_id = 'MiniMax-H3-' + suffix
        model = models.get(model_id, {})
        name = 'minimax' + suffix + 'Video'
        schema = manifest['requestSchemas'].get(name, {})
        fields = schema.get('properties', {})
        if (model.get('requestSchemaRef') != name or schema.get('additionalProperties') is not False
                or set(schema.get('required', [])) != {'model', 'prompt', 'duration', 'resolution'}
                or fields.get('model', {}).get('const') != model_id
                or fields.get('resolution', {}).get('enum') != resolutions or model.get('resolutions') != resolutions
                or fields.get('duration') != {'type': 'integer', 'minimum': 4, 'maximum': 15}
                or model.get('durationSeconds') != {'min': 4, 'max': 15}
                or fields.get('prompt') != {'type': 'string', 'minLength': 1, 'maxLength': 7000, 'lengthUnit': 'utf16'}
                or set(fields.get('ratio', {}).get('enum', [])) != java_string_set(source, 'RATIOS')
                or set(model.get('ratios', [])) != java_string_set(source, 'RATIOS')):
            errors.append('CLI snapshot drift: ' + model_id + ' request contract')
        for key, expected in {'referenceRequiresConcreteRatio': suffix == 'Lite',
                              'textOnlyRequiresConcreteRatio': True, 'frameModeForcesAdaptive': True,
                              'framesAndReferencesMutuallyExclusive': True, 'audioOnlyReference': False,
                              'lastFrameWithoutFirstFrame': True, 'generateAudioSupported': False}.items():
            if model.get('rules', {}).get(key) is not expected:
                errors.append('CLI snapshot drift: ' + model_id + ' ' + key)
        base = models['MiniMax-H3']
        for key in ('materialRules', 'referenceLimits'):
            if model.get(key) != base.get(key):
                errors.append('CLI snapshot drift: ' + model_id + ' ' + key)
    return errors


def validate(generate_root: Path, cli_root: Path) -> list[str]:
    errors: list[str] = []
    manifest = json.loads((cli_root / "holycrab/references/capabilities.json").read_text(encoding="utf-8"))
    errors.extend(validate_new_model_snapshot(manifest))
    errors.extend(validate_minimax_v071(generate_root, manifest))
    image = (generate_root / "client/user-front/src/utils/imageGenerationParams.ts").read_text(encoding="utf-8")
    kling = (generate_root / "client/user-front/src/config/klingModels.ts").read_text(encoding="utf-8")
    kling_image = (generate_root / "client/user-front/src/utils/klingImage.ts").read_text(encoding="utf-8")
    minimax_image = (generate_root / "client/user-front/src/utils/minimaxImage.ts").read_text(encoding="utf-8")
    image += "\n" + minimax_image
    video_options = (generate_root / "client/user-front/src/utils/generationOptions.ts").read_text(encoding="utf-8")
    task_api = (generate_root / "client/user-front/src/api/tasks.ts").read_text(encoding="utf-8")
    seed_audio = (generate_root / "client/user-front/src/utils/seedAudioRules.ts").read_text(encoding="utf-8")
    seedance = (generate_root / "server/user/src/main/java/com/astrox/seedance/user/service/SeedanceGenerationRequestValidator.java").read_text(encoding="utf-8")
    minimax = (generate_root / "server/user/src/main/java/com/astrox/seedance/user/service/MinimaxGenerationRequestValidator.java").read_text(encoding="utf-8")
    agnes_options = (generate_root / "client/user-front/src/utils/agnesModels.ts").read_text(encoding="utf-8")
    agnes = (generate_root / "server/user/src/main/java/com/astrox/seedance/user/service/AgnesGenerationRequestValidator.java").read_text(encoding="utf-8")
    controller = (generate_root / "server/user/src/main/java/com/astrox/seedance/user/controller/UserTaskController.java").read_text(encoding="utf-8")
    berry = (generate_root / "server/user/src/main/java/com/astrox/seedance/user/service/MulerouterGenerationRequestValidator.java").read_text(encoding="utf-8")
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
        if model.lower() not in (image + kling + agnes_options + seedance + minimax + agnes + controller + berry).lower():
            errors.append(f"Generate contract drift: model id missing: {model}")

    constants = typescript_constants(video_options + agnes_options + kling + kling_image + minimax_image + image)
    aliases = wan_model_aliases(controller)
    options_match = re.search(r"VIDEO_MODEL_OPTIONS\s*=\s*\[(.*?)\]\s*as\s+const", video_options, re.DOTALL)
    generate_video_ids = option_model_ids(options_match.group(1), constants, errors) if options_match else set()
    generate_video_ids = {aliases.get(model, model) for model in generate_video_ids}
    snapshot_video_ids = {row["id"] for row in manifest["videoModels"]}
    if generate_video_ids != snapshot_video_ids:
        errors.append("CLI snapshot drift: video model ids (missing in CLI: " + ", ".join(sorted(generate_video_ids - snapshot_video_ids)) + "; absent from Generate: " + ", ".join(sorted(snapshot_video_ids - generate_video_ids)) + ")")

    image_options_match = re.search(r"IMAGE_MODEL_OPTIONS.*?=\s*\[(.*?)\]\s*\n\s*export const DEFAULT_IMAGE_MODEL", image, re.DOTALL)
    generate_image_ids = option_model_ids(image_options_match.group(1), constants, errors) if image_options_match else set()
    generate_image_ids.update(typescript_array(minimax_image, "MINIMAX_IMAGE_MODELS"))
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
    require(minimax, r"!frames\s*&&\s*\(!references\s*\|\|\s*lite\)\s*&&\s*\"adaptive\"", "H3 text-only ratio restriction", errors)
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
    expected_image_fields = {"prompt", "model", "size", "aspectRatio", "resolution", "outputFormat", "imageUrls", "imageAssetIds"}
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
    errors.extend(validate_material_contract(generate_root, cli_root, manifest))
    errors.extend(validate_release074(generate_root, manifest))
    errors.extend(validate_wan(cli_root, manifest, controller, task_api, aliases))
    errors.extend(validate_kling_image(generate_root, manifest, controller))
    return errors


def validate_wan(cli_root: Path, manifest: dict, controller: str, task_api: str,
                 aliases: dict[str, str]) -> list[str]:
    errors: list[str] = []
    settings = ast.parse((cli_root / "holycrab/scripts/holycrab_runtime/settings.py").read_text(encoding="utf-8"))
    assignments = {target.id: node.value for node in settings.body if isinstance(node, ast.Assign)
                   for target in node.targets if isinstance(target, ast.Name)}
    cli_aliases = ast.literal_eval(assignments["WAN_MODEL_ALIASES"])
    cli_routes = ast.literal_eval(assignments["GENERATION_ROUTES"])
    frontend_mapping = re.search(r'const WAN3_API_MODEL_IDS\s*=\s*\{([^}]+)\}', task_api)
    frontend_aliases = dict(re.findall(r"'([^']+)':\s*'([^']+)'", frontend_mapping[1])) if frontend_mapping else {}
    wan_schema = manifest["requestSchemas"].get("wanVideo", {}).get("properties", {})
    if (len(aliases) != 4 or aliases != frontend_aliases or aliases != cli_aliases
            or set(aliases.values()) != set(wan_schema.get("model", {}).get("enum", []))):
        errors.append("Generate contract drift: WAN public model mapping")
    if cli_routes.get("wanVideo") != ("/api/tasks/wan/freeze-credit", "/api/tasks/wan"):
        errors.append("CLI snapshot drift: WAN estimate and submit routes")
    for route, handler in (("wan", "submitMulerouterGeneration"),
                           ("wan/freeze-credit", "mulerouterGenerationFreezeCredit")):
        require(controller, rf'@PostMapping\("/{route}"\)\s*public [^\n]* {handler}\('
                r'[^}]*?translateWanModel\(body\);[^}]*?modelAccessService.requireAccess',
                "WAN " + route + " route and translation", errors)
    require(controller, r'@GetMapping\("/wan/\{uniqId:', "WAN result route", errors)
    return errors


def validate_kling_image(generate_root: Path, manifest: dict, controller: str) -> list[str]:
    errors: list[str] = []
    validator = (generate_root / "server/user/src/main/java/com/astrox/seedance/user/service/KlingImageRequestValidator.java").read_text(encoding="utf-8")
    dto = (generate_root / "server/user/src/main/java/com/astrox/seedance/user/vo/KlingImageRequest.java").read_text(encoding="utf-8")
    schema = manifest["requestSchemas"].get("klingImage", {})
    model = next((row for row in manifest["imageModels"] if row["id"] == "kling-image-4.0"), {})
    properties = schema.get("properties", {})
    refs = properties.get("references", {}).get("items", {})
    for name, fields in (("KlingImageRequest", properties), ("Reference", refs.get("properties", {}))):
        record = re.search(rf'record {name}\(([^)]+)\)', dto)
        if not record or {param.split()[-1] for param in record[1].split(",")} != set(fields):
            errors.append("CLI snapshot drift: Kling Image " + name + " fields")
    if (model.get("requestSchemaRef") != "klingImage" or schema.get("additionalProperties") is not False
            or set(schema.get("required", [])) != {"model", "prompt"}
            or properties.get("model", {}).get("const") != "kling-image-4.0"
            or refs.get("properties", {}).get("type", {}).get("const") != "refer_image"
            or set(refs.get("required", [])) != {"type", "assetId", "id"}
            or set(model.get("ratios", [])) != java_string_set(validator, "RATIOS")
            or set(properties.get("aspectRatio", {}).get("enum", [])) != set(model.get("ratios", []))
            or set(properties.get("resolution", {}).get("enum", [])) != {"2k", "4k", "8k"}
            or set(model.get("resolutions", [])) != {"2k", "4k", "8k"}
            or set(properties.get("resultType", {}).get("enum", [])) != {"single", "series"}
            or set(properties.get("seriesAmount", {}).get("enum", [])) != {"auto", *(str(n) for n in range(2, 11))}
            or (properties.get("n", {}).get("minimum"), properties.get("n", {}).get("maximum")) != (1, 9)
            or model.get("referenceLimits", {}).get("images") != 10):
        errors.append("CLI snapshot drift: Kling Image request limits")
    defaults = {"resultType": "single", "resolution": "2k", "aspectRatio": "auto", "n": 1, "seriesAmount": "4"}
    if any(properties.get(field, {}).get("default") != value for field, value in defaults.items()):
        errors.append("CLI snapshot drift: Kling Image request defaults")
    for route, handler in (("image-generation", "create"), ("image-generation/freeze-credit", "preview")):
        require(controller, rf'@PostMapping\("/{route}"\)\s*public [^{{]+'
                rf'\{{\s*if \(isKlingImage\(body\)\).*?klingImageTaskService\.{handler}\(',
                "Kling Image " + handler + " route", errors)
    return errors


def validate_release074(root: Path, manifest: dict) -> list[str]:
    """Audited Flash, Seedream 5.5 and Seedance 4K behavior."""
    errors = []
    models = {m['id']: m for group in ('videoModels', 'imageModels') for m in manifest[group]}
    flash = models.get('dreamina-seedance-2-5-flash-261115', {})
    image = models.get('dola-seedream-5-5-261028', {})
    for actual, expected, label in (
        (flash.get('resolutions'), ['480p', '720p'], 'Flash resolutions'),
        (flash.get('durationSeconds'), {'min': 4, 'max': 30}, 'Flash duration'),
        (flash.get('taskTypes'), ['reference', 'frames', 'edit', 'extend'], 'Flash modes'),
        (flash.get('draftSupported'), False, 'Flash draft restriction'),
        (image.get('sizes'), ['1K', '1.5K', '2K'], 'Seedream 5.5 sizes'),
        (image.get('outputFormatDefault'), 'png', 'Seedream 5.5 default format'),
        (image.get('customSize', {}).get('minPixels'), 921600, 'Seedream 5.5 min pixels'),
        (image.get('customSize', {}).get('maxPixels'), 4624220, 'Seedream 5.5 max pixels'),
    ):
        if actual != expected: errors.append('CLI snapshot drift: ' + label)
    if 'dimensionMultiple' in image.get('customSize', {}):
        errors.append('CLI snapshot drift: Seedream 5.5 dimension multiple')
    for key, limit in (('images', 10), ('videos', 5), ('audio', 5), ('total', 20)):
        if flash.get('referenceLimits', {}).get(key) != limit:
            errors.append('CLI snapshot drift: Flash reference ' + key)
    if image.get('referenceLimits', {}).get('images') != 10:
        errors.append('CLI snapshot drift: Seedream 5.5 reference images')
    server = root / 'server/user/src/main/java/com/astrox/seedance/user'
    validator = (server / 'service/SeedanceGenerationRequestValidator.java').read_text()
    materials = (server / 'service/TaskAssetValidator.java').read_text()
    final = (server / 'service/DraftFinalGenerationService.java').read_text()
    upstream = (root / SEEDANCE_4K_FILES[4]).read_text()
    for text, pattern, label in (
        (validator, r'"4k".equals\(resolution\).*?!Set.of\("seedance-2-0",\s*SEEDANCE_25_MODEL\).contains\(model\)', 'Seedance 2.5 normal 4K'),
        (validator, r'"1080p".equals\(resolution\).*?!Set.of\("seedance-2-0",\s*SEEDANCE_25_MODEL\).contains\(model\)', 'Flash excludes 1080p'),
        (validator, r'Boolean.TRUE.equals\(request.getDraft\(\)\).*?!SEEDANCE_25_MODEL.equals\(model\).*?!"480p".equals\(resolution\)', 'Flash excludes drafts'),
        (validator, r'SEEDREAM_55_MODEL\s*=\s*"dola-seedream-5-5-261028"', 'Seedream 5.5 identifier'),
        (validator, r'Set.of\("1K",\s*"1.5K",\s*"2K"\)', 'Seedream 5.5 sizes'),
        (validator, r'width\s*>\s*4624220L\s*/\s*height', 'Seedream 5.5 output pixels'),
        (validator, r'imageStrings\(body.get\("imageUrls"\)\).size\(\)\s*>\s*10', 'Seedream 5.5 reference count'),
        (final, r'Set.of\("1080p",\s*"4k"\).contains\(request.getResolution\(\)\)', 'draft final resolutions'),
        (final, r'child.setResolution\(request.getResolution\(\)\)', 'draft final task resolution'),
        (upstream, r'payload.put\("resolution",\s*resolution\)', 'upstream final resolution forwarding'),
        (materials, r'SEEDANCE_25_FLASH.equals\(model\)\s*\?\s*10', 'Flash image limit'),
        (materials, r'SEEDANCE_25_FLASH.equals\(model\)\s*\?\s*5', 'Flash video/audio limit'),
        (materials, r'seedream55\s*&&\s*image.*?maxBytes\s*=\s*30_000_000L;\s*inclusive\s*=\s*true', 'Seedream 5.5 material bytes'),
    ):
        require(text, pattern, label, errors)
    return errors


def source_paths() -> tuple[str, ...]:
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    paths = {node.right.value for node in ast.walk(tree)
             if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
             and isinstance(node.left, ast.Name) and node.left.id == "generate_root"
             and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str)}
    return tuple(sorted(paths | set(SOURCE_FILES) | set(SEEDANCE_4K_FILES)))


@contextmanager
def immutable_sources(repository: Path, main_ref: str, seedance_4k_ref: str):
    """A main snapshot plus an explicitly scoped overlay, with separate hashes."""
    with immutable_source(repository, main_ref) as (root, commit, inputs):
        overlay_commit = subprocess.check_output(['git', '-C', str(repository), 'rev-parse',
            '--verify', seedance_4k_ref + '^{commit}'], text=True).strip()
        if re.fullmatch(r'[0-9a-f]{40}', overlay_commit) is None:
            raise ValueError('Expected an immutable Seedance 4K commit')
        overlay_files = {}
        for name in SEEDANCE_4K_FILES:
            data = subprocess.check_output(['git', '-C', str(repository), 'show', overlay_commit + ':' + name])
            (root / name).write_bytes(data)
            overlay_files[name] = hashlib.sha256(data).hexdigest()
        yield root, commit, inputs, {'seedance4k': {'commit': overlay_commit, 'sourceFiles': overlay_files}}


@contextmanager
def immutable_source(repository: Path, ref: str):
    """Read committed blobs only; never switch refs or read dirty source files."""
    commit = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "--verify", ref + "^{commit}"], text=True).strip()
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError("Expected a full immutable Git commit")
    with tempfile.TemporaryDirectory(prefix="holycrab-generate-contract-") as temp:
        root = Path(temp)
        inputs = {}
        for name in source_paths():
            result = subprocess.run(["git", "-C", str(repository), "show", commit + ":" + name], capture_output=True)
            if result.returncode and name.endswith("/assetUploadValidation.ts"):
                continue  # Historical optional frontend rules moved to the backend.
            if result.returncode:
                raise ValueError("Required source missing from selected commit: " + name)
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(result.stdout)
            inputs[name] = hashlib.sha256(result.stdout).hexdigest()
        yield root, commit, inputs


def record_evidence(record_path: Path, cli_root: Path, commit: str, expected_main: str, inputs: dict,
                    *, overlays: dict | None = None, expected_4k: str | None = None) -> None:
    try:
        from tools.verify_release_acceptance import CANDIDATE_CONTRACT_TAGS, candidate_fingerprint, evidence_digest
    except ModuleNotFoundError:
        from verify_release_acceptance import CANDIDATE_CONTRACT_TAGS, candidate_fingerprint, evidence_digest
    if commit != expected_main or re.fullmatch(r"[0-9a-f]{40}", expected_main) is None:
        raise ValueError("Selected source must match the independently verified remote main SHA")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if record.get("version") not in CANDIDATE_CONTRACT_TAGS:
        raise ValueError("Local evidence recording requires an explicitly supported candidate version")
    if record.get('version') == 'v0.7.4':
        if not overlays or overlays.get('seedance4k', {}).get('commit') != expected_4k or not expected_4k:
            raise ValueError('Seedance 4K evidence must match the independently verified branch SHA')
    elif record.get("version") == "v0.7.5" and overlays:
        raise ValueError("CLI v0.7.5 requires a main-only source snapshot")
    fingerprint = candidate_fingerprint(cli_root)
    proof = {"status": "passed", "mode": "local-immutable-main", "verifiedMainCommit": commit,
             "verifiedAt": datetime.now(timezone.utc).isoformat(), "candidateSha256": fingerprint,
             "sourceFiles": inputs, "validatorSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if overlays:
        proof.update(mode='local-immutable-main-with-seedance-4k', overlays=overlays)
    proof["evidenceSha256"] = evidence_digest(proof)
    record["candidateSha256"] = fingerprint
    record["generateContractCheck"] = proof
    # Write only after strict validation succeeds; preserve real-person/publication gates.
    target = record_path.with_name(record_path.name + ".tmp")
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    target.replace(record_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("generate_root", type=Path)
    parser.add_argument("--ref", help="Read an immutable snapshot of this Git ref instead of the working tree")
    parser.add_argument("--write-evidence", type=Path, help="Record a successful strict check for this exact candidate")
    parser.add_argument("--expected-main-sha", help="Remote main SHA independently verified before recording evidence")
    parser.add_argument('--seedance-4k-ref', help='Overlay only the audited Seedance 4K source files from this ref')
    parser.add_argument('--expected-4k-sha', help='Independently verified remote 2.5-4k SHA for recording evidence')
    args = parser.parse_args()
    if args.write_evidence and (not args.ref or not args.expected_main_sha):
        parser.error("--write-evidence requires --ref and --expected-main-sha")
    if args.seedance_4k_ref and not args.ref:
        parser.error('--seedance-4k-ref requires --ref')
    cli_root = Path(__file__).resolve().parents[1]
    try:
        if args.seedance_4k_ref:
            with immutable_sources(args.generate_root.resolve(), args.ref, args.seedance_4k_ref) as (root, commit, inputs, overlays):
                errors = validate(root, cli_root)
                if not errors and args.write_evidence:
                    record_evidence(args.write_evidence, cli_root, commit, args.expected_main_sha, inputs,
                                    overlays=overlays, expected_4k=args.expected_4k_sha)
                print('Validated Generate main: ' + commit + '; Seedance 4K overlay: ' + overlays['seedance4k']['commit'])
        elif args.ref:
            with immutable_source(args.generate_root.resolve(), args.ref) as (root, commit, inputs):
                errors = validate(root, cli_root)
                if not errors and args.write_evidence:
                    record_evidence(args.write_evidence, cli_root, commit, args.expected_main_sha, inputs)
                print("Validated Generate commit: " + commit)
        else:
            errors = validate(args.generate_root.resolve(), cli_root)
    except (OSError, KeyError, ValueError, subprocess.SubprocessError) as error:
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
