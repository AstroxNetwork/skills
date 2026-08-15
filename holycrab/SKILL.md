---
name: holycrab
description: Use when a user wants to discover, estimate, create, upload media for, or query HolyCrab image, video, and audio generation from a local Agent environment.
---

# HolyCrab

Use the installed `holycrab` CLI or its local MCP tools. Treat the capability response as current; do not guess model limits from memory.

## Safe workflow

1. Check the configured API Key with `holycrab auth status`. If missing, ask the user to run `holycrab setup` locally. Never ask them to paste the API Key into chat.
2. Query `capabilities_list` / `capability_get`, or `holycrab models list|show`, before building a request.
3. For a local media file, use `holycrab assets upload` and keep only the returned stable asset ID.
4. Call `generation_estimate` or `holycrab generate estimate`. Tell the user the model, output specification, duration, and estimated credit.
5. Wait for explicit confirmation. Create one new stable `attemptId` for that draw, then call `generation_create` once with `confirmed: true` and that ID, or run `holycrab generate create ... --yes` once.
6. Save the returned task ID. Query it with `generation_get`, `generation_list`, or `holycrab tasks get|list|wait`.

## Submission rule

A user may intentionally repeat the same prompt to draw another result. Each explicit confirmation is a new submission and may create a new billable task.

Do not retry the same submission after a timeout or broken connection. Keep its attempt ID, query recent tasks, and explain that the result is uncertain. Reusing an existing attempt ID is blocked locally. If the user explicitly confirms another draw, create a different attempt ID.

## Public boundary

- Return public model names, limits, credit estimates, task IDs, status, public errors, and final outputs.
- Never expose the API Key, authentication headers, local config contents, temporary upload URLs, or their query parameters.
- Do not use a generic raw-request path when a named CLI command or MCP tool exists.
- Do not invent output URLs or retry a paid operation during polling.

The bundled [public capability manifest](references/capabilities.json) is a fallback for discovery. Prefer the installed CLI/MCP response when available.
