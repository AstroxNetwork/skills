#!/usr/bin/env python3
"""Fail closed until an actual operator records the required manual acceptance."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


def validate(record, tag):
    if not isinstance(record, dict) or record.get("version") != tag:
        raise ValueError("Manual acceptance version must match the release tag")
    flow = record.get("realHumanCoreFlow")
    if not isinstance(flow, dict) or flow.get("status") != "passed":
        raise ValueError("Actual real-person authorization and upload acceptance is still pending; do not release")
    if not all(isinstance(flow.get(k), str) and flow[k].strip() for k in ("operator", "testedAt", "notes")):
        raise ValueError("Manual acceptance requires an operator, test time and non-sensitive outcome notes")
    datetime.fromisoformat(flow["testedAt"].replace("Z", "+00:00"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("--record", type=Path)
    args = parser.parse_args()
    path = args.record or Path(__file__).resolve().parents[1] / f"docs/release-acceptance/{args.tag}.json"
    try:
        validate(json.loads(path.read_text(encoding="utf-8")), args.tag)
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")
    print("Required manual acceptance is recorded for " + args.tag)


if __name__ == "__main__":
    main()
