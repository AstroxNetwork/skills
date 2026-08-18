from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "holycrab_cli.py"
SPEC = importlib.util.spec_from_file_location("holycrab_mcp", SCRIPT_PATH)
assert SPEC and SPEC.loader
holycrab = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(holycrab)


class McpProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(
            os.environ,
            {
                "HOLYCRAB_CONFIG_DIR": self.temp.name,
                "HOLYCRAB_API_KEY": "test-secret",
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_initialize_returns_server_capabilities(self) -> None:
        response = holycrab.mcp_dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
            }
        )
        self.assertEqual(response["id"], 1)
        self.assertEqual(response["result"]["serverInfo"]["name"], "holycrab-local")
        self.assertEqual(response["result"]["protocolVersion"], "2025-11-25")
        self.assertIn("tools", response["result"]["capabilities"])

    def test_initialize_falls_back_for_unknown_protocol(self) -> None:
        response = holycrab.mcp_dispatch(
            {"jsonrpc": "2.0", "id": 9, "method": "initialize", "params": {"protocolVersion": "unknown"}}
        )
        self.assertEqual(response["result"]["protocolVersion"], "2025-11-25")

    def test_initialize_rejects_non_object_params_without_raising(self) -> None:
        response = holycrab.mcp_dispatch(
            {"jsonrpc": "2.0", "id": 10, "method": "initialize", "params": "bad"}
        )
        self.assertEqual(response["error"]["code"], -32602)

    def test_tools_list_is_small_and_has_no_raw_request_tool(self) -> None:
        response = holycrab.mcp_dispatch(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertIn("generation_create", names)
        self.assertIn("generation_get", names)
        self.assertNotIn("request", names)
        self.assertLessEqual(len(names), 9)

    def test_generation_create_without_confirmation_only_returns_estimate(self) -> None:
        params = {
            "name": "generation_create",
            "arguments": {
                "kind": "image",
                "request": {
                    "model": "seedream-5-0-lite-260128",
                    "prompt": "a crab",
                    "size": "2k",
                },
                "confirmed": False,
                "attemptId": "quote-attempt",
            },
        }
        with patch.object(
            holycrab,
            "send",
            return_value=(200, {"code": 0, "data": {"frozenCredit": 5}}),
        ) as send:
            response = holycrab.mcp_dispatch(
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": params}
            )
        self.assertFalse(response["result"]["isError"])
        self.assertIn("confirmationRequired", response["result"]["structuredContent"])
        self.assertEqual(send.call_count, 1)

    def test_confirmed_generation_requires_stable_attempt_id(self) -> None:
        arguments = {
            "kind": "image",
            "request": {"model": "seedream-5-0-lite-260128", "prompt": "a crab", "size": "2k"},
            "confirmed": True,
        }
        with patch.object(holycrab, "send") as send:
            response = holycrab.mcp_dispatch(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "generation_create", "arguments": arguments},
                }
            )
        self.assertTrue(response["result"]["isError"])
        self.assertIn("attemptId is required", response["result"]["content"][0]["text"])
        send.assert_not_called()

    def test_same_mcp_attempt_id_cannot_create_twice(self) -> None:
        arguments = {
            "kind": "image",
            "request": {"model": "seedream-5-0-lite-260128", "prompt": "a crab", "size": "2k"},
            "confirmed": True,
            "attemptId": "mcp-attempt-1",
        }
        replies = [
            (200, {"code": 0, "data": {"frozenCredit": 5}}),
            (200, {"code": 0, "data": {"uniqId": "task-1"}}),
        ]
        with patch.object(holycrab, "send", side_effect=replies) as send:
            first = holycrab.mcp_dispatch(
                {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "generation_create", "arguments": arguments}}
            )
            second = holycrab.mcp_dispatch(
                {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "generation_create", "arguments": arguments}}
            )
        self.assertFalse(first["result"]["isError"])
        self.assertTrue(second["result"]["isError"])
        self.assertEqual(send.call_count, 2)

    def test_generation_create_schema_requires_attempt_id(self) -> None:
        response = holycrab.mcp_dispatch(
            {"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}}
        )
        tool = next(item for item in response["result"]["tools"] if item["name"] == "generation_create")
        self.assertIn("attemptId", tool["inputSchema"]["required"])

    def test_malformed_tool_arguments_return_error_without_raising(self) -> None:
        response = holycrab.mcp_dispatch(
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {"name": "generation_estimate", "arguments": {"kind": "video", "request": "bad"}},
            }
        )
        self.assertTrue(response["result"]["isError"])
        self.assertIn("request must be an object", response["result"]["content"][0]["text"])

    def test_mcp_task_result_omits_private_and_internal_backend_fields(self) -> None:
        backend = {
            "code": 0,
            "data": {
                "uniqId": "task-1",
                "step": 2,
                "imageUrls": ["https://cdn.example/result.jpg"],
                "provider": "internal-provider",
                "request": {"prompt": "private prompt"},
            },
        }
        with patch.object(holycrab, "send", return_value=(200, backend)):
            response = holycrab.mcp_dispatch(
                {
                    "jsonrpc": "2.0",
                    "id": 9,
                    "method": "tools/call",
                    "params": {"name": "generation_get", "arguments": {"taskId": "task-1"}},
                }
            )
        content = response["result"]["structuredContent"]
        self.assertEqual(content["uniqId"], "task-1")
        self.assertNotIn("provider", content)
        self.assertNotIn("request", content)


if __name__ == "__main__":
    unittest.main()
