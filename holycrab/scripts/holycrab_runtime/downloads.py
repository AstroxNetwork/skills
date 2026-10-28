"""Verified task downloads shared by CLI, MCP and workflows."""
from __future__ import annotations
import os
import tempfile
import time
import urllib.parse
from pathlib import Path
from . import transport as _transport, results as _results, settings as _settings, presentation as _presentation


def download_task(task_id: str, output: str, *, index: int = 0, force: bool = False, progress=None) -> dict:
    progress = progress or (lambda message: None)
    task_id = _presentation.public_id(task_id, "taskId")
    progress(f"Checking downloadable results for task {task_id}...")
    status, response = _transport.send("GET", f"/api/tasks/{urllib.parse.quote(task_id, safe='')}")
    data = _transport.response_data(status, response)
    urls = _results.task_output_urls(data)
    if not urls:
        raise SystemExit("This task has no downloadable URL yet")
    if index < 0 or index >= len(urls):
        raise SystemExit(f"Output index must be between 0 and {len(urls) - 1}")
    url = _transport.validate_download_url(urls[index])
    destination = Path(output).expanduser().resolve()
    force = bool(force)
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
        progress(f"Downloading output {index + 1}...\nPath: {destination}")
        with _transport.open_download(url) as remote, os.fdopen(descriptor, "wb") as local:
            descriptor = -1
            if getattr(remote, "status", 200) != 200 or remote.headers.get("Content-Range"):
                raise SystemExit("Download did not return a complete file; partial responses are not accepted. The output was not replaced")
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
                    progress(f"Downloaded {total} bytes...")
                    last_progress = now
            if expected_length is not None and total != expected_length:
                raise SystemExit(f"Download was incomplete: expected {expected_length} bytes, received {total}. The output was not replaced")
        if force:
            os.replace(partial, destination)
        else:
            try:
                if os.name == "nt":
                    # Windows rename is atomic and refuses an existing destination,
                    # including filesystems without hard-link support (e.g. exFAT).
                    os.rename(partial, destination)
                else:
                    os.link(partial, destination)
            except FileExistsError as error:
                raise SystemExit(f"Output already exists: {destination}. Use --force to replace it") from error
            partial.unlink(missing_ok=True)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            partial.unlink()
        except FileNotFoundError:
            pass
    return {"taskId": task_id, "output": str(destination), "bytes": total}
