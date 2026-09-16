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
3. For local media, MCP must call `asset_upload_prepare`, explain the complete target and file list in the user's current language, obtain one explicit confirmation, then call `asset_upload_execute` with the returned `uploadPlanId` and `confirmed: true`. The old `asset_upload` tool never uploads. CLI `holycrab assets upload FILE [FILE ...]` performs the same preview and one-confirmation flow. Query `asset_get` or `holycrab assets get|wait` until `ready: true` (`step: UPLOADED_TO_ARK`); upload registration alone is not readiness.
4. Call `generation_estimate` or `holycrab generate estimate`. Tell the user the model, output specification, duration, and estimated credit.
5. Wait for explicit confirmation. Create one new stable `attemptId` for that draw, then call `generation_create` once with `confirmed: true` and that ID, or run `holycrab generate create ... --yes` once.
6. Save the returned task ID. Query it with `generation_get`, `generation_list`, or `holycrab tasks get|list|wait`.

Before each operation, explain what you are about to do in the user's current language. Afterward, explain whether it succeeded, failed, was cancelled, timed out, or has an uncertain outcome. If `nextAction` applies, explain it and show its command only when the command is not null. If it is null, ask for the missing user choice; never invent a path, ID, request, or command. Completed balance/model queries need no extra steps. Do not dump raw JSON without an explanation to a non-technical user. Ctrl+C stops local waiting, not the online task; reconcile interrupted writes instead of repeating them.

## Submission rule

A user may intentionally repeat the same prompt to draw another result. Each explicit confirmation is a new submission and may create a new billable task.

Do not retry the same submission after a timeout, HTTP 408/5xx, malformed success response, or missing task ID. Keep its attempt ID, query `generation_attempt_get` / `holycrab generate attempts get`, then query recent tasks. Explain that finding no task still cannot prove that the service created nothing. Reusing an existing attempt ID is blocked locally. If the user explicitly confirms another draw, create a different attempt ID. This local protection cannot prevent duplicates created from another computer, after deleting local records, or by bypassing the CLI.

## Real-person authorization

1. If the user already authorized the person, query `real_human_groups_list` or `holycrab real-human groups list`. Follow pagination; select their public group ID. Ask which person when names are ambiguous.
2. To authorize a new person at the user's request, call `real_human_authorization_start` with their chosen display `name`, or `holycrab real-human start --name "Name"`, once. Show the returned `h5Link` and QR image privately to the user. MCP returns an image block; CLI returns an absolute `qrPath`. The link and QR are temporary verification credentials: do not publish them, send them to third-party QR services, or include them in logs. If QR generation fails, show the existing link without creating another session.
3. The person must open the link or scan the QR and complete verification themselves, including the final completion button that returns to the official callback page. Never simulate verification, submit callback results yourself, or ask for a `bytedToken` or a callback URL. The browser handles the callback; the local Agent does not need their web login.
4. Query `real_human_authorization_get` with `authorizationId`, or use `holycrab real-human get|wait AUTHORIZATION_ID`. For `CREATED`, wait about 5 seconds before the next query and keep waits bounded. A timeout means stop waiting and retain the ID, not start over. Stop on `SUCCEEDED`, `FAILED`, or `EXPIRED`. On failure/expiry, explain the result and only start a new session when the user asks to retry. On success, explain that the person group has been created but authorization does not create or upload any assets. Continue by asking the user to select files for that person, then preview and confirm their upload separately. Use the returned `group.uniqId` internally; do not ask the user to guess it.
5. Prepare only the user-selected person's files with `asset_upload_prepare` (`files` plus `groupUniqId`), or `holycrab assets upload FILE [FILE ...] --real-human-group GROUP_ID`. Show the person's name, group ID, every resolved path, type, size, and known duration. Obtain one confirmation for the complete batch, then call `asset_upload_execute` with `confirmed: true`. Omitting the group uses ordinary assets; never use that as a fallback for failed authorization. Use `real_human_assets_list` to select an existing asset or reconcile an uncertain upload.
6. Wait for `asset_get` to return `ready: true`; report `FAILED` and its public error. Use the returned public asset ID in the existing `imageAssetIds` or `videoAssetIds` request fields, never an upstream asset/group ID. Check the selected model's capabilities, estimate credit, and obtain the normal generation confirmation. Real-person verification does not authorize a paid generation.

### Manage authorized people and assets

- Rename a selected public group with `real_human_group_rename` or `holycrab real-human groups rename GROUP_ID --name "Name"`. Return only the public group fields.
- Deleting is permanent. `real_human_group_delete` removes the selected person, every asset in that group, and upstream records. `real_human_asset_delete` removes the selected asset from storage, upstream records, and the database.
- Before any delete, identify the exact target using the list tools and show the person or asset name plus the group asset count when available. Obtain an explicit user request for that exact deletion. Only then set MCP `confirmed: true` or use CLI `--yes`; never infer confirmation from authorization, upload, or generation consent.
- `holycrab uninstall` is a local machine operation, not an MCP tool. Never invoke it unless the user explicitly asks to uninstall HolyCrab. Explain in the user's current language that the default preserves local credentials and records, while `--purge` removes known local state but does not revoke the server-side API Key; obtain explicit approval for the exact mode before using `--yes`.
- If the user declines or confirmation is absent, make no delete request. If PATCH or DELETE times out, disconnects, returns 5xx, or returns malformed success data, do not retry. Query the relevant lists/details to reconcile state and report that the result is uncertain.
- Use only public `groupUniqId` and asset `uniqId`. Authorization revocation and automatic retention cleanup are unavailable; do not invent them.

Creation/registration errors can have an uncertain outcome, including gateway errors. Do not automatically recreate the authorization or reupload; keep any returned authorization/asset ID, query it, and explain what remains unknown. These tools require the corresponding production API and official callback page to be deployed. A missing route is not a reason to switch origins or invent another callback.

Real-person uploads accept JPG/JPEG, PNG, WEBP, GIF, HEIC, MP4, and MOV only; audio is rejected. Images must be below 30 MiB and videos at most 50 MiB. Local checks do not guarantee dimensions, ratio, frame rate, duration, or codec acceptance; those remain online checks. A batch contains at most 10 files, stops at the first failed or uncertain item, and reports `uploaded`, `failedOrUnknown`, and `notAttempted` separately. Never execute the same upload plan twice or retry an uncertain item automatically.

## Public boundary

- Return public model names, limits, credit estimates, task IDs, status, public errors, and final outputs.
- Never expose the API Key, authentication headers, local config contents, temporary upload URLs, or their query parameters.
- Do not use a generic raw-request path when a named CLI command or MCP tool exists.
- Do not invent output URLs or retry a paid operation during polling.

The bundled [public capability manifest](references/capabilities.json) is the release snapshot for discovery. Prefer the installed CLI/MCP response because it identifies the snapshot version in its output.
