"""FFmpeg renderer for vertical 1080x1920 Shorts.

Input is a plan dict (see templates.py) describing a hook, timed caption segments and a
visual style. Output is an H.264/AAC MP4 with:
  * a slow zoom on a generated neon background (no third-party footage),
  * an opening hook card for the first seconds,
  * burned-in English captions with highlighted keywords (ASS subtitles, libass),
  * a generated audio bed normalized to EBU R128 loudness (-14 LUFS, -1.5 dBTP),
  * duration checked against the 20-45 second window.

Everything is generated locally. The renderer never downloads or reuses game footage.
"""
import json
import re
import subprocess
import textwrap
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

STYLES = {
    # name: (top colour, bottom colour, glow colour, accent colour)  as RGB
    "vice_pink": ((18, 4, 30), (120, 10, 110), (255, 40, 180), (0, 230, 255)),
    "sunset_cyan": ((8, 6, 24), (60, 20, 90), (255, 140, 40), (40, 240, 255)),
    "midnight_neon": ((4, 8, 20), (30, 10, 80), (180, 60, 255), (255, 60, 200)),
}


class RenderError(RuntimeError):
    pass


def _ff(args, cwd=None):
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
                          capture_output=True, text=True, cwd=cwd)
    if proc.returncode != 0:
        raise RenderError(proc.stderr[-2000:])


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, check=True)
    data = json.loads(out.stdout)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    return {
        "width": int(v["width"]), "height": int(v["height"]),
        "duration_s": float(data["format"]["duration"]),
        "video_codec": v["codec_name"], "audio_codec": a["codec_name"] if a else None,
    }


def background_png(path, width, height, style):
    top, bottom, glow, accent = STYLES[style]
    y = np.linspace(0, 1, height)[:, None]
    base = (np.array(top)[None, None, :] * (1 - y[..., None]) + np.array(bottom)[None, None, :] * y[..., None])
    img = np.repeat(base, width, axis=1).astype(np.uint8)
    im = Image.fromarray(img, "RGB")
    # glow blob near the upper third
    glow_layer = Image.new("RGB", (width, height), (0, 0, 0))
    d = ImageDraw.Draw(glow_layer)
    cx, cy = width // 2, int(height * 0.36)
    for r in range(520, 0, -20):
        k = 1 - r / 520
        c = tuple(int(g * k * 0.55) for g in glow)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c)
    im = Image.fromarray(np.clip(np.asarray(im, np.int16) + np.asarray(glow_layer, np.int16), 0, 255).astype(np.uint8))
    # perspective-free neon grid in the lower half
    grid = ImageDraw.Draw(im)
    for x in range(-width, width * 2, 120):
        grid.line([(width // 2, int(height * 0.62)), (x, height)], fill=accent, width=2)
    for i in range(12):
        yy = int(height * 0.62 + (height * 0.38) * (i / 12) ** 1.6)
        grid.line([(0, yy), (width, yy)], fill=accent, width=2)
    im.save(path)
    return path


def hook_png(path, width, height, lines, font_bold, accent):
    """Transparent PNG with a centred hook headline (up to 3 lines)."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(font_bold, 150)
    y = int(height * 0.16)
    for line in lines:
        w = d.textlength(line, font=f)
        x = (width - w) / 2
        for dx, dy in [(-6, 0), (6, 0), (0, -6), (0, 6)]:
            d.text((x + dx, y + dy), line, font=f, fill=(0, 0, 0, 230))
        d.text((x, y), line, font=f, fill=accent + (255,))
        y += 170
    img.save(path)
    return path


def _ass_color(rgb):
    r, g, b = rgb
    return f"&H00{b:02X}{g:02X}{r:02X}&"


def ass_file(path, segments, font_name, accent, pink, start_offset):
    """segments: list of dict(text, start, end, keywords=[...]). Keywords are highlighted."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font_name},68,{_ass_color((255, 255, 255))},&H000000FF&,{_ass_color((20, 0, 30))},&H80000000&,-1,0,0,0,100,100,0,0,1,6,2,2,80,80,430,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def ts(t):
        t = max(0.0, t + start_offset)
        h, rem = divmod(t, 3600)
        m, s = divmod(rem, 60)
        return f"{int(h)}:{int(m):02d}:{s:05.2f}"

    lines = []
    for seg in segments:
        # Explicit ASS line breaks keep captions inside the 1080px frame (auto-wrap lost lines).
        text = textwrap.fill(seg["text"].replace("\n", " "), width=20).replace("\n", "\\N")
        for kw in sorted(seg.get("keywords", []), key=len, reverse=True):
            text = re.sub(re.escape(kw), lambda m: "{\\c" + _ass_color(pink) + "\\b1}" + m.group(0) + "{\\c&HFFFFFF&\\b0}", text, flags=re.I)
        lines.append(f"Dialogue: 0,{ts(seg['start'])},{ts(seg['end'])},Caption,,0,0,0,,{text}")
    Path(path).write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def measure_loudness(path):
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
         "-f", "null", "-"], capture_output=True, text=True)
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr, flags=re.S)
    return json.loads(m[-1]) if m else None


def render(plan, out_path, work_dir, cfg):
    """plan keys: hook (list of lines), segments (list), style, duration_s, font_name."""
    r = cfg["render"]
    W, H, FPS = r["width"], r["height"], r["fps"]
    dur = float(plan["duration_s"])
    if not (r["min_seconds"] <= dur <= r["max_seconds"]):
        raise RenderError(f"duration {dur}s outside {r['min_seconds']}-{r['max_seconds']}s")
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    style = plan.get("style", "vice_pink")
    accent = STYLES[style][3]
    pink = STYLES[style][2]

    bg = background_png(work / "bg.png", W, H, style)
    hk = hook_png(work / "hook.png", W, H, plan["hook"], r["font_bold"], accent)
    ass = ass_file(work / "captions.ass", plan["segments"], "DejaVu Sans", accent, pink, 0)
    frames = int(dur * FPS)
    zoom_step = 0.10 / frames  # slow push-in from 1.00x to 1.10x

    # Generated audio bed: a soft pulsing tone plus low noise. Original, no samples used.
    audio = (f"aevalsrc='0.22*sin(2*PI*110*t)*exp(-6*mod(t,0.5))+0.06*sin(2*PI*55*t)"
             f"+0.015*(random(0)-0.5)':s=48000:d={dur}")
    vf = (
        f"[0:v]zoompan=z='min(zoom+{zoom_step:.8f},1.10)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":s={W}x{H}:fps={FPS},format=yuv420p,subtitles=captions.ass[bgv];"
        f"[1:v]format=rgba,fade=t=in:st=0:d=0.4,fade=t=out:st={max(dur - 0.5, 0):.2f}:d=0.4[hk];"
        f"[bgv][hk]overlay=0:0:enable='lt(t,4)'[v]"
    )
    mp4 = work / "raw.mp4"
    _ff([
        "-loop", "1", "-framerate", str(FPS), "-i", str(bg),
        "-loop", "1", "-framerate", str(FPS), "-i", str(hk),
        "-f", "lavfi", "-i", audio,
        "-filter_complex", vf, "-map", "[v]", "-map", "2:a",
        "-t", str(dur), "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(mp4),
    ], cwd=work)

    # Second pass: loudness normalization, then the final file.
    final = Path(out_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    _ff(["-i", str(mp4), "-af", f"loudnorm=I={r['loudness_lufs']}:TP={r['true_peak_db']}:LRA=11",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(final)])

    info = probe(final)
    problems = []
    if (info["width"], info["height"]) != (W, H):
        problems.append(f"size {info['width']}x{info['height']} != {W}x{H}")
    if not (r["min_seconds"] <= info["duration_s"] <= r["max_seconds"]):
        problems.append(f"duration {info['duration_s']:.2f}s out of range")
    if info["audio_codec"] is None:
        problems.append("no audio stream")
    if problems:
        raise RenderError("; ".join(problems))
    info["loudness"] = measure_loudness(final)
    info["path"] = str(final)
    return info
