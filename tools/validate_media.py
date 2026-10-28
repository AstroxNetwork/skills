#!/usr/bin/env python3
"""Real offline FFmpeg acceptance; synthetic clips, no API calls or credits."""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "holycrab/scripts"))
from holycrab_runtime import media, workflows


def validate(parent):
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="run-", dir=parent)).resolve()
    os.environ["HOLYCRAB_CONFIG_DIR"] = str(root / "state")
    os.environ["HOLYCRAB_NO_UPDATE_CHECK"] = "1"
    health = media.doctor()
    if not health["ok"]: raise ValueError(health["errors"])
    ffmpeg = health["tools"]["ffmpeg"]["path"]
    fixtures = {
        "landscape.mp4": ["-f", "lavfi", "-i", "color=c=red:s=320x240:d=2", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-c:a", "aac"],
        "portrait.mp4": ["-f", "lavfi", "-i", "color=c=blue:s=240x320:d=3", "-c:v", "libx264"],
        "voice.wav": ["-f", "lavfi", "-i", "sine=frequency=600:duration=1.5"],
        "music.wav": ["-f", "lavfi", "-i", "sine=frequency=220:duration=0.3"],
    }
    for name, arguments in fixtures.items():
        media.run([ffmpeg, "-hide_banner", "-loglevel", "error", *arguments, str(root / name)])
    (root / "import.srt").write_text("1\n00:00:00,100 --> 00:00:01,800\n导入字幕 / Imported caption\n")
    (root / "import.ass").write_text("""[Script Info]
ScriptType: v4.00+
PlayResX: 640
PlayResY: 360
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,24,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,16,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.10,0:00:01.80,Default,,0,0,0,,ASS 字幕 / Caption
""")
    base = {"clips": [{"path": str(root / "landscape.mp4"), "duration": 1.2},
                      {"path": str(root / "portrait.mp4"), "start": 0.2, "duration": 1.0}],
            "width": 640, "height": 360, "fps": 10}
    outputs, checks = [], []
    for name, options in (
        ("captions-music", {"music": str(root / "music.wav"), "captions": [{"start": 0, "end": 1.2, "text": "第一镜头 / Scene one"}, {"start": 1.2, "end": 2.2, "text": "第二镜头 / Scene two"}]}),
        ("cover-muted", {"fit": "cover", "mute": True}),
        ("narration-srt", {"narration": str(root / "voice.wav"), "music": str(root / "music.wav"), "subtitles": str(root / "import.srt")}),
        ("imported-ass", {"subtitles": str(root / "import.ass")}),
    ):
        request = {**base, **options, "output": str(root / (name + ".mp4"))}
        preview = media.preview(request)
        result = media.compose(request, confirmed=True, approval_hash=preview["approvalHash"])
        assert result["verified"] and result["hasAudio"] is not options.get("mute", False)
        outputs.append(result); checks.append(name)
    media.run([ffmpeg, "-loglevel", "error", "-ss", "0.5", "-i", outputs[0]["output"], "-frames:v", "1", str(root / "captions.png")])
    def rejects(label, function):
        try: function()
        except (ValueError, OSError): checks.append(label)
        else: raise AssertionError(label + " was not rejected")
    rejects("existing-output-preserved", lambda: media.preview({**base, "output": outputs[0]["output"]}))
    assert media.sha256(outputs[0]["output"]) == outputs[0]["sha256"]
    rejects("network-input-rejected", lambda: media.local_file("https://example.com/video.mp4"))
    request = {**base, "output": str(root / "not-approved.mp4")}
    rejects("unapproved-render-rejected", lambda: media.compose(request, confirmed=True))
    preview = media.preview(request)
    (root / "landscape.mp4").write_bytes((root / "landscape.mp4").read_bytes() + b"changed")
    rejects("changed-input-rejected", lambda: media.compose(request, confirmed=True, approval_hash=preview["approvalHash"]))
    assert not (root / "not-approved.mp4").exists()
    request = {**base, "output": str(root / "workflow.mp4"), "mute": True}
    flow = workflows.prepare({"title": "Offline local edit", "steps": [{"id": "edit", "kind": "compose", "request": request}]})
    estimate = workflows.estimate(flow["workflowId"])
    assert estimate["readyCredits"] == 0
    result = workflows.run_next(flow["workflowId"], approval_id=estimate["approvalId"], confirmed=True)
    assert result["state"] == "succeeded"
    checksum = media.sha256(root / "workflow.mp4")
    workflows.run_next(flow["workflowId"], approval_id=estimate["approvalId"], confirmed=True)
    assert media.sha256(root / "workflow.mp4") == checksum
    checks.append("workflow-composition-and-resume-without-rerender")
    report = {"status": "passed", "scope": "offline-synthetic-media", "checks": checks, "outputs": outputs, "tools": health["tools"], "directory": str(root)}
    (root / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "build/media-validation")
    report = validate(parser.parse_args().output)
    print(json.dumps({"status": report["status"], "checks": len(report["checks"]), "report": report["directory"] + "/report.json"}))
