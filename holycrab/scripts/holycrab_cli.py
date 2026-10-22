#!/usr/bin/env python3
"""Stable bootstrap: existing launchers and MCP registrations keep this path."""
from pathlib import Path
import sys

_root = str(Path(__file__).resolve().parent)
if _root not in sys.path:
    sys.path.insert(0, _root)
import holycrab_runtime


def _owner(name):
    return holycrab_runtime.owner(name)


def __getattr__(name):
    return getattr(holycrab_runtime, name)


if __name__ == "__main__":
    from holycrab_runtime.cli import main
    raise SystemExit(main())
