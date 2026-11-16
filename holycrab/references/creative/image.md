# Product and creative images
Use for product listing images, lifestyle scenes, campaign visuals and reference frames. Read execution.md first.

Identify the deliverable, dimensions/format if specified, background, product identity and text that must be exact. Use the user's selected photos as identity references. Keep distinguishing proportions, labels and materials; ask when a visible product detail cannot be inferred. Never invent specifications or endorsements.

Separate subject, composition, environment, light, style and required exclusions. Add only details that improve the brief, not generic quality adjectives. Query the selected model schema and accepted reference fields; do not copy model limits from a prompt guide. Respect imageAssetIds versus imageUrls precedence. The service owns material validation.

For one image, estimate and submit one confirmed task. For variants, describe how they differ (background, composition or audience) and create one explicit workflow step per requested task with workflows.md. Do not submit extra candidates to improve your confidence without approval. For reference images used downstream, download then upload through the confirmed workflow and wait for processing before the video task.

Deliver completed images with file paths, a short description of each version, and any obvious uncertainty. If the agent can view images, inspect identity, requested text and composition; do not claim visual review otherwise. Revisions create separately approved tasks and preserve previous results.

Example: “Make a clean product listing photo from this bottle.” Preserve the bottle and label, clarify an essential output requirement only if missing, and use a supported image request. Asking for a prompt alone must not create an image.

MiniMax H3 Image and Highspeed support up to 16 owned references and n=1–10 images per task. Default to n=1; use either a multi-image task or separate variant steps without multiplying both. Obtain the model's estimate, and download each completed image by its output index. Use model-specific asset checks; Ark review is not required for MiniMax.

## Seedream 5.5 (CLI 0.7.4)

Choose `dola-seedream-5-5-261028` for 1K/1.5K/2K, PNG by default or JPEG, one image per request. Custom output spans 921,600–4,624,220 pixels, ratio 1:16–16:1, no multiple-of-16 requirement. Use at most 10 reference images via IDs OR URLs; never mix them. Check capabilities and the service quote before executing. Other Seedream models keep their own defaults and limits.
