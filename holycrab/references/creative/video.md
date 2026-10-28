# Video production
Use for product ads, social clips, image animation, scripts and multi-shot videos. Read execution.md before any API call and workflows.md for multiple tasks.

Understand the user's result: product/message, audience, placement, length, aspect ratio, materials, sound and budget if supplied. Ask only for missing decisions that change the result. Preserve explicit choices, including model, silence, identity and only-planning requests. Do not ask the user to write prompts.

For a short request supported by a single model call, use one generation. For a real multi-shot production, create a compact storyboard with visual, action, duration and sound for each shot. Use a shared identity/style description and selected reference assets across shots. An ad can use hook, evidence/use, benefit, call to action; a story can use setup, action, resolution. Choose only the beats the brief needs. Do not invent product claims, testimonials or on-screen UI.

Assign each selected material a role: identity/product, environment, composition, motion, camera, voice. Clearly describe what to borrow and what to exclude. Use images for identity, text for camera direction, and motion references only when useful. Keep asset order and prompt labels consistent with the chosen model's guide and actual request. Never use a filename as a supported reference identifier unless the capability contract says so.

Query capabilities before choosing a model. Respect a named model; otherwise select from supported task types, input modes, length, aspect ratio, requested audio and current account permissions. Explain the selected model and quoted cost, not a claim that it is universally best. Load seedance.md or h3.md for those model families. Other families use their own schemas, never renamed Seedance fields. Unknown metadata remains unknown; service validation decides admissibility.

Generate additional reference images only when needed and approved. Do not create an image for every shot by default. When the request needs multiple assets, use the workflow graph in workflows.md. Voiceover is a separate audio task when needed; music is an existing user-selected file unless a supported model can satisfy the requested sound within video generation. For exact subtitles or logo typography prefer local composition over model-rendered text.

Review a short storyboard and the executable estimates, upload only confirmed selected files, then submit approved tasks. Query and download completed outputs. Use composition.md to deliver one final file when required. Return the actual local file, which parts completed and any remaining limitations. Do not call separate clips a finished edit.

Example: “Use this product photo for a 15-second vertical ad, no voiceover.” Keep the product appearance, vertical framing and no voiceover; choose one supported generation when sufficient. “Only write the storyboard” ends at the storyboard with no upload or generation.
