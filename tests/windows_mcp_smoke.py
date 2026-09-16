#!/usr/bin/env python3
"""Exercise the installed MCP stdio entry point from Windows CI."""

from __future__ import annotations

import json
import subprocess
import sys


request = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "ci", "version": "1"},
    },
}
completed = subprocess.run(
    [sys.executable, sys.argv[1], "mcp", "serve"],
    input=json.dumps(request) + "\n",
    text=True,
    capture_output=True,
    check=False,
)
if completed.returncode != 0:
    raise SystemExit(completed.stderr or f"MCP exited {completed.returncode}")

try:
    response = json.loads(completed.stdout)
except json.JSONDecodeError as exc:
    raise SystemExit(f"Invalid MCP response: {completed.stdout!r}") from exc

if response.get("result", {}).get("serverInfo", {}).get("name") != "holycrab-local":
    raise SystemExit(f"Unexpected MCP handshake: {completed.stdout}")

print("Windows MCP handshake succeeded.")
