# Persistent production workflows
Use workflows for multiple generations, variants, generated reference images, downloads, uploads and final edits. The host writes a JSON plan; the CLI validates and executes it, without calling an LLM.

Plan: {"title":"Product ad", "brief":"approved user intent", "style":"shared visual identity", "steps":[...]}. Each step has id, kind, request and optional dependsOn. Generation kinds are image/video/audio; local pipeline kinds are download/upload/compose. Keep IDs stable across revisions. A dependency value is {"fromStep":"step-id","output":"taskId"} (also output, assetIds, files); an optional index selects an array element. Dependencies must point to a completed predecessor; no fabricated IDs.

Example sequence: image generation -> download {taskId: reference, output: absolute path} -> upload {files:[reference to downloaded output]} -> video {imageAssetIds: reference to upload assetIds} -> download -> compose. Use an explicit download per selected result. For a directly supplied local file start with upload; already usable public asset IDs can go straight into generation.

`workflows prepare --json @plan.json` creates a local workflow without sending requests. `workflows estimate ID` resolves currently ready steps, returns exact request previews, upload file lists, local-output previews and actual credit estimates with approvalId. Its total covers only ready steps; blocked dependencies are listed, never silently priced at zero. Show the entire batch before approval. `workflows run-next ID --approval-id ID --yes` executes one approved ready step. MCP workflow_run_next additionally requires confirmed=true. The same approval may cover the exact remaining ready steps shown in that batch, but cannot cover future dependent steps or revisions. Re-estimate when inputs become available. An upload completion can still require asset processing before dependent generation.

`workflows list` (MCP `workflow_list`) finds recent local projects by title, with limit/offset pagination. Use it when resuming without a known workflow ID.

`workflows get ID --refresh` reconciles existing remote tasks and retained attempts. It never creates a generation. Keep polling bounded and inform the user of meaningful progress. Partial/failed/unknown states are not success. A background run is not implied; the host calls the next step while active.

`workflows prepare --workflow-id ID --expected-revision N --json @plan.json` replaces the plan under a revision check. Unchanged completed steps are reused; changes invalidate dependent steps and existing approvals. Do not edit running steps. For a user-approved new draw, `rerunStepIds` in the plan invalidates that step and its descendants; the next estimate highlights any uncertain prior attempt and requires approval of the duplicate risk. Old outputs and attempts remain in history.

Account identity is bound when remote work first starts; another credential cannot silently resume it. State is local to this computer, not a server project or global idempotency guarantee. Do not delete records to retry. If local persistence fails after a submission, retain the IDs returned to the user and reconcile them.

Before a multi-step plan starts, explain any future estimates and required materials. An approved plan is not approval of unspecified future spending. Clip generation alone is not final composition; include a compose step when one finished video was requested.

User consent and the runtime approvalId are separate: an explicit task/budget authorization can cover later steps, but each newly ready batch still needs a fresh estimate and matching approvalId. Reuse covered user consent instead of asking again. Report only current progress and the next needed action; keep request hashes, old revisions and routine task IDs internal. CLI terminal output is concise; `--json-output` exposes complete records for inspection, and piped output remains JSON.
