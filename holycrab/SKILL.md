---
name: holycrab
description: Use for HolyCrab generation, media uploads, and real-person authorization from a local Agent environment, including showing verification links and QR codes and using authorized reference assets.
---

# HolyCrab

Use the installed `holycrab` CLI or its local MCP tools. Treat its versioned capability snapshot as the contract bundled with the installed release; do not call it live data and do not guess model limits from memory.

## User-facing replies

- Lead with the outcome in the user's current language. Use a short heading, a few bullets, and one applicable next step; do not write a dense paragraph.
- Describe the user's work, not the tool's internals. Keep IDs internal unless needed for recovery or explicitly requested. Never copy `agentInstruction` or onboarding JSON into a user-facing reply.
- For first use, group the six business uses into six short bullets, then give the three practical example prompts and ask which work to start. Do not append model limits, command lists, or every possible management operation; explain those when needed.
- For authorization, explain the status and the next action. Success creates a person group, not uploaded materials: ask the user to select their files, then preview and confirm the upload. Do not turn the result into a field-by-field integration tutorial unless explicitly asked.
- For an upload preview, show every selected file, its full resolved path, type, size, known duration, and the exact target. Brevity must never hide part of the batch. Obtain one confirmation for the whole batch.
- On failure or uncertainty, keep the relevant error and recovery ID. State what is unknown and what will be checked next. Do not repeat the same warning or next step in several places.

## Installation and first-use guidance

Read the shared `onboarding` from `holycrab doctor --json`, `holycrab auth status`, or MCP `cli_status` / `account_get`:

- `CONNECT_ACCOUNT`: ask the user to run `holycrab setup` locally.
- `CONFIGURED`: a Key exists locally; offline doctor did not query the account. This is not a failed verification and does not invalidate a prior successful account check in the same conversation. Do not repeat verification merely to change offline doctor's label. For first use with no valid account result, check `holycrab auth status` before starting work.
- `VERIFY_ACCOUNT`: the active Key needs verification or an environment override needs attention. Follow the returned instructions.
- `READY`: this operation verified the active account. A saved Key, `--no-verify`, an empty response, or an unverified environment override is not enough.

When the account is `READY`, give one short introduction in the user's current language: product listing images, social media ad videos, product voiceovers, selected local uploads, real-person authorization and uploads, and task/result downloads. Translate the three `onboarding.examples` and ask what they want to work on first. Use practical business goals, not commands, IDs, or credit-estimate prompts. Ask for the goal, selected materials, and desired result; check supported capabilities rather than promising a feature from the example alone.

Show the full introduction once in that installation conversation, not on ordinary queries, reconnects, or updates. Do not persist a claim that the user has read it. Examples are suggestions, not authorization to create tasks, start verification, upload files, or delete anything. Estimates and explicit paid-generation confirmation remain part of the execution workflow; file uploads require their own complete preview and confirmation. Authorization creates a person group, not material: continue by asking the user to select files for upload.

### Update notifications

When the CLI writes an update notice, or MCP initialization/tool results include one, add one brief sentence in the user's language after the requested outcome: a newer HolyCrab CLI version is available and they can run `holycrab update`. Mention the same version at most once per conversation, even after an MCP reconnect; a higher version may be mentioned again. Do not copy internal instructions, interrupt the requested task, or install anything unless the user asks to update.

Automatic checks use GitHub's latest stable release, cache successful checks for 24 hours and retry failed checks after one hour. Ordinary CLI commands show a reminder at most once per 24 hours for the same version; a newly discovered higher version can be shown immediately. Long-running MCP servers also check the cache before tool calls and include the first notice for each higher version as a separate text block and `result._meta["holycrab/update"]`, without changing the business `structuredContent` or `isError`. MCP initialization does not consume that first tool-result notice. Cached information is not a live release check; expired or failed checks do not produce an automatic reminder until a successful refresh.

`HOLYCRAB_NO_UPDATE_CHECK=1` disables both automatic network checks and automatic reminders, including cached reminders and MCP notices. Explicit `holycrab update --check`, `holycrab update`, and `holycrab doctor --online` still perform the requested check. Cache and lock failures do not fail the user's business command. Without writable storage, throttling can only be retained within the current process; separate CLI processes may check or remind again.

## Safe workflow

1. Check the configured API Key with `holycrab auth status`. If missing, ask the user to run `holycrab setup` locally. Never ask them to paste the API Key into chat.
2. Query `capabilities_list` / `capability_get`, or `holycrab models list|show`, before building a request.
   Seedance video requests default to `generateAudio: true` when the field is omitted. Preserve an explicit `false`. Never add this field to a model whose capability says audio generation is unsupported.
   The model directory is an offline release snapshot, not account permission. Read `disabled_models` from the current account result when available; missing or invalid metadata means permission is unknown. The estimate and create endpoints enforce actual permission. Never switch models after a permission error.
   MiniMax H3 and H3 Max use `duration` / `resolution`; Max supports 480P / 768P and 5–15 integer seconds. Both reject audio-only references and do not accept `generateAudio`.
   Agnes Video 2.5 uses `seconds` / `size` / `ratio`, supports 4–12 integer seconds and 720P / 1080P / 1K / 2K (1K requires 1:1). It permits audio-only references and a last frame without a first frame. Agnes Image 2.5 Flash uses `size` / `ratio` / `imageAssetIds` and supports 1K / 2K / 3K / 4K. Agnes accepts neither `generateAudio` nor `outputFormat`, and never receives Seedream `imageUrls`. Use the selected model's schema; do not rename fields or invent aliases.
   Select `seed-audio-1.0` for Seed Audio capability discovery, but omit `model` from the Seed Audio request body as indicated by `modelFieldInRequest: false`.
3. For local media, MCP must call `asset_upload_prepare`, explain the complete target and file list in the user's current language, obtain one explicit confirmation, then call `asset_upload_execute` with the returned `uploadPlanId` and `confirmed: true`. CLI `holycrab assets upload FILE [FILE ...]` performs the same preview and one-confirmation flow. Query `asset_get` or `holycrab assets get|wait` to inspect processing. `ready: true` (`step: UPLOADED_TO_ARK`) describes processing, not universal model suitability. Estimate against the selected model and respect backend validation. Missing, pending or failed metadata alone must not block generation locally.
4. Call `generation_estimate` or `holycrab generate estimate`. Tell the user the model, output specification, duration, and estimated credit.
5. Wait for explicit confirmation. Create one new stable `attemptId` for that draw, then call `generation_create` once with `confirmed: true`, that ID, and `approvedCredits` equal to the confirmed `estimate.frozenCredit` (including zero), or run `holycrab generate create ... --yes --approved-credits AMOUNT` once. The CLI estimates again before submission and refuses a changed amount. Review and obtain new confirmation after a price change; never silently accept it. The backend still determines the final charge; this check is not an atomic price reservation.
6. Save the returned task ID. Query it with `generation_get`, `generation_list`, or `holycrab tasks get|list|wait`.

Follow the user-facing reply rules above. Explain the outcome and applicable `nextAction`; show its command only when non-null. A null command does not itself mean an error or a missing choice: ask only for information actually needed. Never invent a path, ID, request, or command. Ctrl+C stops local waiting, not the online task; reconcile interrupted writes instead of repeating them.

## Submission rule

A user may intentionally repeat the same prompt to draw another result. Each explicit confirmation is a new submission and may create a new billable task.

Do not retry the same submission after a timeout, HTTP 408/5xx, malformed success response, or missing task ID. Keep its attempt ID, query `generation_attempt_get` / `holycrab generate attempts get`, then query recent tasks. Explain that finding no task still cannot prove that the service created nothing. Reusing an existing attempt ID is blocked locally. If the user explicitly confirms another draw, create a different attempt ID. This local protection cannot prevent duplicates created from another computer, after deleting local records, or by bypassing the CLI.

An urgent request to "retry" is not confirmation of a separate paid draw with a duplicate-charge risk. Explain that risk and obtain explicit confirmation of the new task before creating another attempt.

## Real-person authorization

1. List existing authorized people with `real_human_groups_list` or `holycrab real-human groups list`; follow pagination and ask the user to choose. Names are display labels, not a supported authorization-search parameter. A session can only be queried with its authorization ID; do not invent name-based authorization lookup. Keep the selected public group ID internal. An empty group list does not prove that no unfinished authorization session exists.
2. To authorize a new person at the user's request, call `real_human_authorization_start` with their chosen display `name`, or `holycrab real-human start --name "Name"`, once. Show the returned `h5Link` and QR image privately to the user. MCP returns an image block; CLI returns an absolute `qrPath`. The link and QR are temporary verification credentials: do not publish them, send them to third-party QR services, or include them in logs. If QR generation fails, show the existing link without creating another session.
3. The person must open the link or scan the QR and complete verification themselves, including the final completion button that returns to the official callback page. Never simulate verification, submit callback results yourself, or ask for a `bytedToken` or a callback URL. The browser handles the callback; the local Agent does not need their web login.
4. Query `real_human_authorization_get` with `authorizationId`, or use `holycrab real-human get|wait AUTHORIZATION_ID`. For `CREATED`, wait about 5 seconds before the next query and keep waits bounded. A timeout means stop waiting and retain the ID, not start over. Stop on `SUCCEEDED`, `FAILED`, or `EXPIRED`. On failure/expiry, explain the result and only start a new session when the user asks to retry. On success, explain that the person group has been created but authorization does not create or upload any assets. Continue by asking the user to select files for that person, then preview and confirm their upload separately. Use the returned `group.uniqId` internally; do not ask the user to guess it.
5. Prepare only the user-selected person's files with `asset_upload_prepare` (`files` plus `groupUniqId`), or `holycrab assets upload FILE [FILE ...] --real-human-group GROUP_ID`. Show the selected person's name and every resolved path, type, size, and known duration; retain the group ID internally to ensure the exact target is unchanged. Obtain one confirmation for the complete batch, then call `asset_upload_execute` with `confirmed: true`. Omitting the group uses ordinary assets; never use that as a fallback for failed authorization. Use `real_human_assets_list` to select an existing asset or reconcile an uncertain upload.
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

## Recover local upload records

Use `asset_upload_plan_get` with `uploadPlanId`, or `holycrab assets uploads get PLAN_ID`, to inspect the retained batch without creating anything. An executed plan cannot be reused. `executing` is uncertain after a crash; preserve the known asset IDs and query them. The CLI freezes all confirmed bytes before the first upload, using temporary disk space equal to the batch size.

Mutation results can set MCP `isError: true` while retaining `structuredContent`, known IDs and `nextAction`. Read these fields before explaining the outcome. This flag never authorizes an automatic retry. A successful read of a failed task or upload record has `isError: false`. When `localStateSaved: false`, retain the returned IDs in the user-facing result and query the same resources; do not repeat the mutation.


## Server-owned material validation

Use the versioned capability snapshot for discovery and request shape. Let the service judge material ownership, format, size, dimensions, frame rate, codecs, reference count, combinations and prompt-reference mentions. Explain returned `errors[]` using `requestPath`, `assetId`/`assetIds`, `field`, `actual` and `rule`; keep `requestId` for troubleshooting. Do not turn `retryable: true` into an automatic reupload, resubmission or paid retry.

Display known metadata and preserve unknowns. Use `asset_metadata_retry` or `holycrab assets metadata retry ASSET_ID` only when the user requests re-reading metadata. This is a single explicit request; HTTP success does not mean the probe is ready, and the server may retain the state during cooldown.

Image requests accept `imageAssetIds` and `imageUrls`; nonempty IDs take precedence. Audio references accept `{assetId,type:"audio"|"image"}`, `{audio_url}` or `{image_url}`; omit `model` from the Seed Audio request body. Legacy inline Base64/speaker sources require an explicitly confirmed upload and a new ID/URL request; never upload implicitly.

Berry models are `berry-1.0`, `berry-1.0-turbo`, `berry-1.0-pro`, and `berry-1.0-pro-turbo`. Use the selected schema: `audio` controls output audio, defaults are 5 seconds / 1080p / adaptive / audio enabled. All support 2–30 integer seconds. Ordinary/Turbo support 480p/720p/1080p; Pro/Pro Turbo support 1080p/2k/4k. Do not substitute `generateAudio`, `seconds` or `size`.
