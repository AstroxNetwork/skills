#!/usr/bin/env python3
"""Verify release acceptance and explicitly scoped, unchanged-core reuse evidence."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

LOCAL_EVIDENCE_TAG = "v0.4.4"
LOCAL_EVIDENCE_TAGS = {LOCAL_EVIDENCE_TAG, "v0.5.0"}
REUSED_CORE_TAG = "v0.5.0"
REUSED_CORE_HASH = "dde70a8d891d97c6837276e9a3daa32016f7ca987e9cc92adec620e0912da94a"
MODEL_UPDATE_NODES = {"VERSION", "GENERATION_ROUTES", "MODEL_GENERATION_ROUTES", "PUBLIC_ACCOUNT_FIELDS",
                      "public_account_data", "format_terminal_result", "validate_generation_request", "generation_endpoints"}


def unchanged_core_fingerprint(source):
    tree = ast.parse(source)
    tree.body = [node for node in tree.body if not (
        isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in MODEL_UPDATE_NODES
        or isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in MODEL_UPDATE_NODES for target in node.targets)
    )]
    # AST dump defaults changed in 3.13; serialize stable fields explicitly.
    def canonical(value):
        if isinstance(value, ast.AST):
            return [type(value).__name__, [[name, canonical(item)] for name, item in ast.iter_fields(value)
                    if name != "type_params" or item]]
        if isinstance(value, list):
            return [canonical(item) for item in value]
        if isinstance(value, bytes):
            return ["bytes", value.hex()]
        if value is Ellipsis:
            return ["ellipsis"]
        return value
    return hashlib.sha256(json.dumps(canonical(tree), ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def validate_unchanged_core(flow, root):
    evidence = flow.get("unchangedCore")
    if root is None or not isinstance(evidence, dict) or evidence.get("baselineVersion") != "v0.4.4":
        raise ValueError("v0.5.0 reuse requires the checked v0.4.4 core baseline and local source")
    if evidence.get("sha256") != REUSED_CORE_HASH:
        raise ValueError("Unchanged-core evidence does not match the verified baseline")
    actual = unchanged_core_fingerprint((root / "holycrab/scripts/holycrab_cli.py").read_text(encoding="utf-8"))
    if actual != REUSED_CORE_HASH:
        raise ValueError("Real-person/upload or other non-model core code changed; fresh acceptance is required")

CONTRACT_FILES = ("holycrab/scripts/holycrab_cli.py", "holycrab/references/capabilities.json",
                  "tools/validate_generate_contract.py")


def validate_contract(record, root):
    if record.get("version") not in LOCAL_EVIDENCE_TAGS:
        raise ValueError("Local contract evidence requires an explicitly authorized release version")
    contract = record.get("generateMainContract")
    if not isinstance(contract, dict) or contract.get("status") != "passed" or contract.get("mode") != "local-synced-main":
        raise ValueError("A passed local Generate main contract record is required")
    local, remote = contract.get("localMainCommit"), contract.get("remoteMainCommit")
    if not isinstance(local, str) or re.fullmatch(r"[0-9a-f]{40}", local) is None or local != remote:
        raise ValueError("Validated local Generate main must match the verified remote main commit")
    datetime.fromisoformat(contract["verifiedAt"].replace("Z", "+00:00"))
    hashes = contract.get("files")
    if not isinstance(hashes, dict) or set(hashes) != set(CONTRACT_FILES):
        raise ValueError("Local contract evidence must identify all validated CLI inputs")
    for relative in CONTRACT_FILES:
        actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        if hashes[relative] != actual:
            raise ValueError("Local contract evidence is stale for " + relative + "; rerun the local check")



def evidence_digest(proof):
    value = {key: item for key, item in proof.items() if key != "evidenceSha256"}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_candidate_contract(record, root):
    """Verify recorded local evidence, not a live read of private remote main."""
    proof = record.get("generateContractCheck")
    if not isinstance(proof, dict) or proof.get("status") != "passed" or proof.get("mode") != "local-immutable-main":
        raise ValueError("A passed immutable local Generate contract record is required")
    if proof.get("evidenceSha256") != evidence_digest(proof):
        raise ValueError("Generate contract evidence content changed; rerun the strict check")
    commit = proof.get("verifiedMainCommit")
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError("Generate evidence requires an immutable main commit")
    verified = datetime.fromisoformat(proof["verifiedAt"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    if verified.tzinfo is None or verified > now + timedelta(minutes=5) or now - verified > timedelta(days=7):
        raise ValueError("Generate contract evidence is expired or future-dated; rerun within seven days")
    if proof.get("candidateSha256") != candidate_fingerprint(root) or record.get("candidateSha256") != proof["candidateSha256"]:
        raise ValueError("Generate evidence does not match this exact candidate")
    validator = Path(root) / "tools/validate_generate_contract.py"
    if proof.get("validatorSha256") != hashlib.sha256(validator.read_bytes()).hexdigest():
        raise ValueError("Generate validator changed since the recorded check")
    # Import only for the v0.6.0 contract path; legacy evidence keeps its historical rules.
    try:
        from tools.validate_generate_contract import source_paths
    except ModuleNotFoundError:
        from validate_generate_contract import source_paths
    files = proof.get("sourceFiles")
    required = set(source_paths()) - {"client/user-front/src/utils/assetUploadValidation.ts"}
    if not isinstance(files, dict) or not required <= set(files) or set(files) - set(source_paths()):
        raise ValueError("Generate evidence must identify every validated backend source")
    if any(not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None for value in files.values()):
        raise ValueError("Generate evidence has invalid source hashes")

def candidate_fingerprint(root):
    """Bind acceptance to all shipped sources and the release/validation machinery."""
    root = Path(root)
    manifest = json.loads((root / "runtime-files.json").read_text())
    paths = {row["source"] for row in manifest["files"]}
    paths.update({".gitattributes", "runtime-files.json", "install.sh", "install.ps1", ".github/workflows/ci.yml",
                  "tools/validate_generate_contract.py", "tools/verify_release_acceptance.py",
                  "tools/refresh_installer_hashes.py"})
    # The acceptance must also become stale if its test runner or fixtures
    # change. Keep the acceptance record itself outside its own fingerprint.
    paths.update(path.relative_to(root).as_posix()
                 for folder in ("tools", "tests", ".github/workflows")
                 for path in (root / folder).rglob("*")
                 if path.is_file() and path.suffix in {".py", ".ps1", ".json", ".txt", ".yml", ".yaml"}
                 and "__pycache__" not in path.parts)
    package = {path.relative_to(root).as_posix() for path in (root / "holycrab/scripts/holycrab_runtime").glob("*.py")}
    if not package or not package.issubset(paths):
        raise ValueError("Candidate runtime inventory is incomplete")
    hashes = {}
    for name in sorted(paths):
        path = root / name
        if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
            raise ValueError("Unsafe candidate file path")
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate(record, tag, *, root=None, contract_only=False):
    if not isinstance(record, dict) or record.get("version") != tag:
        raise ValueError("Manual acceptance version must match the release tag")
    if tag in LOCAL_EVIDENCE_TAGS and root is not None:
        validate_contract(record, root)
    if tag == "v0.6.0" and root is not None:
        validate_candidate_contract(record, root)
    if contract_only:
        if (tag not in LOCAL_EVIDENCE_TAGS and tag != "v0.6.0") or root is None:
            raise ValueError("Local contract mode requires an explicitly authorized release version")
        return
    if tag not in LOCAL_EVIDENCE_TAGS:
        if root is None or json.loads((Path(root) / "runtime-files.json").read_text()).get("version") != tag.removeprefix("v"):
            raise ValueError("Candidate version does not match the acceptance tag")
        if root is None or record.get("candidateSha256") != candidate_fingerprint(root):
            raise ValueError("Fresh acceptance must match this exact candidate; historical waivers do not apply")
    flow = record.get("realHumanCoreFlow")
    if not isinstance(flow, dict):
        raise ValueError("Actual real-person authorization and upload acceptance is still pending; do not release")
    if tag == LOCAL_EVIDENCE_TAG and flow.get("status") == "previously-accepted" and flow.get("retestWaived") is True:
        if not all(isinstance(flow.get(k), str) and flow[k].strip() for k in ("operator", "confirmedAt", "notes")):
            raise ValueError("Reused acceptance requires explicit operator confirmation")
        datetime.fromisoformat(flow["confirmedAt"].replace("Z", "+00:00"))
        return
    if tag == REUSED_CORE_TAG and flow.get("status") == "previously-accepted" and flow.get("retestWaived") is True:
        if not all(isinstance(flow.get(k), str) and flow[k].strip() for k in ("operator", "confirmedAt", "notes")):
            raise ValueError("Reused acceptance requires explicit operator confirmation")
        datetime.fromisoformat(flow["confirmedAt"].replace("Z", "+00:00"))
        validate_unchanged_core(flow, root)
        return
    if flow.get("status") != "passed":
        raise ValueError("Actual real-person authorization and upload acceptance is still pending; do not release")
    if not all(isinstance(flow.get(k), str) and flow[k].strip() for k in ("operator", "testedAt", "notes")):
        raise ValueError("Manual acceptance requires an operator, test time and non-sensitive outcome notes")
    datetime.fromisoformat(flow["testedAt"].replace("Z", "+00:00"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("--record", type=Path)
    parser.add_argument("--contract-only", action="store_true", help="Verify synchronized local main evidence for an explicitly authorized release")
    parser.add_argument("--fingerprint", action="store_true", help="Print the current candidate fingerprint; does not approve acceptance")
    args = parser.parse_args()
    if args.fingerprint:
        print(candidate_fingerprint(Path(__file__).resolve().parents[1]))
        return
    path = args.record or Path(__file__).resolve().parents[1] / f"docs/release-acceptance/{args.tag}.json"
    try:
        validate(json.loads(path.read_text(encoding="utf-8")), args.tag,
                 root=Path(__file__).resolve().parents[1], contract_only=args.contract_only)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        parser.exit(1, str(error) + "\n")
    print(("Recorded local Generate contract verified for " if args.contract_only else "Release acceptance verified for ") + args.tag)


if __name__ == "__main__":
    main()
