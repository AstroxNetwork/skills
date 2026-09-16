#!/usr/bin/env python3
"""Export the real argparse/MCP interface for release guidance checks. Offline only."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HIDDEN_COMPATIBILITY = {"auth set-key", "credits estimate"}


def load_cli():
    spec = importlib.util.spec_from_file_location("holycrab_interface", ROOT / "holycrab/scripts/holycrab_cli.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parser_inventory(parser, path=()):
    children = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if children:
        return [row for action in children for name, child in action.choices.items()
                for row in parser_inventory(child, (*path, name))]
    command = " ".join(path)
    return [{"command": command, "compatibilityOnly": command in HIDDEN_COMPATIBILITY,
             "arguments": [{"name": a.dest, "flags": a.option_strings, "required": a.required,
                            "nargs": a.nargs, "choices": list(a.choices) if a.choices is not None else None,
                            "type": getattr(a.type, "__name__", "string")}
                           for a in parser._actions if a.dest != "help"]}]


def command_examples(text):
    """Executable code blocks, not prose, responses or syntax-reference tables."""
    result = []
    for language, block in re.findall(r"```(bash|shell|powershell|text)\n(.*?)```", text, re.S):
        for line in block.replace("\\\n", " ").splitlines():
            line = line.strip()
            if line.startswith("holycrab "):
                result.append({"language": language, "command": line})
    return result


def public_inventory(cli=None):
    cli = cli or load_cli()
    return {"version": cli.VERSION, "commands": parser_inventory(cli.build_parser()), "mcpTools": cli.MCP_TOOLS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--website", type=Path, help="Optional isolated website checkout; read generated CLI MDX")
    args = parser.parse_args()
    inventory = public_inventory()
    paths = [ROOT / name for name in ("README.md", "HolyCrab CLI 使用指南.md", "holycrab/SKILL.md", "holycrab/references/api.md")]
    if args.website:
        paths += sorted(args.website.glob("docs-site/*/guide/cli/**/index.mdx"))
    inventory["examples"] = [{"source": path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT)
                              else "website/" + path.relative_to(args.website).as_posix(),
                              **example} for path in paths for example in command_examples(path.read_text(encoding="utf-8"))]
    print(json.dumps(inventory, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
