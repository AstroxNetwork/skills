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
    "service/SeedanceGenerationRequestValidator.java",
    "service/UserAssetService.java", "controller/UserAssetController.java",
)) + ("client/user-front/src/config/wan3Models.ts",)
BERRY = {"berry-1.0", "berry-1.0-pro", "berry-1.0-turbo", "berry-1.0-pro-turbo"}
ERROR_FIELDS = ("code", "scope", "assetId", "assetIds", "role", "requestPath", "field", "actual", "rule", "message", "retryable")
METADATA_FIELDS = ("duration", "mediaFormat", "metadataStatus", "metadataErrorCode", "metadataUpdatedAt",
                   "width", "height", "sizeBytes", "frameRate", "videoCodec", "audioCodec")


# Independently audited source/description pair. These are deliberately conservative:
# ANY source-rule change requires re-audit, not automatic acceptance of a changed bound.
# This gate does not execute a second copy of the server's material policy.
AUDITED_RULE_SOURCES = {'server/common/src/main/java/com/astrox/seedance/common/integration/ark/SeedanceService.java': ['e64cd71db409cbf518f830a7157be7ff92135e804d25147f57600c187102114c', '632e3ddd12fba8543e332df5d25883f0f276fd25fad69cbca5ebf587b58ab8c4'], 'server/user/src/main/java/com/astrox/seedance/user/config/SeedanceCreditProperties.java': ['21b789600fd4ceeee926b573a270143c7343c050d40b8edc3fe25fc8a313b0c3', '1ef482c2e0bdd2abe4c17b5601a7255cf51cae8b4ab60f94e8a60311772e7a14', 'ce97a226c1f44f7bf03114f85633df06b2f1fdee77546fc25418b16b1bcc7e60'], 'server/user/src/main/java/com/astrox/seedance/user/controller/UserTaskController.java': ['8322d0e438cb034d95a29d13551f7f773d2fd95fc46408ebeb706a5ebe0f34b6', '49bd796d3fbd276bd3157d5eb4b4e658acb218fe36821a2a60234281d20123ca'], 'server/user/src/main/java/com/astrox/seedance/user/service/AssetRuleValidator.java': '7d44d4e8be33ea663f65240dadcaf61ba88f9565837603a937f6cd5c6ba65178', 'server/user/src/main/java/com/astrox/seedance/user/service/DraftFinalGenerationService.java': ['4368e6ff1a8ef9447a7e22202b7cd1ae9a8765f03b5c1059d99e0ebaabd3a699', '4fc7433947929d96ca3afcb0e30f6aab14fb2d7893236ab496de86e09b4a233d', '9c39b4c32ed0e12e777abe6eb7b2b88693025f58f48163e6845fd50be8d70974'], 'server/user/src/main/java/com/astrox/seedance/user/service/KlingAssetValidator.java': '54555357cb2acc9cfc0a1d86368c237bf9b20bfb018f1e026853eab56ce8009a', 'server/user/src/main/java/com/astrox/seedance/user/service/KlingGenerationRequestValidator.java': '4ebcfce89a4b191d8b0d5096f4d416917be718d8797c4b47644ee3f51b85c983', 'server/user/src/main/java/com/astrox/seedance/user/service/MinimaxImageRequestValidator.java': '01b1c6baf83ba28cedd65951cf2d83d2a62395cb4126502498208cb851901015', 'server/user/src/main/java/com/astrox/seedance/user/service/SeedanceGenerationRequestValidator.java': ['356348b3454834e1eb58e747be6b72937537b3491c0e9aa5c8886487e77385eb', 'd9785ddce9885adf6d9ba4ccb0e59b6e315dc2e638f8141429553d6fa300e45b'], 'server/user/src/main/java/com/astrox/seedance/user/service/TaskAssetParameterValidator.java': '69e7dceea793fd0117ff90a792fd5e2f19bf9832fde2dea85222c111d7ddf7de', 'server/user/src/main/java/com/astrox/seedance/user/service/TaskAssetResolver.java': ['8fe0bcf751af9504eab114ae164b5363554d59833270916639bcd99940f41adf', 'dc5443131bddf9162bd85898489a1bdaff26ced153d6ce24260f527836f679dd'], 'server/user/src/main/java/com/astrox/seedance/user/service/TaskAssetValidator.java': '5ac63dbb83d8d4ed647dc2ea1dfa629787c001669e3bb7d1a053505c1ac3dcef', 'server/user/src/main/java/com/astrox/seedance/user/service/UserTaskService.java': ['04b8aee0a83fdc00b25cd54112b4dbc91217f3bd7b1e64916e6f5b439176801e', '0602f957d4f045f27b9d789ea98aaf2237874269a6365bcf4074778db2ccd12e', '8aeb35af327155f3a7080e740407697e8752ccf58792bb019a8ab66e0c29b727'], 'server/user/src/main/resources/application.yml': ['920fc07165acbafdfe9dd658dec9a29738d1202aef2ab2e45c9d06a499c4c92a', '2b6afe7dd395247078057b79095dedb87fdb00080811509c51326ef2a704ff2f', '5580919b50054d60cbea058812763e4ccf8e1bd2a44069a80afde39e62422614'], 'server/user/src/main/java/com/astrox/seedance/user/service/KlingImageRequestValidator.java': '480ac04f700ea8ec3e20922b50e2a68bdaed9afc5975723dd68fb6e69a999f8b', 'server/user/src/main/java/com/astrox/seedance/user/service/KlingImageTaskService.java': '5d093fb5d827d83c65ab8288644bc730a26aa9a584210b7239601a19520a50e3', 'server/user/src/main/java/com/astrox/seedance/user/serialization/TaskSerializer.java': 'af01a4cfc2c18adc2408c2331c81169929984e8457301eab05291d9050f0aad5'}
AUDITED_DESCRIPTION_SHA256 = "1eb6aa4af059e57cbfbae72d70eccf93764475cecb24719f36f662bf83439cb0"


SOURCE_FILES = tuple(dict.fromkeys(SOURCE_FILES + tuple(AUDITED_RULE_SOURCES)))


def wan_model_aliases(controller: str) -> dict[str, str]:
    mapping = re.search(r'private static void translateWanModel\([^)]*\)\s*\{.*?switch\s*\(body.getModel\(\)\)\s*\{([^}]*)\}', controller, re.S)
    return {legacy: public for public, legacy in re.findall(r'case\s+"([^"]+)"\s*->\s*"([^"]+)"', mapping[1])} if mapping else {}


def validate_audited_material_pair(root: Path, manifest: dict) -> list[str]:
    errors = []
    for name, expected in AUDITED_RULE_SOURCES.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() not in ([expected] if isinstance(expected, str) else expected):
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
        require(server("service/SeedanceGenerationRequestValidator.java"), r'body.get\("' + field + r'"\)', "image field " + field)
    for field in ("audio_url", "image_url"):
        require(server("service/SeedanceGenerationRequestValidator.java"), r'entry.get\("' + field + r'"\)', "audio field " + field)

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
    for model_id in wan_model_aliases(server("controller/UserTaskController.java")).values():
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
