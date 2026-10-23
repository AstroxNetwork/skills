"""Additional source-contract checks for server-owned material validation."""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

SERVER = "server/user/src/main/java/com/astrox/seedance/user/"
SOURCE_FILES = tuple(SERVER + name for name in (
    "api/ApiResult.java", "api/AssetValidationError.java", "api/GlobalExceptionHandler.java",
    "serialization/AssetSerializer.java", "vo/AssetVO.java", "vo/MulerouterGenerationRequest.java",
    "service/MulerouterGenerationRequestValidator.java", "service/TaskAssetValidator.java",
    "service/AssetRuleValidator.java", "service/TaskAssetResolver.java", "service/TaskAssetParameterValidator.java",
    "service/ImageGenerationRequestValidator.java", "service/AudioGenerationRequestValidator.java",
    "service/UserAssetService.java", "controller/UserAssetController.java",
)) + ("client/user-front/src/config/wan3Models.ts",)
BERRY = {"berry-1.0", "berry-1.0-pro", "berry-1.0-turbo", "berry-1.0-pro-turbo"}
ERROR_FIELDS = ("code", "scope", "assetId", "assetIds", "role", "requestPath", "field", "actual", "rule", "message", "retryable")
METADATA_FIELDS = ("durationSeconds", "mediaFormat", "metadataStatus", "metadataErrorCode", "metadataUpdatedAt",
                   "width", "height", "sizeBytes", "frameRate", "videoCodec", "audioCodec")


# Independently audited source/description pair. These are deliberately conservative:
# ANY source-rule change requires re-audit, not automatic acceptance of a changed bound.
# This gate does not execute a second copy of the server's material policy.
AUDITED_RULE_SOURCES = {'server/user/src/main/java/com/astrox/seedance/user/service/TaskAssetValidator.java': 'cc6f1efe8398b6dc83041b39603bf17a525dd98e0a9205ecfa4f72386bc7a659', 'server/user/src/main/java/com/astrox/seedance/user/service/AssetRuleValidator.java': '3e9bb56da7dceb77b910b797512af8e5fb64747e0846e2b758b97a46820bdc2f', 'server/user/src/main/java/com/astrox/seedance/user/service/TaskAssetResolver.java': '501cf7989a1bcb785837399617cc2c1717e1ec1dd64c3c01d9406f328a16888c', 'server/user/src/main/java/com/astrox/seedance/user/service/TaskAssetParameterValidator.java': 'ca82e618aedab57c0b6a1a16d27bc458dea62b11fb5f764cce5aaf3e13708b2d'}
AUDITED_DESCRIPTION_SHA256 = "a64ff2be235c78e0af333f02da7b1e8ca813c8e64f88872b5fcd121a0fa6a482"


def validate_audited_material_pair(root: Path, manifest: dict) -> list[str]:
    errors = []
    for name, expected in AUDITED_RULE_SOURCES.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            errors.append("Material contract drift: audited rule source changed: " + name)
    facts = {row["id"]: {key: row[key] for key in ("materialRules", "referenceLimits", "rules") if key in row}
             for group in ("videoModels", "imageModels", "audioModels") for row in manifest[group]}
    actual = hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if actual != AUDITED_DESCRIPTION_SHA256:
        errors.append("Material contract drift: audited CLI material descriptions changed")
    return errors


def validate_material_contract(root: Path, cli_root: Path, manifest: dict) -> list[str]:
    errors = validate_audited_material_pair(root, manifest)
    def require(text, pattern, label):
        if re.search(pattern, text, re.S | re.M) is None:
            errors.append("Material contract drift: " + label)
    sources = {name: (root / name).read_text(encoding="utf-8") for name in SOURCE_FILES}
    def server(name): return sources[SERVER + name]
    record = server("api/AssetValidationError.java")
    for field in ERROR_FIELDS:
        require(record, r"\b" + field + r"\b", "error field " + field)
    require(server("api/ApiResult.java"), r"List<AssetValidationError>\s+errors", "errors array")
    require(server("api/GlobalExceptionHandler.java"), r"setErrors\(ex.getErrors\(\)\)", "error array serialization")
    for field in METADATA_FIELDS:
        suffix = field[0].upper() + field[1:]
        require(server("vo/AssetVO.java"), r"\b" + field + r"\s*;", "asset field " + field)
        require(server("serialization/AssetSerializer.java"), r"set" + suffix + r"\(row.get" + suffix + r"\(\)\)", "metadata projection " + field)
    require(server("controller/UserAssetController.java"), r'@PostMapping\("/\{uniqId:[^"\n]+\}/metadata/retry"\)', "metadata retry endpoint")
    require(server("service/UserAssetService.java"), r"validateDurationForAssetType[\s\S]*?图片素材不需要 duration_seconds", "upload argument boundary")
    require(server("service/TaskAssetValidator.java"), r'if\s*\(!"READY".equals\(a.getMetadataStatus\(\)\)\)\s*return errors', "optional metadata policy")
    require(server("service/TaskAssetParameterValidator.java"), r"useIds\s*\?\s*ids\s*:\s*urls", "image ID precedence")
    require(server("service/TaskAssetParameterValidator.java"), r'item.get\("assetId"\)', "audio material IDs")
    require(server("service/TaskAssetParameterValidator.java"), r'item.get\("type"\)', "audio material type")
    for field in ("imageAssetIds", "imageUrls"):
        require(server("service/ImageGenerationRequestValidator.java"), r'body.get\("' + field + r'"\)', "image field " + field)
    for field in ("audio_url", "image_url"):
        require(server("service/AudioGenerationRequestValidator.java"), r'entry.get\("' + field + r'"\)', "audio field " + field)

    berry = server("service/MulerouterGenerationRequestValidator.java")
    models = re.search(r'List.of\(([^;]+?)\).contains\(request.getModel\(\)\)', berry, re.S)
    if not models or set(re.findall(r'"([^"]+)"', models.group(1))) != BERRY:
        errors.append("Material contract drift: Berry models")
    for pattern, label in (
        (r'getDuration\(\)\s*<\s*2\s*\|\|\s*request.getDuration\(\)\s*>\s*30', "Berry duration"),
        (r'WAN3_PROMPT_MAX_CHARACTERS\s*=\s*20_000', "Berry prompt limit"),
        (r'getPrompt\(\).length\(\)\s*>\s*WAN3_PROMPT_MAX_CHARACTERS', "Berry UTF-16 prompt limit usage"),
        (r'pro\s*\?\s*List.of\("1080p",\s*"2k",\s*"4k"\)\s*:\s*List.of\("480p",\s*"720p",\s*"1080p"\)', "Berry resolutions"),
        (r'List.of\("16:9",\s*"4:3",\s*"1:1",\s*"3:4",\s*"9:16",\s*"adaptive"\)', "Berry ratios"),
        (r'getSeed\(\)\s*<\s*0', "Berry seed"),
        (r'Boolean.TRUE.equals\(request.getEnableThinking\(\)\)', "Berry thinking restriction"),
        (r'Boolean.TRUE.equals\(request.getWatermark\(\)\)', "Berry watermark restriction"),
    ): require(berry, pattern, label)
    dto = server("vo/MulerouterGenerationRequest.java")
    for field, literal in (("duration", "5"), ("resolution", '"1080p"'), ("ratio", '"adaptive"'), ("audio", "true"), ("enableThinking", "false"), ("watermark", "false")):
        require(dto, r"\b" + field + r"\s*=\s*" + re.escape(literal) + r"\s*;", "Berry default " + field)
    for pattern, label in (
        (r'wan\(model\)\s*\?\s*10', "Berry image count"),
        (r'wan\(model\)\s*\?\s*5', "Berry video/audio count"),
        (r'videoSeconds.add\(BigDecimal.valueOf\(context.outputDuration\(\)\)\),\s*30', "Berry combined video/output duration"),
    ): require(server("service/TaskAssetValidator.java"), pattern, label)
    cli_models = {row["id"]: row for row in manifest["videoModels"]}
    for model_id in BERRY:
        model = cli_models.get(model_id, {})
        resolutions = {"1080p", "2k", "4k"} if "-pro" in model_id else {"480p", "720p", "1080p"}
        if set(model.get("resolutions", [])) != resolutions or model.get("durationSeconds") != {"min": 2, "max": 30}:
            errors.append("Material contract drift: CLI Berry output parameters " + model_id)
        if any(model.get("referenceLimits", {}).get(key) != count for key, count in (("images", 10), ("videos", 5), ("audio", 5))):
            errors.append("Material contract drift: CLI Berry reference description " + model_id)
    schemas = manifest["requestSchemas"]
    if "imageAssetIds" not in schemas["imageGeneration"]["properties"]:
        errors.append("Material contract drift: CLI image IDs")
    audio_fields = schemas["audioGeneration"]["properties"]["references"]["items"]["properties"]
    if not {"assetId", "type", "audio_url", "image_url"} <= set(audio_fields) or {"speaker", "audio_data", "image_data"} & set(audio_fields):
        errors.append("Material contract drift: CLI audio reference shapes")
    # Material limits remain descriptive; executable request schemas only check shape.
    def material_schema(value, path=""):
        if isinstance(value, dict):
            if any(name in path for name in ("AssetIds", "references", "imageUrls")) and {"maxItems", "uniqueItems"} & set(value):
                errors.append("Material contract drift: local material limit " + path)
            for key, child in value.items(): material_schema(child, path + "." + key)
        elif isinstance(value, list):
            for child in value: material_schema(child, path)
    material_schema(schemas)
    # Inspect the actual request boundary rather than accepting a matching token in comments.
    tree = ast.parse((cli_root / "holycrab/scripts/holycrab_runtime/generation.py").read_text(encoding="utf-8"))
    estimate = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "estimate_generation")
    calls = [node for node in ast.walk(estimate) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "send"]
    payloads = [kw.value for call in calls for kw in call.keywords if kw.arg == "payload"]
    if len(payloads) != 1 or not isinstance(payloads[0], ast.Name) or payloads[0].id != "normalized":
        errors.append("Material contract drift: estimate must send the normalized body for audio too")
    return errors
