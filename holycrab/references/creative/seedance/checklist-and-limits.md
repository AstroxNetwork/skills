# Pre-Submission Checklist and Limitations

## Checklist

Before handing back a finished prompt, check it against these — most apply only if the relevant technique was used:

- Does the prompt clearly state the subject and primary action or event?
- Does every reference material state what to use and what *not* to use?
- Is every distinct character, product, and prop named and bound to a specific reference?
- Are references selected per scene, rather than all required to appear at once?
- Does each stage of a long video contain only one primary change and a clear end state?
- Do character count, clothing, prop ownership, and spatial relationships stay consistent throughout?
- For video editing: does the prompt define the sole editing master, edit scope, target quantity, and content to preserve?
- Are abstract emotions and niche cinematography terms paired with directly visible/audible cues?
- For first/last frames and keyframes: does each image have exactly one defined role, and do the first/last images share an aspect ratio?
- For storyboards: does the prompt state which structure to inherit (not literal panel reproduction)?
- For blockouts: did you identify coarse vs. fine first, and specify what temporal/structural/material/style info to inherit?
- Do editing, first/last-frame generation, and extension prompts respect their auto-locked aspect-ratio/duration rules (see `editing-and-extension.md`)?
- For extension: did you check the boundary frame, motion trend, and audio continuity?
- For one-click video: are material roles, image order, motion amount, editing style, and audio all defined?
- For seamless transitions: are both videos' roles, the trigger action, transition process, and arrival state all defined?

## Hard limitations — don't overpromise these

- **Timestamps allocate time, they aren't frame-accurate edit points.** Don't imply the model will hit a beat at the exact frame.
- Video-editing prompts increase the *probability* critical events align with the source — they can't guarantee frame-by-frame overlap.
- Multi-reference creation is about selecting/combining the right materials per scene, not making every material appear simultaneously.
- For subtitles, formulas, signs, product specs, or anything needing frame-level accuracy: recommend prepared reference materials plus post-production — the prompt alone can't guarantee it.
- Video editing auto-locks the input's aspect ratio and approximate duration (output may differ by up to ~0.3s); neither is separately settable.
- First-frame / first-and-last-frame generation locks aspect ratio to the first image; duration is settable. Mismatched first/last ratios can stretch the last frame.
- Video extension locks aspect ratio to the source; duration is settable. Extended-segment audio volume may differ slightly from the source.
- One-click video: if image order or character mapping matters, it must be stated explicitly — the model won't infer it.
- Seamless transitions aim for visual/audio continuity, not pixel-identical preservation of both source videos.

## Reference material technical limits (official BytePlus specs, 2026-08)

| Type | Hard limit | Recommended range |
|---|---|---|
| Images | 0-30, each ≤4K and ≤30 MB (jpeg/png/webp/bmp/tiff/gif/heic/heif) | 1-8 distinct subjects |
| Videos | 0-10, 480p-4K, ≤200 MB each, 2-30s per clip, combined ≤30s (mp4/mov) | 1-5 subjects, 5-10s per clip |
| Audio | 0-10 clips, ≤15 MB each, 2-30s per clip, combined ≤30s (mp3/wav) | Only clips directly relevant to the task |
| Video editing source | 1 video + optional reference images | Source ≤20s, 1-5 reference images |

Video and audio totals are counted **separately** — each modality can reach 30s. Audio-only reference is supported (new in 2.5); all seven modality combinations work (image / video / audio alone or in any combination).

Asset-mix guidance (official): when the main references are video/audio, keep subjects ≤5 (6-10 testable); when images, keep subjects ≤8 (9-12 testable). With ≤5 subjects, multi-view references are fine; above 5, use single views — and always separate view images rather than collages. Clip length 5-10s is the recognition sweet spot (~2s too little signal, ~30s dilutes traits). Prioritize assets: **core characters > key products/props > scene environment > overall style**.

Duration: any integer 4-30s (smart duration supported), 24 FPS, output MP4 or MOV (MOV better preserves color/brightness/AV consistency for extension and editing chains). Single extension: up to 30s; nested extensions: up to 60s total. Ultra Long Video mode: up to 180s. Output resolution: 480p or 720p.

Aspect ratios: 21:9, 16:9, 4:3, 1:1, 3:4, 9:16, plus Smart Aspect Ratio; unlike 2.0's six fixed ratios, any output ratio in [0.4, 2.5] is achievable by controlling input assets. Editing/extension/first-frame tasks force Smart (inherit the input's ratio). Timestamps: 2.5 honors integer-second timestamps (2.0 only responds to shot numbers).
