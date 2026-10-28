"""Approved local video editing. No generation or media network requests."""
from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

FORMATS = "mov,matroska,avi,wav,mp3,flac,ogg,aac"
MEDIA_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command, *, cwd=None, timeout=120):
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("Local media tool unavailable or timed out: " + str(error)) from error
    if "failed to find any fallback with glyph" in result.stderr:
        raise ValueError("Subtitle font is missing required characters; select a font and fontFamily that cover your text")
    if result.returncode:
        raise ValueError("Local media command failed: " + result.stderr[-3000:])
    return result.stdout


def executable(name):
    configured = os.environ.get("HOLYCRAB_" + name.upper(), name)
    found = shutil.which(configured)
    if not found:
        raise ValueError(f"Install {name} locally or set HOLYCRAB_{name.upper()} to its executable")
    return str(Path(found).resolve())


def doctor():
    tools, errors, diagnostics = {}, [], {}
    for name in ("ffmpeg", "ffprobe"):
        try:
            path = executable(name)
            version = run([path, "-version"], timeout=15).splitlines()[0]
            tools[name] = {"path": path, "version": version, "sha256": sha256(path)}
        except ValueError as error:
            errors.append(f"{name} is unavailable. Reinstall it or set HOLYCRAB_{name.upper()} to a working executable.")
            diagnostics[name] = str(error)
    if "ffmpeg" in tools:
        try:
            filters = run([tools["ffmpeg"]["path"], "-hide_banner", "-filters"], timeout=15)
            encoders = run([tools["ffmpeg"]["path"], "-hide_banner", "-encoders"], timeout=15)
            if " subtitles " not in filters or "libx264" not in encoders or " aac " not in encoders:
                errors.append("FFmpeg requires the subtitles filter, libx264 and AAC encoders")
        except ValueError as error:
            errors.append("FFmpeg capability check failed. Repair the local installation.")
            diagnostics["ffmpegCapabilities"] = str(error)
    return {"ok": not errors, "tools": tools, "errors": errors, "diagnostics": diagnostics,
            "instruction": "Install missing tools using your chosen local package source; HolyCrab does not install them automatically."}


def local_file(value, *, subtitle=False, font=False):
    if not isinstance(value, str) or not value or "://" in value or "\x00" in value:
        raise ValueError("Select a local file path")
    path = Path(value).expanduser().resolve(strict=True)
    allowed = {".ttf", ".otf", ".ttc"} if font else {".srt", ".ass"} if subtitle else MEDIA_EXTENSIONS
    if not path.is_file() or path.suffix.lower() not in allowed or path.stat().st_size == 0:
        raise ValueError("Unsupported or empty local media file: " + str(path))
    return path


def output_path(value, *, force=False):
    if type(force) is not bool or not isinstance(value, str) or not value or "://" in value or "\x00" in value:
        raise ValueError("Select an output path and a boolean force option")
    original = Path(value).expanduser()
    if original.is_symlink():
        raise ValueError("Output must not be a symbolic link")
    path = original.resolve()
    if path.exists() and (not force or not path.is_file()):
        raise ValueError("Output already exists; choose another path or explicitly approve force=true")
    return str(path)


def probe(path, tool):
    value = json.loads(run([tool, "-v", "error", "-protocol_whitelist", "file",
        "-format_whitelist", FORMATS, "-show_streams", "-show_format", "-of", "json", str(path)]))
    duration = float(value.get("format", {}).get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Media has no finite positive duration")
    return {"duration": duration, "streams": value.get("streams", [])}


def number(value, name, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be a number between {low} and {high}")
    return value


def preview(request):
    allowed = {"clips", "output", "width", "height", "fps", "fit", "mute", "narration", "music",
               "musicVolume", "sourceVolume", "subtitles", "captions", "force", "font", "fontFamily"}
    if not isinstance(request, dict) or set(request) - allowed:
        raise ValueError("Unknown composition option")
    clips = request.get("clips")
    if not isinstance(clips, list) or not 1 <= len(clips) <= 100:
        raise ValueError("Select 1-100 local clips")
    output = output_path(request.get("output"), force=request.get("force", False))
    if Path(output).suffix.lower() != ".mp4":
        raise ValueError("Composition output must be an MP4")
    width, height = request.get("width", 1920), request.get("height", 1080)
    for name, dimension in (("width", width), ("height", height)):
        if type(dimension) is not int or dimension % 2 or not 64 <= dimension <= 4096:
            raise ValueError(name + " must be an even integer between 64 and 4096")
    fps = number(request.get("fps", 30), "fps", 1, 60)
    fit, mute = request.get("fit", "contain"), request.get("mute", False)
    if fit not in {"contain", "cover"} or type(mute) is not bool:
        raise ValueError("fit must be contain/cover and mute must be boolean")
    music_volume = number(request.get("musicVolume", 0.15), "musicVolume", 0, 2)
    source_volume = number(request.get("sourceVolume", 1), "sourceVolume", 0, 2)
    health = doctor()
    if not health["ok"]:
        raise ValueError("; ".join(health["errors"]))
    files, timeline, captions = {}, [], []
    total = 0.0
    def inspect(value, subtitle=False, font=False):
        path = local_file(value, subtitle=subtitle, font=font)
        if str(path) == output:
            raise ValueError("Output must differ from every input")
        if str(path) not in files:
            before = sha256(path)
            info = {} if subtitle or font else probe(path, health["tools"]["ffprobe"]["path"])
            if sha256(path) != before:
                raise ValueError("Media changed during inspection")
            files[str(path)] = {"path": str(path), "bytes": path.stat().st_size, "sha256": before, **info}
        return files[str(path)]
    for clip in clips:
        if not isinstance(clip, dict) or set(clip) - {"path", "start", "duration", "text"}:
            raise ValueError("Invalid clip specification")
        item = inspect(clip.get("path"))
        if not any(s.get("codec_type") == "video" for s in item["streams"]):
            raise ValueError("Each clip must contain video")
        start = number(clip.get("start", 0), "start", 0, item["duration"])
        duration = number(clip.get("duration", item["duration"] - start), "duration", 0.05, 3600)
        if start + duration > item["duration"] + 0.02:
            raise ValueError("Clip trim exceeds its duration")
        text = clip.get("text", "")
        if not isinstance(text, str) or len(text) > 10000:
            raise ValueError("Clip caption must be text of at most 10000 characters")
        if text:
            captions.append({"start": total, "end": total + duration, "text": text})
        timeline.append({"path": item["path"], "start": start, "duration": duration,
                         "timelineStart": total, "hasAudio": any(s.get("codec_type") == "audio" for s in item["streams"])})
        total += duration
    if total > 3600:
        raise ValueError("One local edit can be at most one hour")
    if sum(bool(x) for x in (captions, request.get("captions"), request.get("subtitles"))) > 1:
        raise ValueError("Choose clip text, captions, or an imported subtitle file")
    captions = request.get("captions", captions)
    if not isinstance(captions, list) or len(captions) > 5000:
        raise ValueError("captions must be an array of at most 5000 entries")
    for caption in captions:
        if not isinstance(caption, dict) or set(caption) != {"start", "end", "text"}:
            raise ValueError("Captions require start, end and text")
        start = number(caption["start"], "caption start", 0, total)
        end = number(caption["end"], "caption end", 0, total)
        if end <= start or not isinstance(caption["text"], str) or len(caption["text"]) > 10000:
            raise ValueError("Invalid caption timing or text")
    sources = {}
    for name in ("narration", "music", "subtitles"):
        if request.get(name):
            item = inspect(request[name], subtitle=name == "subtitles")
            sources[name] = item["path"]
            if name != "subtitles" and not any(s.get("codec_type") == "audio" for s in item["streams"]):
                raise ValueError(name + " must contain audio")
            if name == "narration" and item["duration"] > total + 0.05:
                raise ValueError("Narration exceeds the timeline; revise the edit instead of silently cutting speech")
    if captions or sources.get("subtitles"):
        candidates = [(Path("/System/Library/Fonts/STHeiti Medium.ttc"), "Heiti SC"),
                      (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"), "Noto Sans CJK SC"),
                      (Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc", "Microsoft YaHei")]
        default_font = next(((str(p), family) for p, family in candidates if p.is_file()), (None, None))
        explicit_font = request.get("font") or os.environ.get("HOLYCRAB_SUBTITLE_FONT")
        font = explicit_font or default_font[0]
        family = request.get("fontFamily") or (None if explicit_font else default_font[1])
        if font:
            if not isinstance(family, str) or not re.fullmatch(r"[\w -]{1,80}", family):
                raise ValueError("An explicit font requires its fontFamily (letters, numbers, spaces or hyphens)")
            sources["font"] = inspect(font, font=True)["path"]
            sources["fontFamily"] = family
    if mute and (sources.get("narration") or sources.get("music")):
        raise ValueError("mute cannot be combined with narration or music")
    value = {"output": output, "force": request.get("force", False), "width": width, "height": height,
             "fps": fps, "fit": fit, "mute": mute, "musicVolume": music_volume, "sourceVolume": source_volume,
             "duration": total, "timeline": timeline, "captions": captions, "sources": sources,
             "files": list(files.values()), "tools": health["tools"]}
    value["approvalHash"] = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return value


def srt_time(seconds):
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def compose(request, *, confirmed=False, approval_hash=None):
    plan = preview(request)
    if not confirmed:
        return plan
    if not approval_hash or approval_hash != plan["approvalHash"]:
        raise ValueError("Preview the current inputs and confirm their exact approvalHash before rendering")
    output = Path(plan["output"])
    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg, ffprobe = (plan["tools"][x]["path"] for x in ("ffmpeg", "ffprobe"))
    timeout = min(7200, max(120, plan["duration"] * 30))
    with tempfile.TemporaryDirectory(prefix=".holycrab-edit-", dir=output.parent) as folder:
        work = Path(folder)
        frozen = {}
        for index, item in enumerate(plan["files"]):
            target = work / (f"input-{index}" + Path(item["path"]).suffix.lower())
            shutil.copyfile(item["path"], target)
            if sha256(target) != item["sha256"]:
                raise ValueError("Input changed since approval; preview again")
            frozen[item["path"]] = target.name
        base = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
        w, h, fps = plan["width"], plan["height"], plan["fps"]
        scale = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2" if plan["fit"] == "contain"
                 else f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}")
        video = ["-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p"]
        audio = ["-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k"]
        segments = []
        for index, clip in enumerate(plan["timeline"]):
            name = f"segment-{index:03}.mp4"
            command = base + ["-ss", str(clip["start"]), "-protocol_whitelist", "file", "-format_whitelist", FORMATS,
                              "-i", frozen[clip["path"]]]
            if not clip["hasAudio"]:
                command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            command += ["-map", "0:v:0", "-map", "0:a:0" if clip["hasAudio"] else "1:a:0",
                        "-vf", scale + f",setsar=1,fps={fps}", "-af", "apad",
                        "-t", str(clip["duration"])] + video + audio + [name]
            run(command, cwd=work, timeout=timeout)
            segments.append((name, clip["duration"]))
        (work / "clips.txt").write_text("".join(f"file '{name}'\nduration {duration}\n" for name, duration in segments), encoding="utf-8")
        command = base + ["-protocol_whitelist", "file", "-f", "concat", "-safe", "1", "-i", "clips.txt"]
        sources = plan["sources"]
        indices = {}
        for name in ("narration", "music"):
            if name in sources:
                indices[name] = len(indices) + 1
                if name == "music":
                    command += ["-stream_loop", "-1"]
                command += ["-protocol_whitelist", "file", "-format_whitelist", FORMATS, "-i", frozen[sources[name]]]
        filters = []
        if plan["mute"]:
            command += ["-an"]
        else:
            source = f"{indices['narration']}:a:0" if "narration" in indices else "0:a:0"
            volume = 1 if "narration" in indices else plan["sourceVolume"]
            filters.append(f"[{source}]volume={volume},apad[voice]")
            if "music" in indices:
                filters += [f"[{indices['music']}:a:0]volume={plan['musicVolume']}[music]",
                            "[voice][music]amix=inputs=2:normalize=0:duration=longest,alimiter=limit=0.95[audio]"]
            else:
                filters.append("[voice]anull[audio]")
            command += ["-filter_complex", ";".join(filters), "-map", "[audio]"] + audio
        subtitle = frozen.get(sources.get("subtitles"))
        if plan["captions"]:
            subtitle = "captions.srt"
            blocks = []
            for index, caption in enumerate(plan["captions"]):
                # SRT markup is escaped; neutralize ASS overrides and literal escape sequences.
                text = html.escape(caption["text"].replace("\\", "＼").replace("{", "｛").replace("}", "｝"))
                blocks.append(f"{index + 1}\n{srt_time(caption['start'])} --> {srt_time(caption['end'])}\n{text}\n")
            (work / subtitle).write_text("\n".join(blocks), encoding="utf-8")
        command += ["-map", "0:v:0"]
        if subtitle:
            font_option = ""
            if sources.get("font"):
                (work / "fonts").mkdir()
                shutil.copyfile(work / frozen[sources["font"]], work / "fonts" / Path(frozen[sources["font"]]).name)
                font_option = ":fontsdir=fonts:force_style='FontName=" + sources["fontFamily"] + "'"
            command += ["-loglevel", "info", "-vf", f"subtitles={subtitle}{font_option}"] + video
        else:
            command += ["-c:v", "copy"]
        command += ["-t", str(plan["duration"]), "-movflags", "+faststart", "result.mp4"]
        run(command, cwd=work, timeout=timeout)
        rendered = work / "result.mp4"
        check = probe(rendered, ffprobe)
        stream = next((s for s in check["streams"] if s.get("codec_type") == "video"), {})
        has_audio = any(s.get("codec_type") == "audio" for s in check["streams"])
        if (stream.get("width") != w or stream.get("height") != h or has_audio == plan["mute"]
                or abs(check["duration"] - plan["duration"]) > max(0.15, 2 / fps)):
            raise ValueError("Rendered media failed duration, dimensions or audio verification")
        checksum = sha256(rendered)
        if plan["force"]:
            os.replace(rendered, output)
        else:
            os.link(rendered, output)  # atomic no-clobber, including files created during the render
        return {"output": str(output), "bytes": output.stat().st_size, "sha256": checksum,
                "duration": check["duration"], "width": w, "height": h, "hasAudio": has_audio,
                "verified": True, "approvalHash": plan["approvalHash"]}
