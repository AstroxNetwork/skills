#!/usr/bin/env python3
"""Verify release acceptance; local/reused evidence is authorized only for v0.4.3."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

LOCAL_EVIDENCE_TAG = "v0.4.3"
CONTRACT_FILES = ("holycrab/scripts/holycrab_cli.py", "holycrab/references/capabilities.json",
                  "tools/validate_generate_contract.py")


def validate_contract(record, root):
    if record.get("version") != LOCAL_EVIDENCE_TAG:
        raise ValueError("Local contract evidence is authorized only for v0.4.3")
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


def validate(record, tag, *, root=None, contract_only=False):
    if not isinstance(record, dict) or record.get("version") != tag:
        raise ValueError("Manual acceptance version must match the release tag")
    if tag == LOCAL_EVIDENCE_TAG and root is not None:
        validate_contract(record, root)
    if contract_only:
        if tag != LOCAL_EVIDENCE_TAG or root is None:
            raise ValueError("Local contract mode is authorized only for v0.4.3")
        return
    flow = record.get("realHumanCoreFlow")
    if not isinstance(flow, dict):
        raise ValueError("Actual real-person authorization and upload acceptance is still pending; do not release")
    if tag == LOCAL_EVIDENCE_TAG and flow.get("status") == "previously-accepted" and flow.get("retestWaived") is True:
        if not all(isinstance(flow.get(k), str) and flow[k].strip() for k in ("operator", "confirmedAt", "notes")):
            raise ValueError("Reused acceptance requires explicit operator confirmation")
        datetime.fromisoformat(flow["confirmedAt"].replace("Z", "+00:00"))
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
    parser.add_argument("--contract-only", action="store_true", help="Verify the v0.4.3 synchronized local main evidence")
    args = parser.parse_args()
    path = args.record or Path(__file__).resolve().parents[1] / f"docs/release-acceptance/{args.tag}.json"
    try:
        validate(json.loads(path.read_text(encoding="utf-8")), args.tag,
                 root=Path(__file__).resolve().parents[1], contract_only=args.contract_only)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        parser.exit(1, str(error) + "\n")
    print(("Recorded local Generate contract verified for " if args.contract_only else "Release acceptance verified for ") + args.tag)


if __name__ == "__main__":
    main()
