# Seedance prompt adaptation
Use the selected Seedance capability for request fields, task types, duration, resolution, references and audio flags. The accompanying upstream guides are creative references; their platform limits and prices are not the HolyCrab API contract. User constraints and current CLI capabilities take precedence.

Structure the scene as subject -> concrete action -> environment -> visual treatment -> camera -> requested audio. Keep parameters outside the prompt. Identify the role of every selected image/video/audio reference, and preserve their order. For multiple subjects or motion references read seedance/multi-reference.md. For multiple beats read seedance/long-video-and-timing.md. For edit/extend read seedance/editing-and-extension.md only if the selected capability supports that task. For keyframes read seedance/advanced-techniques.md. Never promise a task type merely because it appears in a creative guide.

For API execution read execution.md and workflows.md. For prompt-only requests return a usable prompt and assumptions without account queries, upload or generation. Do not turn a request for one short clip into unnecessary reference generation or a longer production.

## Model-specific execution limits (CLI 0.7.4)

Seedance 2.5 flagship supports 480p/720p/1080p/4k, up to 30 images, 10 videos and 10 audio clips (50 total). Flash supports only 480p/720p and up to 10 images, 5 videos and 5 audio clips (20 total). Both support reference/frames/edit/extend; edit uses duration -1, other modes 4–30 seconds. Flash supports no draft. Flagship drafts are 480p and can produce one 1080p or 4k final. Query an existing final before conversion; a different or unknown resolution is a stop, not permission to create another final. Read the installed capability for all other limits and quote the exact request.
