#!/usr/bin/env python3
"""Exercise installed MCP framing, discovery, local calls and every tool's validation.

No real account is used. Invalid arguments are deliberately sent to every advertised
operation, including write tools; they must fail before any network access.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def run_smoke(cli_path: str) -> dict:
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "native-offline-ci", "version": "2"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
    ]
    with tempfile.TemporaryDirectory(prefix="holycrab-mcp-native-") as temp:
        env = dict(os.environ, HOLYCRAB_CONFIG_DIR=temp, HOLYCRAB_NO_UPDATE_CHECK="1")
        env.pop("HOLYCRAB_API_KEY", None)
        env.pop("HOLYCRAB_API_BASE", None)
        env.pop("HOLYCRAB_BASE_URL", None)

        def exchange(batch):
            completed = subprocess.run(
                [sys.executable, "-X", "utf8", cli_path, "mcp", "serve"],
                input="".join(json.dumps(row, ensure_ascii=False) + "\n" for row in batch),
                encoding="utf-8", capture_output=True, env=env, timeout=30,
            )
            if completed.returncode:
                raise AssertionError(f"MCP exited {completed.returncode}: {completed.stderr}")
            rows = [json.loads(line) for line in completed.stdout.splitlines()]
            expected_ids = [row["id"] for row in batch if "id" in row]
            if [row.get("id") for row in rows] != expected_ids:
                raise AssertionError("MCP emitted extra output, lost requests, or answered a notification")
            return rows

        replies = exchange(requests)
        assert replies[0]["result"]["serverInfo"]["name"] == "holycrab-local"
        tools = replies[1]["result"]["tools"]
        names = [row["name"] for row in tools]
        assert len(names) == len(set(names)) == 22, names
        assert replies[2]["result"] == {}
        calls = []
        for name in names:
            calls.append({"jsonrpc": "2.0", "id": len(calls) + 10, "method": "tools/call",
                          "params": {"name": name, "arguments": {"unexpected_中文": True}}})
        for reply in exchange(calls):
            assert reply["result"]["isError"] is True, reply
            assert "Unknown tool argument" in reply["result"]["content"][0]["text"], reply
        local = [
            ("cli_status", {}), ("capabilities_list", {}),
            ("capability_get", {"model": "berry-1.0-pro-turbo"}),
            ("generation_attempt_list", {}),
        ]
        rows = exchange([{"jsonrpc": "2.0", "id": i + 50, "method": "tools/call",
                          "params": {"name": name, "arguments": args}}
                         for i, (name, args) in enumerate(local)])
        for row in rows:
            assert row["result"]["isError"] is False, row
            assert isinstance(row["result"]["structuredContent"], dict)
            assert row["result"]["content"][0]["type"] == "text"
        return {"handshake": True, "toolCount": len(names), "invalidCalls": len(calls),
                "successfulLocalCalls": len(rows), "notificationReplies": 0}


if __name__ == "__main__":
    print(json.dumps(run_smoke(str(Path(sys.argv[1]).resolve())), ensure_ascii=False))
