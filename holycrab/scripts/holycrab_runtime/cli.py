"""HolyCrab cli responsibilities."""
from __future__ import annotations

import argparse
import getpass
import http.client
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from . import capabilities as _capabilities
from . import generation as _generation
from . import lifecycle as _lifecycle
from . import mcp as _mcp
from . import presentation as _presentation
from . import real_human as _real_human
from . import results as _results
from . import settings as _settings
from . import state as _state
from . import tasks as _tasks
from . import transport as _transport
from . import uploads as _uploads



def parse_json_argument(value: str) -> dict[str, Any]:
    raw = Path(value[1:]).expanduser().read_text(encoding="utf-8") if value.startswith("@") else value
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise SystemExit("JSON input must be an object")
    return parsed


def command_set_key(args: argparse.Namespace) -> int:
    if args.stdin:
        api_key = sys.stdin.readline().strip()
    else:
        _presentation.command_progress(f"Create or copy an API Key at: {_settings.PUBLIC_ACCOUNT_URL}")
        api_key = getpass.getpass("Paste API Key (input hidden): ").strip()
    if not api_key:
        raise SystemExit("API Key cannot be empty")
    _settings.SENSITIVE_OUTPUT_VALUES.add(api_key)
    account = None
    if not args.no_verify:
        _presentation.command_progress("API Key received. Verifying your HolyCrab account...")
        status, response, account = _tasks.query_account(api_key=api_key)
        if not _results.response_ok(status, response):
            _transport.response_data(status, response)
        if account is None:
            raise ValueError("Account verification returned an incomplete response; the API Key was not saved")
    config = _state.load_config()
    previously_configured = bool(config.get("apiKey"))
    config["apiKey"] = api_key
    _state.save_config(config)
    _presentation.command_progress("HolyCrab API Key saved locally with user-only permissions.")
    overridden = bool(os.environ.get("HOLYCRAB_API_KEY"))
    if overridden and not sys.stdout.isatty():
        _presentation.command_progress("Warning: HOLYCRAB_API_KEY is still set and overrides the saved login.")
        _presentation.command_progress("Run: " + _results.clear_environment_command("HOLYCRAB_API_KEY"))
    verified = account is not None
    guidance = _presentation.onboarding_guidance(configured=True, verified=verified, environment_override=overridden)
    result = _presentation.public_account_data(account) if isinstance(account, dict) else {}
    result.update({"configured": True, "valid": verified and not overridden, "savedKeyVerified": verified,
                   "credentialSource": "environment" if overridden else "local config", "onboarding": guidance})
    _presentation.print_result(result, force_json=args.stdin, introduce_onboarding=not previously_configured)
    if not sys.stdout.isatty() and not args.stdin:
        _presentation.show_onboarding(guidance, introduce=not previously_configured)
    return 0


def command_auth_status(args: argparse.Namespace) -> int:
    source = (
        "environment" if os.environ.get("HOLYCRAB_API_KEY")
        else "local config" if _state.load_config().get("apiKey") else None
    )
    if not source:
        _presentation.print_result({"configured": False, "valid": False, "next": "Run `holycrab setup`.",
                    "onboarding": _presentation.onboarding_guidance(configured=False),
                    "nextAction": _results.next_action("CONNECT_ACCOUNT", "Configure your API Key locally; never send it in chat.", "holycrab setup")})
        return 1
    _presentation.command_progress("Checking your HolyCrab account...")
    status, response, account = _tasks.query_account()
    if account is None:
        _presentation.print_result({"configured": True, "valid": False, "credentialSource": source, "httpStatus": status,
                    "onboarding": _presentation.onboarding_guidance(configured=True),
                    "nextAction": _results.next_action("CHECK_API_KEY", "Check that the API Key is enabled on the account page, then configure the correct Key locally.", "holycrab setup")})
        return 1
    _presentation.print_result({"configured": True, "valid": True, "credentialSource": source, "account": _presentation.public_account_data(account),
                "onboarding": _presentation.onboarding_guidance(configured=True, verified=True)})
    return 0


def command_clear_key(args: argparse.Namespace) -> int:
    config = _state.load_config()
    config.pop("apiKey", None)
    config.pop("apiKeyDpapi", None)
    _state.save_config(config)
    print("Saved API Key cleared from this computer.\n"
          "Environment variables were not changed.\n"
          "The online API Key was not revoked.")
    return 0


def command_models_list(args: argparse.Namespace) -> int:
    models = _capabilities.all_models()
    brief = [{"id": item["id"], "label": item.get("label"), "kind": item["kind"]} for item in models]
    _presentation.print_result(_capabilities.capability_snapshot() if args.json else brief, force_json=args.json, view="models")
    return 0


def command_models_show(args: argparse.Namespace) -> int:
    _presentation.print_result(_capabilities.find_model(args.model))
    return 0


def command_credits_balance(args: argparse.Namespace) -> int:
    _presentation.command_progress("Checking your HolyCrab credit balance...")
    status, response, _ = _tasks.query_account()
    return _presentation.print_response(status, _presentation.public_account_response(response), view="Credit balance")


def command_generation_estimate(args: argparse.Namespace) -> int:
    _presentation.command_progress(f"Estimating credits for this {args.kind} request; no task will be created...")
    _presentation.print_result(_generation.estimate_generation(args.kind, parse_json_argument(args.json)))
    return 0


def command_generation_create(args: argparse.Namespace) -> int:
    payload = parse_json_argument(args.json)
    approved_estimate = None
    if not args.yes:
        _presentation.command_progress("Estimating credits before confirmation; no task has been submitted...")
        approved_estimate = _generation.create_generation(args.kind, payload, confirmed=False)
        _presentation.print_result(approved_estimate)
        if sys.stdout.isatty():
            print("\nRequest to confirm:\n" + "\n".join(_presentation.terminal_fields(_capabilities.normalize_generation_request(args.kind, payload))), flush=True)
        if not sys.stdin.isatty():
            print("Not submitted. Re-run with --yes only after the user confirms the estimate.", file=sys.stderr)
            return 2
        answer = _presentation.read_confirmation("Create one billable task with this request? [y/N] ")
        if answer not in {"y", "yes"}:
            print("Cancelled; no generation task was created.", file=sys.stderr)
            return 2
    result = _generation.create_generation(
        args.kind,
        payload,
        confirmed=True,
        attempt_id=args.attempt_id,
        approved_estimate=approved_estimate,
        progress=_presentation.command_progress,
    )
    _presentation.print_result(result)
    if not sys.stdout.isatty():
        _presentation.command_progress("Task created. Next: " + result["nextAction"]["command"] if result.get("state") == "created"
                         else "Creation not confirmed. Query existing tasks; do not resubmit.")
    return 0 if result.get("state") == "created" else 1


def command_attempts_list(args: argparse.Namespace) -> int:
    _presentation.print_result({"attempts": _state.attempt_records()})
    return 0


def command_attempts_get(args: argparse.Namespace) -> int:
    _presentation.print_result(_state.attempt_record(args.attempt_id))
    return 0


def command_task_get(args: argparse.Namespace) -> int:
    _presentation.command_progress(f"Checking task {args.uniq_id}...")
    status, response = _transport.send("GET", f"/api/tasks/{urllib.parse.quote(args.uniq_id, safe='')}")
    return _presentation.print_response(status, _presentation.public_task_response(response))


def command_task_list(args: argparse.Namespace) -> int:
    query = _tasks.task_list_query(args.page, args.page_size, args.start_date, args.end_date, args.type)
    _presentation.command_progress("Checking tasks for your HolyCrab account...")
    status, response = _transport.send(
        "GET", "/api/tasks", query=query
    )
    return _presentation.print_response(status, _presentation.public_task_response(response), view="tasks")


def command_poll_task(args: argparse.Namespace) -> int:
    _tasks.validate_wait_limits(args.timeout, args.interval)
    _presentation.command_progress(f"Waiting for task {args.uniq_id}, up to {args.timeout:g} seconds. Ctrl+C stops only this local wait...")
    code, latest = _tasks.poll_task(args.uniq_id, args.timeout, args.interval, emit=True)
    if code == 2:
        _presentation.print_result({"timedOut": True, "taskId": args.uniq_id,
                    "nextAction": _results.next_action("CONTINUE_QUERYING", "The local wait timed out; the task was not resubmitted. Query this same task again.", f"holycrab tasks get {args.uniq_id}")})
        if not sys.stdout.isatty():
            print("Polling timed out; the task was not resubmitted.", file=sys.stderr)
    else:
        if not sys.stdout.isatty():
            _presentation.command_progress("Task wait finished: completed." if code == 0 else "Task wait finished without a successful result. Review the returned status and error.")
    return code


def command_download(args: argparse.Namespace) -> int:
    _presentation.command_progress(f"Checking downloadable results for task {args.uniq_id}...")
    status, response = _transport.send("GET", f"/api/tasks/{urllib.parse.quote(args.uniq_id, safe='')}")
    data = _transport.response_data(status, response)
    urls = _results.task_output_urls(data)
    if not urls:
        raise SystemExit("This task has no downloadable URL yet")
    if args.index < 0 or args.index >= len(urls):
        raise SystemExit(f"Output index must be between 0 and {len(urls) - 1}")
    url = _transport.validate_download_url(urls[args.index])
    destination = Path(args.output).expanduser().resolve()
    force = bool(getattr(args, "force", False))
    if destination.exists() and not force:
        raise SystemExit(f"Output already exists: {destination}. Use --force to replace it")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, partial_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".part", dir=destination.parent
    )
    partial = Path(partial_name)
    total = 0
    last_progress = time.monotonic()
    try:
        _presentation.command_progress(f"Downloading output {args.index + 1}...\nPath: {destination}")
        with _transport.open_download(url) as remote, os.fdopen(descriptor, "wb") as local:
            descriptor = -1
            content_length = remote.headers.get("Content-Length") if getattr(remote, "headers", None) else None
            expected_length = int(content_length) if isinstance(content_length, str) and content_length.isascii() and content_length.isdigit() else None
            if expected_length is not None and expected_length > _settings.MAX_DOWNLOAD_RESPONSE_BYTES:
                raise SystemExit("Download response is larger than the allowed limit")
            while True:
                chunk = remote.read(1024 * 1024)
                if not chunk:
                    break
                local.write(chunk)
                total += len(chunk)
                if total > _settings.MAX_DOWNLOAD_BYTES:
                    raise SystemExit("Download exceeded the allowed size limit")
                now = time.monotonic()
                if now - last_progress >= 2:
                    _presentation.command_progress(f"Downloaded {total} bytes...")
                    last_progress = now
            if expected_length is not None and total != expected_length:
                raise SystemExit(f"Download was incomplete: expected {expected_length} bytes, received {total}. The output was not replaced")
        if force:
            os.replace(partial, destination)
        else:
            try:
                os.link(partial, destination)
            except FileExistsError as error:
                raise SystemExit(f"Output already exists: {destination}. Use --force to replace it") from error
            partial.unlink()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            partial.unlink()
        except FileNotFoundError:
            pass
    _presentation.print_result({"taskId": args.uniq_id, "output": str(destination), "bytes": total})
    if not sys.stdout.isatty():
        _presentation.command_progress(f"Download completed: {destination} ({total} bytes).")
    return 0


def command_real_human_start(args: argparse.Namespace) -> int:
    _presentation.command_progress("Creating one real-human authorization. Do not repeat this request if the result is unclear.")
    _presentation.print_result(_real_human.create_authorization(args.name))
    return 0


def command_real_human_get(args: argparse.Namespace) -> int:
    _presentation.command_progress("Checking the existing authorization...")
    _presentation.print_result(_real_human.get_authorization(args.authorization_id))
    return 0


def command_real_human_wait(args: argparse.Namespace) -> int:
    _tasks.validate_wait_limits(args.timeout, args.interval)
    _presentation.command_progress(f"Waiting for this authorization for up to {args.timeout:g} seconds. Ctrl+C stops local waiting only.")
    return _real_human.poll_resource(args.authorization_id, args.timeout, args.interval, authorization=True)


def command_real_human_groups(args: argparse.Namespace) -> int:
    _presentation.command_progress("Listing authorized people...")
    _presentation.print_result(_real_human.list_real_human_groups(args.page, args.page_size), view="people")
    return 0


def command_real_human_group_rename(args: argparse.Namespace) -> int:
    _presentation.command_progress("Renaming the selected authorized person...")
    _presentation.print_result(_real_human.rename_real_human_group(args.group_id, args.name), view="Person renamed")
    return 0




def command_real_human_group_delete(args: argparse.Namespace) -> int:
    _presentation.command_progress("Checking the person and assets selected for deletion...")
    target = _real_human.find_real_human_group(args.group_id)
    _presentation.print_result({"warning": "Permanent deletion removes this person, all group assets, and upstream records.",
                "target": _presentation.public_fields(target, ("uniqId", "name", "assetCount"))})
    if not _presentation.deletion_confirmed("Permanently delete this person and every group asset? [y/N] ", args.yes):
        return 2
    _presentation.command_progress("Deleting the confirmed person and group assets...")
    _presentation.print_result(_real_human.delete_real_human_group(args.group_id, True, target), view="Person deleted")
    return 0


def command_real_human_assets(args: argparse.Namespace) -> int:
    _presentation.command_progress("Listing assets for the selected authorized person...")
    _presentation.print_result(_real_human.list_real_human_assets(args.group, args.page, args.page_size), view="materials")
    return 0


def command_real_human_asset_delete(args: argparse.Namespace) -> int:
    _presentation.command_progress("Checking the real-human asset selected for deletion...")
    group = _real_human.find_real_human_group(args.group)
    asset = _real_human.get_asset(args.asset_id)
    _presentation.print_result({"warning": "Permanent deletion removes this asset's storage, upstream record, and database record.",
                "target": {"groupUniqId": group.get("uniqId"), "groupName": group.get("name"),
                           "groupAssetCount": group.get("assetCount"),
                           **_presentation.public_fields(asset, ("uniqId", "name", "assetType"))}})
    if not _presentation.deletion_confirmed("Permanently delete this real-human asset? [y/N] ", args.yes):
        return 2
    _presentation.command_progress("Deleting the confirmed real-human asset...")
    _presentation.print_result(_real_human.delete_real_human_asset(args.group, args.asset_id, True, group, asset), view="Material deleted")
    return 0


def command_asset_get(args: argparse.Namespace) -> int:
    _presentation.command_progress("Checking the existing asset...")
    _presentation.print_result(_real_human.get_asset(args.uniq_id))
    return 0


def command_asset_wait(args: argparse.Namespace) -> int:
    _tasks.validate_wait_limits(args.timeout, args.interval)
    result = 0
    identifiers = args.uniq_id if isinstance(args.uniq_id, list) else [args.uniq_id]
    _presentation.command_progress(f"Waiting for {len(identifiers)} assets, up to {args.timeout:g} seconds per asset. Ctrl+C stops local waiting only.")
    completed = 0
    for index, identifier in enumerate(identifiers, 1):
        _presentation.command_progress(f"Checking asset {index}/{len(identifiers)}: {identifier}; {completed} ready.")
        code = _real_human.poll_resource(identifier, args.timeout, args.interval, authorization=False)
        completed += int(code == 0)
        result = max(result, code)
    _presentation.command_progress(f"Asset wait ended: {completed}/{len(identifiers)} ready.")
    return result


def command_upload_record(args: argparse.Namespace) -> int:
    _presentation.print_result(_uploads.upload_plan_record(args.plan_id))
    return 0


def command_upload_asset(args: argparse.Namespace) -> int:
    _presentation.command_progress("Checking files and preparing the complete upload preview...")
    preview = _uploads.prepare_upload_plan(args.file, group_uniq_id=args.real_human_group,
                                  duration_seconds=args.duration_seconds)
    _presentation.print_result(preview)
    if not args.yes:
        if not sys.stdin.isatty():
            print("No files were uploaded. Re-run with --yes only after the user confirms the complete preview.", file=sys.stderr)
            return 2
        answer = _presentation.read_confirmation(f"Upload these {len(args.file)} files to the displayed target? [y/N] ")
        if answer not in {"y", "yes"}:
            try:
                _uploads.upload_plan_path(preview["uploadPlanId"]).unlink()
            except FileNotFoundError:
                pass
            print("Cancelled; no files were uploaded.", file=sys.stderr)
            return 2
    result = _uploads.execute_upload_plan(preview["uploadPlanId"], confirmed=True, progress=_presentation.command_progress)
    _presentation.print_result(result)
    if not sys.stdout.isatty():
        _presentation.command_progress(f"Upload ended: {len(result['uploaded'])} uploaded, {int(result['failedOrUnknown'] is not None)} failed or unknown, {len(result['notAttempted'])} not attempted.")
    return 0 if isinstance(result, _results.OperationResult) and result.outcome == "success" else 1


def command_update(args: argparse.Namespace) -> int:
    _presentation.command_progress("Checking GitHub for a stable HolyCrab update...")
    update = _lifecycle.check_for_update(force=True, timeout=30.0)
    if update.get("error"):
        _presentation.print_result(update)
        print("Current installation was not changed.\nNext:\n  holycrab update --check", file=sys.stderr)
        return 1
    _presentation.print_result({key: value for key, value in update.items() if key != "release"})
    if not update.get("updateAvailable") or args.check:
        return 0
    release = update.get("release")
    if not isinstance(release, dict):
        raise SystemExit("Release metadata cache is missing; run `holycrab update --check` again")
    if not args.yes:
        if not sys.stdin.isatty():
            print("Update not installed. Re-run interactively or use --yes after reviewing the release.", file=sys.stderr)
            return 2
        if _presentation.read_confirmation("Install this verified HolyCrab update now? [y/N] ") not in {"y", "yes"}:
            print("Update cancelled; the current installation was not changed.", file=sys.stderr)
            return 2
    _lifecycle.run_update(release, progress=_presentation.command_progress)
    return 0


def command_uninstall(args: argparse.Namespace) -> int:
    # Before confirmation an interrupt changes nothing; after confirmation the
    # cleanup may be partial, so never promise that files/registrations remain.
    args.cleanup_started = False
    try:
        manifest = _lifecycle.load_installation()
        if not manifest:
            return _lifecycle.perform_uninstall(args)
        with _lifecycle.installation_lock(Path(manifest["prefix"])):
            return _lifecycle.perform_uninstall(args)
    except KeyboardInterrupt:
        if not args.cleanup_started:
            raise
        raise _results.CommandInterrupted({"state": "unknown", "interrupted": True,
            "nextAction": _results.next_action("CHECK_LOCAL_CLEANUP", "Uninstall was interrupted after cleanup started. Some registrations or program files may have been removed. Check the installation or reinstall before continuing; local data purge may be incomplete.", "holycrab doctor --json")}) from None


def command_doctor(args: argparse.Namespace) -> int:
    if args.online:
        _presentation.command_progress("Checking local health, refreshing version status, validating the API Key, and checking selected MCP registrations...")
    report = _lifecycle.local_health_report(online=args.online)
    _presentation.print_result(report, force_json=args.json)
    return 0 if report["ok"] else 1


def add_json_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", required=True, help="JSON object or @/path/to/request.json")


def add_key_input_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--stdin", action="store_true", help="Read the key from stdin; avoid shell history")
    parser.add_argument("--no-verify", action="store_true", help=argparse.SUPPRESS)


def add_wait_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=600.0)


def add_page_arguments(parser: argparse.ArgumentParser, page_size: int = 20) -> None:
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--page-size", type=int, default=page_size)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="holycrab", description="HolyCrab local CLI and MCP server")
    parser.add_argument("--version", action="version", version=f"%(prog)s {_settings.VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="Configure and verify a HolyCrab API Key")
    add_key_input_arguments(setup)
    setup.set_defaults(func=command_set_key)

    auth = sub.add_parser("auth", help="Configure the local API Key")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True, metavar="{status,clear-key}")
    set_key = auth_sub.add_parser("set-key", description="Compatibility alias. Use 'holycrab setup' to configure your API Key.")
    add_key_input_arguments(set_key)
    set_key.set_defaults(func=command_set_key)
    auth_sub.add_parser("status", help="Check whether the configured API Key is valid").set_defaults(func=command_auth_status)
    auth_sub.add_parser("clear-key", help="Remove the saved API Key").set_defaults(func=command_clear_key)

    models = sub.add_parser("models", help="Inspect public model capabilities")
    models_sub = models.add_subparsers(dest="models_command", required=True)
    models_list = models_sub.add_parser("list")
    models_list.add_argument("--json", action="store_true")
    models_list.set_defaults(func=command_models_list)
    models_show = models_sub.add_parser("show")
    models_show.add_argument("model")
    models_show.set_defaults(func=command_models_show)

    credits = sub.add_parser("credits", help="Check the current credit balance")
    credits_sub = credits.add_subparsers(dest="credits_command", required=True, metavar="{balance}")
    credits_sub.add_parser("balance", help="Show the current credit balance").set_defaults(func=command_credits_balance)
    # Keep old scripts working without advertising a second estimate workflow.
    estimate = credits_sub.add_parser(
        "estimate", description="Compatibility alias. Use 'holycrab generate estimate' for credit estimates."
    )
    estimate.add_argument("--kind", required=True, choices=["video", "image", "audio"])
    add_json_argument(estimate)
    estimate.set_defaults(func=command_generation_estimate)

    generate = sub.add_parser("generate", help="Estimate or create generation tasks")
    generate_sub = generate.add_subparsers(dest="generate_command", required=True)
    gen_estimate = generate_sub.add_parser("estimate")
    gen_estimate.add_argument("--kind", required=True, choices=["video", "image", "audio"])
    add_json_argument(gen_estimate)
    gen_estimate.set_defaults(func=command_generation_estimate)
    gen_create = generate_sub.add_parser("create")
    gen_create.add_argument("--kind", required=True, choices=["video", "image", "audio"])
    add_json_argument(gen_create)
    gen_create.add_argument("--yes", action="store_true", help="Confirm exactly one billable task")
    gen_create.add_argument("--attempt-id", help="Local ID for this one submission attempt")
    gen_create.set_defaults(func=command_generation_create)
    attempts = generate_sub.add_parser("attempts", help="Inspect one-shot local submission records")
    attempts_sub = attempts.add_subparsers(dest="attempts_command", required=True)
    attempts_sub.add_parser("list", help="List local submission attempts").set_defaults(func=command_attempts_list)
    attempt_get = attempts_sub.add_parser("get", help="Get one local submission attempt")
    attempt_get.add_argument("attempt_id")
    attempt_get.set_defaults(func=command_attempts_get)

    tasks = sub.add_parser("tasks", help="Query generation tasks")
    tasks_sub = tasks.add_subparsers(dest="tasks_command", required=True)
    task_get = tasks_sub.add_parser("get")
    task_get.add_argument("uniq_id")
    task_get.set_defaults(func=command_task_get)
    task_list = tasks_sub.add_parser("list")
    task_list.add_argument("--page", type=int, default=1)
    task_list.add_argument("--page-size", type=int, default=20)
    task_list.add_argument("--start-date", help="Inclusive start date in YYYY-MM-DD; requires --end-date")
    task_list.add_argument("--end-date", help="Inclusive end date in YYYY-MM-DD; requires --start-date")
    task_list.add_argument("--type", choices=["IMAGE", "VIDEO", "AUDIO", "TEXT"], help="Filter by task type")
    task_list.set_defaults(func=command_task_list)
    task_wait = tasks_sub.add_parser("wait")
    task_wait.add_argument("uniq_id")
    task_wait.add_argument("--interval", type=float, default=5.0)
    task_wait.add_argument("--timeout", type=float, default=600.0)
    task_wait.set_defaults(func=command_poll_task)

    download = sub.add_parser("download", help="Download one completed task output")
    download.add_argument("uniq_id")
    download.add_argument("--output", required=True)
    download.add_argument("--index", type=int, default=0)
    download.add_argument("--force", action="store_true", help="Replace an existing output file")
    download.set_defaults(func=command_download)

    real_human = sub.add_parser("real-human", help="Authorize a real person and query their assets")
    human_sub = real_human.add_subparsers(dest="real_human_command", required=True)
    human_start = human_sub.add_parser("start", help="Create a private link and local QR image for manual verification")
    human_start.add_argument("--name", required=True)
    human_start.set_defaults(func=command_real_human_start)
    human_get = human_sub.add_parser("get", help="Query the same authorization ID without creating another")
    human_get.add_argument("authorization_id", help="Authorization ID returned by real-human start")
    human_get.set_defaults(func=command_real_human_get)
    human_wait = human_sub.add_parser("wait", help="Wait for authorization status changes; default timeout 600 seconds")
    human_wait.add_argument("authorization_id", help="Authorization ID returned by real-human start")
    add_wait_arguments(human_wait)
    human_wait.set_defaults(func=command_real_human_wait)
    groups = human_sub.add_parser("groups").add_subparsers(dest="groups_command", required=True)
    group_list = groups.add_parser("list")
    add_page_arguments(group_list)
    group_list.set_defaults(func=command_real_human_groups)
    group_rename = groups.add_parser("rename")
    group_rename.add_argument("group_id")
    group_rename.add_argument("--name", required=True)
    group_rename.set_defaults(func=command_real_human_group_rename)
    group_delete = groups.add_parser("delete")
    group_delete.add_argument("group_id")
    group_delete.add_argument("--yes", action="store_true", help="Use only after explicit approval of this permanent deletion")
    group_delete.set_defaults(func=command_real_human_group_delete)
    human_assets = human_sub.add_parser("assets").add_subparsers(dest="human_assets_command", required=True)
    human_asset_list = human_assets.add_parser("list")
    human_asset_list.add_argument("--group", required=True)
    add_page_arguments(human_asset_list, 50)
    human_asset_list.set_defaults(func=command_real_human_assets)
    human_asset_delete = human_assets.add_parser("delete")
    human_asset_delete.add_argument("asset_id")
    human_asset_delete.add_argument("--group", required=True)
    human_asset_delete.add_argument("--yes", action="store_true", help="Use only after explicit approval of this permanent deletion")
    human_asset_delete.set_defaults(func=command_real_human_asset_delete)

    assets = sub.add_parser("assets", help="Upload and query media assets")
    assets_sub = assets.add_subparsers(dest="assets_command", required=True)
    uploads = assets_sub.add_parser("uploads", help="Inspect a saved upload record").add_subparsers(dest="uploads_command", required=True)
    upload_record = uploads.add_parser("get")
    upload_record.add_argument("plan_id")
    upload_record.set_defaults(func=command_upload_record)
    upload = assets_sub.add_parser("upload", help="Preview 1-10 files, confirm once, then upload in order")
    upload.add_argument("file", nargs="+", help="One to ten local image/video/audio files")
    upload.add_argument("--duration-seconds", type=int, help="Known media duration sent for server validation")
    upload.add_argument("--real-human-group", help="Authorized person's public group ID; real-human formats apply")
    upload.add_argument("--yes", action="store_true", help="Confirm the complete printed file list and target")
    upload.set_defaults(func=command_upload_asset)
    asset_get = assets_sub.add_parser("get", help="Query one asset and receive its next action")
    asset_get.add_argument("uniq_id", help="Public asset ID")
    asset_get.set_defaults(func=command_asset_get)
    asset_wait = assets_sub.add_parser("wait", help="Wait for one or more assets; default timeout 600 seconds each")
    asset_wait.add_argument("uniq_id", nargs="+", help="One or more public asset IDs")
    add_wait_arguments(asset_wait)
    asset_wait.set_defaults(func=command_asset_wait)

    mcp = sub.add_parser("mcp", help="Run the local stdio MCP server")
    mcp_sub = mcp.add_subparsers(dest="mcp_command", required=True)
    mcp_sub.add_parser("serve").set_defaults(func=_mcp.command_mcp_serve)
    update = sub.add_parser("update", help="Check for or install a verified stable CLI release")
    update.add_argument("--check", action="store_true", help="Only check; never run an installer")
    update.add_argument("--yes", action="store_true", help="Install after release and digest verification")
    update.set_defaults(func=command_update)
    doctor = sub.add_parser("doctor", help="Check the local installation")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--online", action="store_true", help="Refresh release status and verify the API Key")
    doctor.set_defaults(func=command_doctor)
    uninstall = sub.add_parser("uninstall", help="Remove this installer-managed HolyCrab CLI")
    uninstall.add_argument("--purge", action="store_true", help="Also remove known local credentials and records")
    uninstall.add_argument("--yes", action="store_true", help="Confirm the complete uninstall preview")
    uninstall.set_defaults(func=command_uninstall)
    return parser


def command_failure_feedback(args: argparse.Namespace) -> None:
    if args.func in {command_set_key, command_auth_status, command_credits_balance}:
        _presentation.command_progress("Check your connection and whether the API Key is enabled.\nNext:\n  holycrab setup\nDo not share your API Key in chat.")
    elif args.func in {command_task_get, command_poll_task, command_download}:
        identifier = args.uniq_id
        command = f"holycrab tasks get {identifier}" if re.fullmatch(r"[A-Za-z0-9]{1,64}", identifier) else "holycrab tasks list"
        _presentation.command_progress(f"Check this existing task; do not submit a replacement.\nNext:\n  {command}")
    elif args.func in {command_generation_create, command_upload_asset, command_real_human_start,
                        command_real_human_group_rename, command_real_human_group_delete, command_real_human_asset_delete}:
        _presentation.command_progress("Review the result and existing records before deciding.\nDo not automatically repeat a write with an uncertain result.")
    elif args.func in {command_generation_estimate, command_task_list, command_real_human_get,
                        command_real_human_wait, command_real_human_groups, command_real_human_assets,
                        command_asset_get, command_asset_wait}:
        _presentation.command_progress("Check your connection and credentials, then query the same records.\nDo not create a new task, authorization or upload to resume a query.")
    elif args.func in {command_doctor, command_update}:
        _presentation.command_progress("Review the checks; reinstall only if a repair is needed.\nNext:\n  holycrab doctor")


def run_cli_command(args: argparse.Namespace, raw_args: list[str]) -> int:
    with _capabilities.capability_operation():
        if raw_args[0] != "uninstall":
            warning = _real_human.cleanup_authorization_qrs()
            if warning:
                print(warning, file=sys.stderr)
            _uploads.cleanup_upload_plans()
        _lifecycle.startup_maintenance(raw_args)
        code = args.func(args)
        if code == 1 and not (sys.stdout.isatty() and args.func in
                             {command_auth_status, command_generation_create, command_doctor, command_update}):
            command_failure_feedback(args)
        return code


def main() -> int:
    raw_args = sys.argv[1:]
    args = build_parser().parse_args()
    try:
        # Semantic argument checks also precede housekeeping and version requests.
        if args.func in {command_poll_task, command_real_human_wait, command_asset_wait}:
            _tasks.validate_wait_limits(args.timeout, args.interval)
        if args.func in {command_task_get, command_poll_task, command_download}:
            try:
                _presentation.public_id(args.uniq_id, "taskId")
            except ValueError as error:
                raise _results.CommandUsageError(str(error)) from error
        if args.func == command_task_list:
            _tasks.task_list_query(args.page, args.page_size, args.start_date, args.end_date, args.type)
        if args.func in {command_real_human_groups, command_real_human_assets}:
            _real_human.page_query(args.page, args.page_size)
        return run_cli_command(args, raw_args)
    except _results.CommandInterrupted as error:
        _presentation.print_result(error.result)
        print("Stopped after the operation began. Review its state; do not retry automatically.", file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        print("Stopped. Ctrl+C ended this local operation; it does not cancel an existing online task or authorization.", file=sys.stderr)
        return 130
    except _results.CommandUsageError as error:
        print(f"Invalid argument: {_presentation.sanitize_text_for_output(str(error))}", file=sys.stderr)
        return 2
    except SystemExit as error:
        if isinstance(error.code, int):
            return error.code
        print(f"Error: {_presentation.sanitize_text_for_output(str(error))}", file=sys.stderr)
        command_failure_feedback(args)
        return 1
    except json.JSONDecodeError as error:
        print(f"Invalid JSON: {error}", file=sys.stderr)
        return 2
    except urllib.error.URLError as error:
        print(f"Network error: {_presentation.sanitize_text_for_output(str(error.reason))}", file=sys.stderr)
        command_failure_feedback(args)
        return 1
    except (ValueError, OSError, http.client.HTTPException) as error:
        print(f"Error: {_presentation.sanitize_text_for_output(str(error))}", file=sys.stderr)
        command_failure_feedback(args)
        return 1
