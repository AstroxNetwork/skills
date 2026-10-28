# HolyCrab creative Skills — 0.7.0

This version adds creative guidance and resumable local workflows. Generation continues to use the existing HolyCrab API contract; no backend change is required.

## User entry points

- Install the CLI for Codex. Its `holycrab` Skill automatically routes video, image, voiceover and editing requests to the relevant bundled guide. Reopen the Codex chat after installation.
- MCP-only access uses `skills_list` and `skill_get` to read the same versioned content on demand.
- The website offers `/skills/`, `/cn/skills/`, `/tw/skills/`, `/hk/skills/` and `/ja/skills/`, with a full CLI archive, a recommended core Skill and five focused Skills. The Skill archive contains one folder and all its references, not another CLI runtime or credentials.
- For local installation: extract `holycrab-cli-local.zip`; in the extracted directory run `HOLYCRAB_INSTALL_SOURCE_DIR="$PWD" HOLYCRAB_INSTALL_AGENTS=codex sh install.sh`. The public stable installer follows the latest verified release.

Examples: “Make a vertical product ad from this photo”; “Give me three listing-image variants”; “Only revise the second shot”; “Combine these clips with my music and subtitles.” Prompt/storyboard-only requests stop before uploads and paid operations.

## Commands and tools

| CLI | MCP | Purpose |
| --- | --- | --- |
| `holycrab skills list` | `skills_list` | List five creative guides |
| `holycrab skills get holycrab-video` | `skill_get` | Read a guide; use `--resource` for a listed reference |
| `holycrab skills doctor` | `cli_status` | Check bundled files; host discovery is verified separately |
| `holycrab workflows prepare --json @plan.json` | `workflow_prepare` | Save a plan without generating |
| `holycrab workflows list` | `workflow_list` | Find recent projects by title |
| `holycrab workflows get ID --refresh` | `workflow_get` | Query existing tasks/assets and recover a saved attempt |
| `holycrab workflows estimate ID` | `workflow_estimate` | Quote currently ready steps; deferred costs are excluded |
| `holycrab workflows run-next ID --approval-id APPROVAL --yes` | `workflow_run_next` | Execute exactly one approved ready step |
| `holycrab download TASK --output FILE` | `generation_download` | Download an existing output |
| `holycrab media doctor` | `media_doctor` | Check FFmpeg, FFprobe, encoders and subtitles |
| `holycrab media compose --json @edit.json` | `media_compose` | Preview a local edit; then confirm its approval hash |

To revise: `workflows prepare --workflow-id ID --expected-revision N --json @revised.json`. Stable step IDs preserve unaffected outputs. A changed step invalidates its dependants. `rerunStepIds` explicitly requests new draws; uncertain prior submissions expose duplicate risk. Running steps must be reconciled first. History retains prior task IDs and outputs.

Read [the plan schema](holycrab/references/creative/workflows.md) and [composition options](holycrab/references/creative/composition.md). Generation request fields always come from `models show` / `capability_get`, including material constraints and model-specific fields.

Terminal progress shows the current state and next action. Add `--json-output` to any `skills`, `workflows` or `media` command for full details; piped output is always JSON. Exact ready requests and selected upload files remain visible before approval. Agent replies normally use 1–3 short sentences and retain explicit consent for the selected task and budget. Account credits and API Key quota are distinct; a quota rejection requires increasing the Key quota, not revising the creative prompt.

## Recovery and costs

A plan is local state under the configured HolyCrab `workflows` directory. Remote steps bind to the current credential. Each generation reserves its own attempt before submission. Unknown responses stop progression and are never automatically submitted again. Query existing IDs; if the user explicitly wants another draw, revise and re-estimate it. Approval expires after 15 minutes or a plan revision, and the generation API re-estimates each selected request before sending it. Estimates cover only ready steps, not future dependencies.

Uploads retain the complete preview and their one-shot upload plan. An incomplete upload never unlocks dependent generation. Asset processing readiness is queried independently. Final edits freeze approved input bytes, normalize clips, mix or mute audio, render subtitles and verify the final MP4 before atomic delivery. A file or tool change invalidates the approval. Existing output requires explicit `force=true`.

Subtitles accept SRT/ASS or segment times. No ASR or word alignment is supplied. Local FFmpeg/FFprobe are user-installed dependencies; they are not shipped or silently installed. `font` and `fontFamily` select a local subtitle font when needed. Generated references, extra variants and voiceovers require their own approved costs.

## Ownership and packaging

The installer hashes creative resources and installs one default Skill. Upgrades refuse customized or unowned target files, preserve unrelated files, and roll back program and Skill bytes on failure. Default uninstall preserves project records. Explicit purge removes recognized workflow records but never source clips or exported media. Optional Skills are independently installed and remain user-owned.

Run `python3 tools/refresh_installer_hashes.py`, then `python3 tools/build_skills.py --website /path/to/site`. ZIP ordering, timestamps and package checksums are deterministic. The website catalog and downloadable files come from the CLI source. Both native Skill access and MCP resource reads share those same guides.
