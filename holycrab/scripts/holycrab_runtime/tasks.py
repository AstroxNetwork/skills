"""HolyCrab tasks responsibilities."""
from __future__ import annotations

import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any
from . import presentation as _presentation
from . import results as _results
from . import transport as _transport



def validate_wait_limits(timeout: float, interval: float) -> None:
    if not math.isfinite(timeout) or timeout < 0 or not math.isfinite(interval) or interval <= 0:
        raise _results.CommandUsageError("timeout must be finite and nonnegative; interval must be finite and positive")


def task_list_query(page: int, page_size: int, start: str | None, end: str | None,
                    task_type: str | None = None) -> list[tuple[str, str]]:
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 100:
        raise _results.CommandUsageError("page must be positive and pageSize must be between 1 and 100")
    if bool(start) != bool(end):
        raise _results.CommandUsageError("--start-date and --end-date must be supplied together")
    if start and end:
        for value in (start, end):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise _results.CommandUsageError("Dates must use YYYY-MM-DD")
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError as error:
                raise _results.CommandUsageError("Dates must use valid YYYY-MM-DD calendar dates") from error
        if start > end:
            raise _results.CommandUsageError("start date must not be after end date")
    query = [("page", str(page)), ("pageSize", str(page_size))]
    if start and end:
        query.extend((("startDate", start), ("endDate", end)))
    if task_type:
        if task_type not in {"IMAGE", "VIDEO", "AUDIO", "TEXT"}:
            raise _results.CommandUsageError("taskType must be IMAGE, VIDEO, AUDIO, or TEXT")
        query.append(("taskType", task_type))
    return query


def valid_account_payload(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    named = any(isinstance(value.get(key), str) and value[key].strip() for key in ("username", "nickname"))
    credit = value.get("credit")
    return bool(named or (isinstance(credit, (int, float)) and not isinstance(credit, bool) and math.isfinite(credit)))


def query_account(*, api_key: str | None = None) -> tuple[int, Any, dict[str, Any] | None]:
    """One account boundary; callers retain their own error and output semantics."""
    options = {"api_key": api_key} if api_key is not None else {}
    status, response = _transport.send("GET", "/api/user/me", **options)
    account = _transport.response_data(status, response) if _results.response_ok(status, response) else None
    return status, response, account if valid_account_payload(account) else None




def poll_task(uniq_id: str, timeout: float, interval: float, *, emit: bool = False) -> tuple[int, Any]:
    validate_wait_limits(timeout, interval)
    deadline = time.monotonic() + timeout
    latest: Any = None
    previous_state: Any = object()
    while True:
        status, latest = _transport.send("GET", f"/api/tasks/{urllib.parse.quote(uniq_id, safe='')}")
        data = _transport.response_data(status, latest) if _results.response_ok(status, latest) else None
        step = data.get("step") if isinstance(data, dict) else None
        state = (status, step)
        if emit and state != previous_state:
            _presentation.print_response(status, _presentation.public_task_response(latest))
            previous_state = state
        if not _results.response_ok(status, latest):
            return 1, latest
        if step == 2:
            return 0, latest
        if step == 3:
            return 1, latest
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 2, latest
        time.sleep(min(interval, remaining))
