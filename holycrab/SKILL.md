---
name: holycrab
description: Use for HolyCrab generation, media uploads, and real-person authorization from a local Agent environment, including showing verification links and QR codes and using authorized reference assets.
---

# HolyCrab

Use the installed `holycrab` CLI or its local MCP tools. Treat its versioned capability snapshot as the contract bundled with the installed release; do not call it live data and do not guess model limits from memory.

## Safe workflow

1. Check the configured API Key with `holycrab auth status`. If missing, ask the user to run `holycrab setup` locally. Never ask them to paste the API Key into chat.
2. Query `capabilities_list` / `capability_get`, or `holycrab models list|show`, before building a request.
   Seedance video requests default to `generateAudio: true` when the field is omitted. Preserve an explicit `false`. Never add this field to a model whose capability says audio generation is unsupported.
   Select `seed-audio-1.0` for Seed Audio capability discovery, but omit `model` from the Seed Audio request body as indicated by `modelFieldInRequest: false`.
3. For a local media file, use `asset_upload` or `holycrab assets upload` and keep the returned stable asset ID. For a real person, follow the authorization workflow below before uploading. Query `asset_get` or `holycrab assets get|wait` until `ready: true` (`step: UPLOADED_TO_ARK`); upload registration alone is not readiness.
4. Call `generation_estimate` or `holycrab generate estimate`. Tell the user the model, output specification, duration, and estimated credit.
5. Wait for explicit confirmation. Create one new stable `attemptId` for that draw, then call `generation_create` once with `confirmed: true` and that ID, or run `holycrab generate create ... --yes` once.
6. Save the returned task ID. Query it with `generation_get`, `generation_list`, or `holycrab tasks get|list|wait`.

## Submission rule

A user may intentionally repeat the same prompt to draw another result. Each explicit confirmation is a new submission and may create a new billable task.

Do not retry the same submission after a timeout or broken connection. Keep its attempt ID, query recent tasks, and explain that the result is uncertain. Reusing an existing attempt ID is blocked locally. If the user explicitly confirms another draw, create a different attempt ID.

## Real-person authorization

1. If the user already authorized the person, query `real_human_groups_list` or `holycrab real-human groups list`. Follow pagination; select their public group ID. Ask which person when names are ambiguous.
2. To authorize a new person at the user's request, call `real_human_authorization_start` with their chosen display `name`, or `holycrab real-human start --name "Name"`, once. Show the returned `h5Link` and QR image privately to the user. MCP returns an image block; CLI returns an absolute `qrPath`. The link and QR are temporary verification credentials: do not publish them, send them to third-party QR services, or include them in logs. If QR generation fails, show the existing link without creating another session.
3. The person must open the link or scan the QR and complete verification themselves, including the final completion button that returns to the official callback page. Never simulate verification, submit callback results yourself, or ask for a `bytedToken` or a callback URL. The browser handles the callback; the local Agent does not need their web login.
4. Query `real_human_authorization_get` with `authorizationId`, or use `holycrab real-human get|wait AUTHORIZATION_ID`. For `CREATED`, wait about 5 seconds before the next query and keep waits bounded. A timeout means stop waiting and retain the ID, not start over. Stop on `SUCCEEDED`, `FAILED`, or `EXPIRED`. On failure/expiry, explain the result and only start a new session when the user asks to retry. A successful response includes `group.uniqId`.
5. Upload only the user-selected person's image/video to that group: `asset_upload` with `file` and `groupUniqId`, or `holycrab assets upload /absolute/path/reference.jpg --real-human-group GROUP_ID`. Omitting the group uses ordinary assets; never use that as a fallback for failed authorization. Use `real_human_assets_list` to select an existing asset or reconcile an uncertain upload.
6. Wait for `asset_get` to return `ready: true`; report `FAILED` and its public error. Use the returned public asset ID in the existing `imageAssetIds` or `videoAssetIds` request fields, never an upstream asset/group ID. Check the selected model's capabilities, estimate credit, and obtain the normal generation confirmation. Real-person verification does not authorize a paid generation.

Creation/registration errors can have an uncertain outcome, including gateway errors. Do not automatically recreate the authorization or reupload; keep any returned authorization/asset ID, query it, and explain what remains unknown. These tools require the corresponding production API and official callback page to be deployed. A missing route is not a reason to switch origins or invent another callback.

## Public boundary

- Return public model names, limits, credit estimates, task IDs, status, public errors, and final outputs.
- Never expose the API Key, authentication headers, local config contents, temporary upload URLs, or their query parameters.
- Do not use a generic raw-request path when a named CLI command or MCP tool exists.
- Do not invent output URLs or retry a paid operation during polling.

The bundled [public capability manifest](references/capabilities.json) is the release snapshot for discovery. Prefer the installed CLI/MCP response because it identifies the snapshot version in its output.
