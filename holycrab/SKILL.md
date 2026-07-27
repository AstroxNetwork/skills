---
name: holycrab
description: Call the HolyCrab Generation API with an API key for account and credit checks, task queries, asset upload and management, and Seedance video, Seedream image, or speech generation. Use when the user asks to invoke, integrate, debug, script, or inspect HolyCrab API-key endpoints, create or poll generation tasks, estimate frozen credit, or manage HolyCrab media assets.
---

# Holycrab

Use the bundled client and API reference to make safe, reproducible HolyCrab requests.

## Prepare authentication

Set `HOLYCRAB_API_KEY` in the environment. Use it for every request through this Skill.
- Optionally set `HOLYCRAB_BASE_URL`; default to `https://abgzfc.holycrab.ai`.

Never print, persist, commit, or place the API key directly in command arguments. Redact authentication headers and presigned URL query strings from user-visible output.

Do not call `/api/user-tokens...` console endpoints: the source documentation requires a console JWT for them, while this Skill intentionally supports only `HOLYCRAB_API_KEY`.

## Select the operation

Read [references/api.md](references/api.md) before constructing a request. Search it by endpoint or section instead of loading unrelated portions:

```bash
rg -n '^##|^###|`(GET|POST) ' references/api.md
rg -n '/api/tasks/image-generation|图片生成' references/api.md
```

Use `scripts/holycrab_api.py` for calls:

```bash
python3 scripts/holycrab_api.py request GET /api/user/me
python3 scripts/holycrab_api.py request GET /api/tasks --query page=1 --query pageSize=20
python3 scripts/holycrab_api.py request POST /api/tasks/image-generation \
  --json '{"prompt":"A crab astronaut","model":"seedream-5-0-lite-260128","size":"2k"}'
```

The client sends requests immediately, including generation, upload, registration, and deletion requests. Ensure the endpoint and payload match the user's instruction before invoking it.

## Validate requests

Before a generation request:

1. Check required fields, model identifiers, duration, resolution or size, reference limits, and mutually exclusive fields against the reference.
2. Prefer the matching `freeze-credit` endpoint when the user asks for cost or when cost is material and unknown.
3. Preserve exact JSON field casing from the reference.
4. Treat HTTP success separately from the response envelope: require `code == 0`; otherwise report `message`, `errorCode`, and `requestId`.

Do not silently retry a generation submission. A timeout after submission is ambiguous and retrying can create a duplicate billable task. Query the task list or ask the user before resubmitting.

## Poll asynchronous work

After submission, retain the returned `uniqId` and poll:

```bash
python3 scripts/holycrab_api.py poll-task TASK_UNIQ_ID
```

Interpret `step=2` as complete and `step=3` as failed. Stop at the configured timeout and report the latest response. Do not fabricate output URLs; return only `videoUrl`, `imageUrls`, `audioIds`, or `cdnUrl` present in the response.

## Upload local assets

Use the three-stage helper for a local file:

```bash
python3 scripts/holycrab_api.py upload-asset /absolute/path/media.mp4 \
  --content-type video/mp4 --duration-seconds 8
```

The helper requests a presigned URL, uploads the bytes, and registers the asset. Presigned upload URLs may point outside the HolyCrab base domain; use only the URL returned by HolyCrab, never forward the API key to that host, and never log the URL.

Poll `GET /api/user-assets/{uniqId}` until the documented success or failure state before using the asset in video generation.

## Report results

Summarize the endpoint, HTTP status, envelope status, task or asset ID, current state, and available outputs. Keep credentials and signed URL parameters redacted. Include `requestId` when reporting an API error so the user can trace it.
