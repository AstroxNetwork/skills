#!/usr/bin/env python3
"""Fail-closed live smoke checks for HolyCrab CLI.

This internal test driver may read production data and call credit-estimation
endpoints. It cannot create generation tasks, start real-human authorization,
upload files, or mutate real-human data.
"""

from __future__ import annotations

import argparse
import collections
import getpass
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


CLI_SCRIPT = Path(__file__).parents[1] / "scripts" / "holycrab_cli.py"
CLI_SPEC = importlib.util.spec_from_file_location("holycrab_safe_live_cli", CLI_SCRIPT)
assert CLI_SPEC and CLI_SPEC.loader
cli = importlib.util.module_from_spec(CLI_SPEC)
CLI_SPEC.loader.exec_module(cli)


FREEZE_CREDIT_ROUTES = frozenset(route[0] for route in cli.GENERATION_ROUTES.values())
GENERATION_CREATE_ROUTES = frozenset(route[1] for route in cli.GENERATION_ROUTES.values())
SAFE_MCP_TOOLS = frozenset(
    {
        "cli_status",
        "account_get",
        "capabilities_list",
        "capability_get",
        "generation_estimate",
        "generation_get",
        "generation_list",
        "generation_attempt_list",
        "generation_attempt_get",
        "real_human_groups_list",
        "real_human_assets_list",
        "asset_get",
        "asset_upload_prepare",
        "asset_upload",
    }
)
SENSITIVE_REPORT_KEYS = {
    "apikey",
    "authorization",
    "authorizationid",
    "groupid",
    "groupuniqid",
    "name",
    "nickname",
    "output",
    "path",
    "privateurl",
    "taskid",
    "uniqid",
    "url",
    "username",
}
ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/][^\s]+|/(?:Users|home|tmp|private|var|etc|opt|root|Volumes)/[^\s]+)"
)
URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)


class SafetyViolation(RuntimeError):
    """Raised before a live request that is outside the smoke-test allowlist."""

    def __init__(self, message: str, *, generation_create: bool = False) -> None:
        super().__init__(message)
        self.generation_create = generation_create


def normalized_route(path: str) -> str | None:
    if path in {"/api/user/me", "/api/tasks", "/api/real-human-groups"}:
        return path
    if re.fullmatch(r"/api/tasks/[A-Za-z0-9]{1,64}", path):
        return "/api/tasks/{id}"
    if re.fullmatch(r"/api/real-human-groups/[A-Za-z0-9]{1,64}/assets", path):
        return "/api/real-human-groups/{id}/assets"
    if re.fullmatch(r"/api/user-assets/[A-Za-z0-9]{1,64}", path):
        return "/api/user-assets/{id}"
    if path in FREEZE_CREDIT_ROUTES:
        return path
    return None


class SafeRequestGuard:
    def __init__(self, delegate: Callable[..., tuple[int, Any]]) -> None:
        self.delegate = delegate
        self.counts: collections.Counter[tuple[str, str]] = collections.Counter()
        self.blocked_calls = 0
        self.generation_create_calls = 0

    def __call__(self, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        resolved_method = method.upper()
        if resolved_method == "POST" and path in GENERATION_CREATE_ROUTES:
            self.generation_create_calls += 1
            self.blocked_calls += 1
            raise SafetyViolation(
                "Generation task creation is disabled by the live smoke-test guard",
                generation_create=True,
            )
        route = normalized_route(path)
        allowed = route is not None and (
            resolved_method == "GET" or (resolved_method == "POST" and path in FREEZE_CREDIT_ROUTES)
        )
        if not allowed:
            self.blocked_calls += 1
            raise SafetyViolation(f"Live request blocked by safety allowlist: {resolved_method} {route or '[redacted]'}")
        self.counts[(resolved_method, route)] += 1
        return self.delegate(resolved_method, path, **kwargs)

    def summary(self) -> dict[str, Any]:
        requests = [
            {"method": method, "route": route, "count": count}
            for (method, route), count in sorted(self.counts.items())
        ]
        return {
            "requests": requests,
            "generationCreateCalls": self.generation_create_calls,
            "blockedCalls": self.blocked_calls,
        }


class SafeMcpGuard:
    def __init__(self, delegate: Callable[[str, dict[str, Any]], Any]) -> None:
        self.delegate = delegate
        self.counts: collections.Counter[str] = collections.Counter()

    def __call__(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in SAFE_MCP_TOOLS:
            raise SafetyViolation(
                f"MCP tool blocked by safety allowlist: {name}",
                generation_create=name == "generation_create",
            )
        result = self.delegate(name, arguments)
        self.counts[name] += 1
        return result

    def summary(self) -> list[dict[str, Any]]:
        return [{"tool": name, "count": count} for name, count in sorted(self.counts.items())]


def sanitize_report(value: Any, *, secrets: set[str] | None = None, key: str = "") -> Any:
    secret_values = {item for item in (secrets or set()) if item}
    normalized_key = re.sub(r"[^a-z0-9]", "", key.lower())
    if normalized_key in SENSITIVE_REPORT_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): sanitize_report(item, secrets=secret_values, key=str(item_key))
                for item_key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_report(item, secrets=secret_values) for item in value]
    if isinstance(value, str):
        if any(secret in value for secret in secret_values):
            return "[REDACTED]"
        cleaned = URL_PATTERN.sub("[REDACTED_URL]", value)
        cleaned = ABSOLUTE_PATH_PATTERN.sub("[REDACTED_PATH]", cleaned)
        return cleaned
    return value


def write_private_report(path: Path, value: dict[str, Any]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def capture_cli(argv: list[str], *, stdin_text: str | None = None) -> dict[str, Any]:
    parser = cli.build_parser()
    stdout = io.StringIO()
    stderr = io.StringIO()
    original_stdin = sys.stdin
    if stdin_text is not None:
        sys.stdin = io.StringIO(stdin_text)
    try:
        try:
            args = parser.parse_args(argv)
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = args.func(args)
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 1
            if error.code not in (None, 0):
                print(str(error), file=stderr)
        except Exception as error:  # The report records failures; safety violations still abort.
            if isinstance(error, SafetyViolation):
                raise
            code = 1
            print(f"{error.__class__.__name__}: {error}", file=stderr)
    finally:
        sys.stdin = original_stdin
    return {"code": int(code or 0), "stdout": stdout.getvalue(), "stderr": stderr.getvalue()}


def assert_invalid_request_stays_local(guard: SafeRequestGuard) -> None:
    before = guard.summary()
    original_send = cli.send
    cli.send = guard
    try:
        try:
            cli.estimate_generation(
                "image",
                {"model": "seedance-2-0", "prompt": "must fail locally", "size": "2K"},
            )
        except SystemExit:
            pass
        else:
            raise AssertionError("An invalid cross-kind model request was accepted")
    finally:
        cli.send = original_send
    if guard.summary() != before:
        raise AssertionError("Invalid generation input reached the network guard")


def _git_commit() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parents[2], text=True,
            capture_output=True, check=False,
        )
        value = completed.stdout.strip()
        return value if completed.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value) else None
    except OSError:
        return None


def _response_records(status: int, response: Any) -> list[dict[str, Any]]:
    data = cli.response_data(status, response)
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return [item for item in data["records"] if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _task_identifier(task: dict[str, Any]) -> str | None:
    value = task.get("uniqId") or task.get("taskId")
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9]{1,64}", value) else None


def _safe_download_candidate(tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    for task in tasks:
        task_type = str(task.get("taskType", "")).upper()
        if task.get("step") == 2 and task_type in {"IMAGE", "AUDIO"} and cli.task_output_urls(task):
            return task
    return None


def _mcp_call(name: str, arguments: dict[str, Any]) -> Any:
    response = cli.mcp_dispatch(
        {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    if not isinstance(response, dict) or "result" not in response:
        raise AssertionError(f"MCP {name} returned no result")
    result = response["result"]
    if isinstance(result, dict) and result.get("isError"):
        raise AssertionError(f"MCP {name} returned an error")
    return result


def run_safe_live_suite(api_key: str, *, account_label: str = "user1",
                        progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    if not api_key.strip():
        raise ValueError("API Key cannot be empty")
    started = datetime.now(timezone.utc)
    checks: list[dict[str, Any]] = []
    secrets = {api_key.strip()}

    def add_check(check: str, operation: Callable[[], Any], *, allowed_codes: set[int] | None = None) -> Any:
        if progress:
            progress(f"Checking: {check}...")
        try:
            result = operation()
            if isinstance(result, dict) and "code" in result:
                allowed = allowed_codes or {0}
                if result["code"] not in allowed:
                    raise AssertionError(f"command exited with code {result['code']}")
            checks.append({"check": check, "status": "passed"})
            if progress:
                progress(f"Passed: {check}")
            return result
        except SafetyViolation:
            checks.append({"check": check, "status": "failed", "reason": "safety guard blocked a request"})
            raise
        except Exception as error:
            reason = str(error) if isinstance(error, AssertionError) else error.__class__.__name__
            checks.append({"check": check, "status": "failed",
                           "reason": reason})
            if progress:
                progress(f"Failed: {check}; details are in the sanitized report.")
            return None

    def not_applicable(check: str, reason: str) -> None:
        checks.append({"check": check, "status": "not_applicable", "reason": reason})

    old_environment = {name: os.environ.get(name) for name in (
        "HOLYCRAB_CONFIG_DIR", "HOLYCRAB_API_KEY", "HOLYCRAB_BASE_URL", "HOLYCRAB_NO_UPDATE_CHECK"
    )}
    original_send = cli.send
    original_mcp_call = cli.mcp_tool_call
    original_upload = cli.open_presigned_upload
    temporary_path: Path | None = None
    request_guard = SafeRequestGuard(original_send)
    mcp_guard = SafeMcpGuard(original_mcp_call)
    try:
        with tempfile.TemporaryDirectory(prefix="holycrab-safe-live-") as directory:
            temporary_path = Path(directory)
            os.environ["HOLYCRAB_CONFIG_DIR"] = directory
            os.environ["HOLYCRAB_NO_UPDATE_CHECK"] = "1"
            os.environ.pop("HOLYCRAB_API_KEY", None)
            os.environ.pop("HOLYCRAB_BASE_URL", None)
            cli.SENSITIVE_OUTPUT_VALUES.add(api_key.strip())
            cli.send = request_guard
            cli.mcp_tool_call = mcp_guard

            def block_upload(*args: Any, **kwargs: Any) -> Any:
                raise SafetyViolation("Presigned upload is disabled by the live smoke-test guard")

            cli.open_presigned_upload = block_upload

            add_check("setup", lambda: capture_cli(["setup", "--stdin"], stdin_text=api_key + "\n"))
            add_check("auth status", lambda: capture_cli(["auth", "status"]))
            add_check("models list", lambda: capture_cli(["models", "list", "--json"]))
            for model in ("dreamina-seedance-2-5-260628", "seedream-5-0-lite-260128", "seed-audio-1.0"):
                add_check(f"models show {model}", lambda model=model: capture_cli(["models", "show", model]))
            add_check("credits balance", lambda: capture_cli(["credits", "balance"]))
            add_check("invalid request stays local", lambda: assert_invalid_request_stays_local(request_guard))

            raw_tasks: list[dict[str, Any]] = []
            before_listing = add_check(
                "task snapshot before",
                lambda: request_guard("GET", "/api/tasks", query=[("page", "1"), ("pageSize", "100")]),
            )
            if isinstance(before_listing, tuple) and len(before_listing) == 2:
                try:
                    raw_tasks = _response_records(before_listing[0], before_listing[1])
                    for item in raw_tasks:
                        identifier = _task_identifier(item)
                        if identifier:
                            secrets.add(identifier)
                except SystemExit:
                    checks.append({"check": "task snapshot before", "status": "failed",
                                   "reason": "API response validation failed"})

            estimates = {
                "video": {
                    "model": "dreamina-seedance-2-5-260628", "prompt": "Safe smoke-test estimate only",
                    "duration": 4, "resolution": "480p", "ratio": "16:9", "videoTaskType": "reference",
                },
                "image": {
                    "model": "seedream-5-0-lite-260128", "prompt": "Safe smoke-test estimate only",
                    "size": "2K",
                },
                "audio": {
                    "textPrompt": "Safe smoke-test estimate only.",
                    "audioConfig": {"format": "mp3", "sample_rate": 24000},
                },
            }
            for kind, request in estimates.items():
                add_check(
                    f"{kind} estimate",
                    lambda kind=kind, request=request: capture_cli(
                        ["generate", "estimate", "--kind", kind, "--json", json.dumps(request)]
                    ),
                )

            add_check("tasks list", lambda: capture_cli(["tasks", "list", "--page-size", "20"]))
            end = datetime.now(timezone.utc).date()
            start = end - timedelta(days=30)
            for task_type in ("IMAGE", "VIDEO", "AUDIO", "TEXT"):
                add_check(
                    f"tasks filter {task_type}",
                    lambda task_type=task_type: capture_cli(
                        ["tasks", "list", "--page-size", "5", "--start-date", start.isoformat(),
                         "--end-date", end.isoformat(), "--type", task_type]
                    ),
                )
            add_check("real-human groups list (read-only)",
                      lambda: capture_cli(["real-human", "groups", "list", "--page-size", "1"]))
            add_check("doctor online", lambda: capture_cli(["doctor", "--json", "--online"]))
            add_check("update check", lambda: capture_cli(["update", "--check"]))

            handshake = add_check(
                "MCP initialize",
                lambda: cli.mcp_dispatch({
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": cli.LATEST_INITIALIZE_PROTOCOL, "capabilities": {},
                               "clientInfo": {"name": "safe-live-smoke", "version": "1"}},
                }),
            )
            if not isinstance(handshake, dict) or "result" not in handshake:
                checks[-1] = {"check": "MCP initialize", "status": "failed", "reason": "missing result"}
            tools = add_check(
                "MCP tools/list",
                lambda: cli.mcp_dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
            )
            if not isinstance(tools, dict) or "result" not in tools:
                checks[-1] = {"check": "MCP tools/list", "status": "failed", "reason": "missing result"}
            add_check("MCP cli status", lambda: _mcp_call("cli_status", {}))
            add_check("MCP account", lambda: _mcp_call("account_get", {}))
            add_check("MCP capabilities", lambda: _mcp_call("capabilities_list", {}))
            add_check("MCP capability", lambda: _mcp_call("capability_get", {"model": "seedream-5-0-lite-260128"}))
            add_check("MCP estimate", lambda: _mcp_call("generation_estimate", {
                "kind": "image", "request": estimates["image"],
            }))
            add_check("MCP tasks list", lambda: _mcp_call("generation_list", {"page": 1, "pageSize": 5}))

            fixture = temporary_path / "safe-fixture.jpg"
            fixture.write_bytes(b"\xff\xd8\xff\xe0safe-live-smoke")
            add_check("MCP upload prepare (local only)",
                      lambda: _mcp_call("asset_upload_prepare", {"files": [str(fixture)]}))

            task = next((item for item in raw_tasks if _task_identifier(item)), None)
            if task is None:
                not_applicable("task get/wait", "No existing task was available; no task was created for testing")
            else:
                task_id = _task_identifier(task)
                assert task_id is not None
                add_check("task get", lambda: capture_cli(["tasks", "get", task_id]))
                add_check("task wait", lambda: capture_cli(
                    ["tasks", "wait", task_id, "--timeout", "0", "--interval", "1"]
                ), allowed_codes={0, 1, 2})
                add_check("MCP task get", lambda: _mcp_call("generation_get", {"taskId": task_id}))

            downloadable = _safe_download_candidate(raw_tasks)
            if downloadable is None:
                not_applicable("download", "No completed image or audio task with a downloadable result was available")
            else:
                task_id = _task_identifier(downloadable)
                assert task_id is not None
                destination = temporary_path / "downloaded-result"
                add_check("download", lambda: capture_cli(
                    ["download", task_id, "--output", str(destination)]
                ))
                existing = capture_cli(["download", task_id, "--output", str(destination)])
                if existing["code"] == 0:
                    checks.append({"check": "download existing-file protection", "status": "failed",
                                   "reason": "existing output was overwritten without --force"})
                else:
                    checks.append({"check": "download existing-file protection", "status": "passed"})
                add_check("download force", lambda: capture_cli(
                    ["download", task_id, "--output", str(destination), "--force"]
                ))

            after_listing = add_check(
                "task snapshot after",
                lambda: request_guard("GET", "/api/tasks", query=[("page", "1"), ("pageSize", "100")]),
            )
            if isinstance(after_listing, tuple) and len(after_listing) == 2:
                try:
                    after_tasks = _response_records(after_listing[0], after_listing[1])
                    before_ids = {identifier for item in raw_tasks if (identifier := _task_identifier(item))}
                    after_ids = {identifier for item in after_tasks if (identifier := _task_identifier(item))}
                    secrets.update(after_ids)
                    checks.append({
                        "check": "task snapshot comparison",
                        "status": "passed",
                        "observation": "unchanged" if before_ids == after_ids else "changed by concurrent account activity",
                    })
                except SystemExit:
                    checks.append({"check": "task snapshot comparison", "status": "failed",
                                   "reason": "API response validation failed"})

            add_check("auth clear-key", lambda: capture_cli(["auth", "clear-key"]))
    finally:
        cli.send = original_send
        cli.mcp_tool_call = original_mcp_call
        cli.open_presigned_upload = original_upload
        cli.SENSITIVE_OUTPUT_VALUES.discard(api_key.strip())
        for name, value in old_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    safety = request_guard.summary()
    safety["mcpTools"] = mcp_guard.summary()
    cleanup_ok = temporary_path is not None and not temporary_path.exists()
    checks.append({"check": "temporary state cleanup", "status": "passed" if cleanup_ok else "failed"})
    ok = all(row["status"] in {"passed", "not_applicable"} for row in checks)
    ok = ok and safety["generationCreateCalls"] == 0 and safety["blockedCalls"] == 0
    report = {
        "ok": ok,
        "accountLabel": account_label,
        "version": cli.VERSION,
        "commit": _git_commit(),
        "startedAt": started.isoformat(),
        "finishedAt": datetime.now(timezone.utc).isoformat(),
        "platform": {"system": cli.platform.system(), "python": cli.platform.python_version()},
        "checks": checks,
        "safety": safety,
        "onlineDataCreated": False,
        "manualCoverage": ["real-human authorization", "real-human asset upload"],
    }
    return sanitize_report(report, secrets=secrets)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run fail-closed HolyCrab production smoke checks without creating tasks or uploading files"
    )
    parser.add_argument("--report", type=Path, required=True, help="Write a sanitized JSON report to this path")
    parser.add_argument("--account-label", default="user1", help="Non-secret label included in the report")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if os.environ.get("HOLYCRAB_API_KEY"):
        print("Refusing to run while HOLYCRAB_API_KEY is set; use the hidden prompt instead.", file=sys.stderr)
        return 2
    try:
        api_key = getpass.getpass(f"Paste the {args.account_label} API Key (input hidden): ").strip()
    except KeyboardInterrupt:
        print("Stopped; no checks were started.", file=sys.stderr)
        return 130
    if not api_key:
        print("API Key cannot be empty.", file=sys.stderr)
        return 2
    print(
        "API Key received. Running safe checks; no generation tasks or uploads will be created.",
        flush=True,
    )
    try:
        report = run_safe_live_suite(api_key, account_label=args.account_label,
                                     progress=lambda message: print(message, file=sys.stderr, flush=True))
    except KeyboardInterrupt:
        print("Stopped. Temporary configuration was cleaned; no generation or upload was submitted.", file=sys.stderr)
        return 130
    except SafetyViolation as error:
        report = {
            "ok": False,
            "accountLabel": args.account_label,
            "checks": [{"check": "safety boundary", "status": "failed", "reason": str(error)}],
            "safety": {"generationCreateCalls": 1 if error.generation_create else 0, "blockedCalls": 1},
            "onlineDataCreated": False,
        }
    safe_report = sanitize_report(report, secrets={api_key})
    try:
        write_private_report(args.report, safe_report)
    except KeyboardInterrupt:
        print("Stopped while saving the report. Checks already ended; no generation or upload was submitted. Check whether the report file exists.", file=sys.stderr)
        return 130
    except (OSError, ValueError):
        print("Checks ended, but the sanitized report could not be saved. Choose a writable report location and contact the test operator.", file=sys.stderr)
        return 1
    checks = safe_report.get("checks", [])
    passed = sum(1 for row in checks if isinstance(row, dict) and row.get("status") == "passed")
    failed = sum(1 for row in checks if isinstance(row, dict) and row.get("status") == "failed")
    not_applicable = sum(
        1 for row in checks if isinstance(row, dict) and row.get("status") == "not_applicable"
    )
    safety = safe_report.get("safety", {})
    generation_calls = safety.get("generationCreateCalls", "unknown") if isinstance(safety, dict) else "unknown"
    print(f"Completed: {passed} passed, {failed} failed, {not_applicable} not applicable.")
    print(
        f"Safety: generation create calls: {generation_calls}; "
        f"online data created: {'yes' if safe_report.get('onlineDataCreated') else 'no'}."
    )
    print(f"Sanitized report: {args.report.expanduser().resolve()}")
    print("No further terminal action is required. Ask the test operator to review the sanitized report.")
    return 0 if safe_report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
