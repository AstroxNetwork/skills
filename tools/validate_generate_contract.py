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


def java_string_set(text: str, name: str) -> set[str]:
    match = re.search(rf"\b{name}\s*=\s*Set\.of\((.*?)\);", text, re.DOTALL)
    if not match:
        return set()
    body = match.group(1)
    values = set(quoted_values(body))
    constants = dict(re.findall(r"(?:private\s+)?static\s+final\s+String\s+(\w+)\s*=\s*['\"]([^'\"]+)['\"]", text))
    values.update(constants[name] for name in re.findall(r"\b[A-Z][A-Z0-9_]+\b", body) if name in constants)
    return values


def validate(generate_root: Path, cli_root: Path) -> list[str]:
    errors: list[str] = []
    manifest = json.loads((cli_root / "holycrab/references/capabilities.json").read_text(encoding="utf-8"))
    image = (generate_root / "client/user-front/src/utils/imageGenerationParams.ts").read_text(encoding="utf-8")
    video_options = (generate_root / "client/user-front/src/utils/generationOptions.ts").read_text(encoding="utf-8")
    task_api = (generate_root / "client/user-front/src/api/tasks.ts").read_text(encoding="utf-8")
    seed_audio = (generate_root / "client/user-front/src/utils/seedAudioRules.ts").read_text(encoding="utf-8")
    seedance = (generate_root / "server/user/src/main/java/com/astrox/seedance/user/service/GenerationRequestValidator.java").read_text(encoding="utf-8")
    minimax = (generate_root / "server/user/src/main/java/com/astrox/seedance/user/service/MinimaxGenerationRequestValidator.java").read_text(encoding="utf-8")
    controller = (generate_root / "server/user/src/main/java/com/astrox/seedance/user/controller/UserTaskController.java").read_text(encoding="utf-8")
    upload = "\n".join((generate_root / path).read_text(encoding="utf-8") for path in (
        "client/user-front/src/utils/assetUploadLimits.ts",
        "client/user-front/src/utils/assetUploadValidation.ts",
    ))

    model_ids = {row["id"] for group in ("videoModels", "imageModels", "audioModels") for row in manifest[group]}
    for model in model_ids - {"seed-audio-1.0"}:
        if model not in image + seedance + minimax + controller:
            errors.append(f"Generate contract drift: model id missing: {model}")

    video_constants = dict(re.findall(r"export const\s+([A-Z0-9_]+_MODEL_VALUE)\s*=\s*['\"]([^'\"]+)['\"]", video_options))
    options_match = re.search(r"VIDEO_MODEL_OPTIONS\s*=\s*\[(.*?)\]\s*as\s+const", video_options, re.DOTALL)
    option_names = re.findall(r"value:\s*([A-Z0-9_]+_MODEL_VALUE)", options_match.group(1)) if options_match else []
    generate_video_ids = {video_constants[name] for name in option_names if name in video_constants}
    snapshot_video_ids = {row["id"] for row in manifest["videoModels"]}
    if generate_video_ids != snapshot_video_ids:
        errors.append("CLI snapshot drift: video model ids")

    image_options_match = re.search(r"IMAGE_MODEL_OPTIONS.*?=\s*\[(.*?)\]\s*\n\s*export const DEFAULT_IMAGE_MODEL", image, re.DOTALL)
    generate_image_ids = set(re.findall(r"\bvalue:\s*['\"]([^'\"]+)['\"]", image_options_match.group(1))) if image_options_match else set()
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
    require(seedance, r"imageCount\s*>\s*30\s*\|\|\s*videoCount\s*>\s*10\s*\|\|\s*audioCount\s*>\s*10", "Seedance 2.5 reference limits", errors)
    require(upload, r"ASSET_UPLOAD_BATCH_LIMIT\s*=\s*10", "asset batch limit", errors)
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
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("generate_root", type=Path)
    args = parser.parse_args()
    cli_root = Path(__file__).resolve().parents[1]
    errors = validate(args.generate_root.resolve(), cli_root)
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return 1
    print("HolyCrab CLI capability snapshot matches the read-only Generate contract.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
