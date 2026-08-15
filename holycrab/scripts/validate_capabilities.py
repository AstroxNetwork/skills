#!/usr/bin/env python3
"""Validate that HolyCrab capability metadata and public docs agree."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def load_json(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"Could not load {path}: {error}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path} must contain a JSON object")
        return {}
    return value


def read_text(path: Path, errors: list[str]) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        errors.append(f"Could not read {path}: {error}")
        return ""


def validate_repository(repo_root: Path) -> list[str]:
    errors: list[str] = []
    skill_root = repo_root / "holycrab"
    manifest = load_json(skill_root / "references" / "capabilities.json", errors)
    api_reference = read_text(skill_root / "references" / "api.md", errors)
    readme = read_text(repo_root / "README.md", errors)
    skill = read_text(skill_root / "SKILL.md", errors)
    if errors:
        return errors

    if manifest.get("schemaVersion") != 1:
        errors.append("capabilities.json schemaVersion must be 1")
    source = manifest.get("source")
    source_version = source.get("version") if isinstance(source, dict) else None
    if not isinstance(source_version, str) or not source_version:
        errors.append("capabilities.json source.version must be a non-empty public version")

    all_models: list[dict[str, Any]] = []
    for group in ("videoModels", "imageModels", "audioModels"):
        models = manifest.get(group)
        if not isinstance(models, list) or not models:
            errors.append(f"capabilities.json {group} must be a non-empty array")
            continue
        if not all(isinstance(model, dict) for model in models):
            errors.append(f"capabilities.json {group} entries must be objects")
            continue
        all_models.extend(models)

    model_ids = [model.get("id") for model in all_models]
    if any(not isinstance(model_id, str) or not model_id for model_id in model_ids):
        errors.append("every capability model must have a non-empty string id")
    if len(model_ids) != len(set(model_ids)):
        errors.append("capability model ids must be unique")

    for model in all_models:
        model_id = model.get("id")
        if isinstance(model_id, str) and model_id not in api_reference:
            errors.append(f"API reference is missing model id: {model_id}")
        endpoints = model.get("endpoints")
        if not isinstance(endpoints, dict):
            errors.append(f"model {model_id} is missing endpoints")
            continue
        for endpoint in endpoints.values():
            if not isinstance(endpoint, str) or endpoint not in api_reference:
                errors.append(f"API reference is missing endpoint for {model_id}: {endpoint}")

    for label in ("Seedance 2.5", "MiniMax H3"):
        if label not in api_reference:
            errors.append(f"API reference is missing {label}")
    if "references/capabilities.json" not in skill:
        errors.append("SKILL.md must link to references/capabilities.json")
    return errors


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    errors = validate_repository(repo_root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("HolyCrab capability manifest and documentation are consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
