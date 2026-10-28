"""Release-bundled creative guidance; no network and no generation side effects."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from . import capabilities, settings


def resource_root() -> Path:
    return capabilities.capabilities_path().parent / "creative"


def catalog() -> dict:
    value = json.loads((resource_root() / "catalog.json").read_text(encoding="utf-8"))
    if value.get("schemaVersion") != 1 or not isinstance(value.get("skills"), list):
        raise ValueError("Invalid creative skill catalog; reinstall the CLI")
    return value


def list_skills() -> dict:
    value = catalog()
    return {**value, "cliVersion": settings.VERSION}


def get_skill(name: str, resource: str | None = None) -> dict:
    value = catalog()
    entry = next((row for row in value["skills"] if row["name"] == name), None)
    if entry is None:
        raise ValueError("Unknown skill; use holycrab skills list")
    selected = resource or entry["entry"]
    if selected not in entry["resources"]:
        raise ValueError("Unknown skill resource; use a resource ID from skills_list")
    root = resource_root().resolve()
    path = (root / selected).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Skill resource is missing or outside the installed bundle")
    data = path.read_bytes()
    return {"name": name, "version": value["version"], "resource": selected,
            "content": data.decode("utf-8"), "sha256": hashlib.sha256(data).hexdigest(),
            "resources": entry["resources"]}


def health() -> dict:
    try:
        value = catalog()
        for row in value["skills"]:
            for resource in row["resources"]:
                get_skill(row["name"], resource)
        return {"ok": True, "version": value["version"], "count": len(value["skills"]),
                "hostLoaded": "unknown", "instruction": "Start a new Codex session to verify skill discovery."}
    except (OSError, ValueError, KeyError, TypeError, SystemExit) as error:
        return {"ok": False, "error": str(error)}
