#!/usr/bin/env python3
"""Build deterministic, self-contained Skill downloads from the CLI source."""
import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def write_zip(path, files):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files.items()):
            entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data)


def build(output, website=None):
    catalog = json.loads((ROOT / "holycrab/references/creative/catalog.json").read_text())
    resources = {p.relative_to(ROOT / "holycrab").as_posix(): p.read_bytes()
                 for p in (ROOT / "holycrab/references").rglob("*") if p.is_file()}
    entries = [{"name": "holycrab", "title": "HolyCrab", "description": "Create videos, images and voiceovers, resume projects and compose finished videos with HolyCrab.", "entry": None}, *catalog["skills"]]
    packages = []
    for row in entries:
        name = row["name"]
        if row["entry"] is None:
            skill = (ROOT / "holycrab/SKILL.md").read_bytes()
            agent = (ROOT / "holycrab/agents/openai.yaml").read_bytes()
        else:
            skill = (f"---\nname: {name}\ndescription: {row['description']}\n---\n\n# {row['title']}\n\n"
                     f"Requires HolyCrab CLI {catalog['minCliVersion']} or later. Read [the workflow](references/creative/{row['entry']}) before planning this task. "
                     "Follow its execution and capability rules. Use the user's language. For prompt-only requests, return the prompt without uploading or generating. "
                     "For generation, use the installed CLI or its MCP tools; never call a third-party proxy.\n\n"
                     "Only read relevant linked resources. List CLI capabilities before selecting model parameters. "
                     "Review exact uploads and estimated credits with the user before submitting. Preserve attempts and task IDs; never retry uncertain submissions. "
                     "For multiple shots, use [persistent workflows](references/creative/workflows.md); deliver a verified final file with [composition](references/creative/composition.md) when requested.\n").encode()
            agent = (f'interface:\n  display_name: "{row["title"]}"\n  short_description: "{row["title"]} with HolyCrab"\n'
                     f'  default_prompt: "Use ${name} to plan and complete my creative task."\npolicy:\n  allow_implicit_invocation: true\n').encode()
        files = {"SKILL.md": skill, "agents/openai.yaml": agent, **resources}
        files["package.json"] = (json.dumps({"schemaVersion": 1, "name": name, "version": catalog["version"],
            "minCliVersion": catalog["minCliVersion"], "files": {p: hashlib.sha256(data).hexdigest() for p, data in sorted(files.items())}}, indent=2) + "\n").encode()
        target = output / (name + ".skill")
        write_zip(target, {name + "/" + p: data for p, data in files.items()})
        package = {"name": name, "title": row["title"], "description": row["description"], "version": catalog["version"],
                   "minCliVersion": catalog["minCliVersion"], "download": "/downloads/skills/" + target.name,
                   "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "bytes": target.stat().st_size,
                   "skillUrl": f"/.well-known/agent-skills/{name}/SKILL.md"}
        packages.append(package)
        if website:
            download = website / "public/downloads/skills" / target.name
            download.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target, download)
            for path, data in files.items():
                destination = website / "public/.well-known/agent-skills" / name / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
    release = json.loads((ROOT / "runtime-files.json").read_text())
    source_files = {name: (ROOT / name).read_bytes() for name in
                    {r["source"] for r in release["files"]} | {"install.sh", "install.ps1", "runtime-files.json", "LICENSE", "CREATIVE-SKILLS.md"}}
    source_files["LOCAL-INSTALL.md"] = b"# Local installation\n\nExtract this archive. In its directory run:\n\n```sh\nHOLYCRAB_INSTALL_SOURCE_DIR=\"$PWD\" HOLYCRAB_INSTALL_AGENTS=codex sh install.sh\n```\n\nThe installer verifies bundled hashes and configures Codex. Reopen Codex, then run holycrab skills doctor. Configure your API key locally with holycrab setup; never paste it into a chat. FFmpeg and FFprobe are optional local dependencies for final video composition.\n"
    source = output / "holycrab-cli-local.zip"
    write_zip(source, {"holycrab-cli/" + p: data for p, data in source_files.items()})
    manifest = {"schemaVersion": 1, "version": catalog["version"], "minCliVersion": catalog["minCliVersion"], "packages": packages,
                "cliBundle": {"download": "/downloads/skills/" + source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}}
    (output / "catalog.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    if website:
        shutil.copyfile(source, website / "public/downloads/skills" / source.name)
        shutil.copyfile(output / "catalog.json", website / "public/downloads/skills/catalog.json")
        destination = website / "src/app/skills/catalog.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(output / "catalog.json", destination)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "build/skills")
    parser.add_argument("--website", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    result = build(args.output, args.website)
    print(f"Built {len(result['packages'])} Skill bundles and one local CLI archive.")
