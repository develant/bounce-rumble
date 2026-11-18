"""Clipping and editing of permitted source files into 9:16 Shorts.

A source must be registered with a creator_id and an acquisition type:
  owned        - the channel owner's own recording
  creator_file - a file supplied by a creator who has a verified permission record
  metadata_only - not clippable (no local file), recorded for discovery only

Every clip is queued with the source's creator_id. Publishing is decided later by the
rights gate in publisher.py: an unverified creator stays queued and blocked, never published.

No downloading happens here. Acquisition of third-party streams is out of scope: the
platforms' terms do not permit it, so the pipeline takes local files only.
"""
import hashlib
import json
import os
from pathlib import Path

from . import moments as moments_mod
from .render import RenderError, _ff, ass_file, hook_png, measure_loudness, probe
from .store import now_iso

ALLOWED_ACQUISITION = {"owned", "creator_file"}


def source_id_for_file(path):
    h = hashlib.sha1(Path(path).read_bytes()[:4_000_000]).hexdigest()[:16]
    return f"file:{h}"


def register_source(store, creator_id, local_path, acquisition, title="", url=""):
    if acquisition not in ALLOWED_ACQUISITION:
        raise ValueError(f"acquisition {acquisition!r} is not clippable")
    if not creator_id:
        raise ValueError("creator_id is required")
    if not Path(local_path).exists():
        raise FileNotFoundError(local_path)
    sid = source_id_for_file(local_path)
    store.execute(
        "INSERT OR REPLACE INTO sources (source_id, creator_id, title, url, local_path, acquisition, added_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (sid, creator_id, title, url, str(local_path), acquisition, now_iso()),
    )
    return sid


def make_clip(src, start, end, out_path, work_dir, cfg, hook, captions, attribution, style="vice_pink"):
    """Render one 9:16 clip from src between start and end seconds. Returns probe info + loudness."""
    r = cfg["render"]
    W, H, FPS = r["width"], r["height"], r["fps"]
    dur = float(end) - float(start)
    if not (r["min_seconds"] <= dur <= r["max_seconds"]):
        raise RenderError(f"clip length {dur:.1f}s outside {r['min_seconds']}-{r['max_seconds']}s")
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    from .render import STYLES

    accent = STYLES[style][3]
    pink = STYLES[style][2]
    hk = hook_png(work / "hook.png", W, H, hook, r["font_bold"], accent)
    segs = list(captions) + [{"text": attribution, "start": 0.0, "end": dur, "keywords": []}]
    ass = ass_file(work / "captions.ass", segs, "DejaVu Sans", accent, pink, 0)
    vf = (
        "[0:v]split=2[bgs][fgs];"
        # Blur a small copy and scale it up: a full-frame boxblur at 1080x1920 is far too slow.
        f"[bgs]scale={W//10}:{H//10}:force_original_aspect_ratio=increase,crop={W//10}:{H//10},boxblur=3:1,scale={W}:{H}[bg];"
        f"[fgs]scale={W}:-2[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p,subtitles=captions.ass[v1];"
        f"[1:v]format=rgba,fade=t=in:st=0:d=0.4[hk];"
        f"[v1][hk]overlay=0:0:enable='lt(t,3.5)'[v];"
        f"[0:a]loudnorm=I={r['loudness_lufs']}:TP={r['true_peak_db']}:LRA=11[a]"
    )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    _ff(["-ss", str(start), "-t", str(dur), "-i", str(src), "-loop", "1", "-framerate", str(FPS), "-i", str(hk),
         "-filter_complex", vf, "-map", "[v]", "-map", "[a]", "-r", str(FPS),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", str(out)], cwd=work)
    info = probe(out)
    if (info["width"], info["height"]) != (W, H):
        raise RenderError(f"size {info['width']}x{info['height']}")
    if not (r["min_seconds"] <= info["duration_s"] <= r["max_seconds"] + 0.5):
        raise RenderError(f"duration {info['duration_s']:.2f}s out of range")
    info["loudness"] = measure_loudness(out)
    info["path"] = str(out)
    return info


def build_candidates(store, cfg, source_id, top=3, clip_s=30, out_root=None, use_visual=True):
    """Find highlight windows in a registered source, render, run QC, and queue candidates.

    Signals: visual events (visual.py), audio reaction/impact/music (audio_features.py), combined
    in moment_scoring.py. A candidate that fails QC or duplicates an earlier clip is not queued.
    Rights are NOT checked here on purpose: a queued candidate stays blocked by publisher.py
    until the creator's permission record is verified.
    """
    from . import audio_features, moment_scoring, publisher, qc, visual

    src = store.fetchone("SELECT * FROM sources WHERE source_id=?", (source_id,))
    if not src:
        raise KeyError(f"unknown source {source_id}")
    out_root = Path(out_root or Path(__file__).parent / "renders" / "clips")
    aud = audio_features.analyze(src["local_path"])
    vis = visual.analyze(src["local_path"]) if use_visual else {"combined": None}
    labels = aud["labels"]
    ent, confidence = moment_scoring.per_second_entertainment(
        visual=vis["combined"] if vis.get("combined") is not None and len(vis["combined"]) else None,
        reaction=labels.get("reaction"), impact=labels.get("impact"), music=labels.get("music"))
    picks = moment_scoring.score_windows(ent, clip_s=clip_s, count=top)
    results = []
    attribution = f"Source: {src['creator_id'].split(':', 1)[-1]} · used with permission"
    for m in picks:
        key = f"clip:{source_id}:{int(m['start'])}"
        d = out_root / key.replace(":", "_")
        d.mkdir(parents=True, exist_ok=True)
        start, end = m["start"], m["end"]
        hook = ["THE BEST", "MOMENT"]
        dur = end - start
        segments = [{"text": "Loudest moment in this stream", "start": 3.5, "end": dur - 1.5, "keywords": ["Loudest"]}]
        info = make_clip(src["local_path"], start, end, d / "clip.mp4", d / "work", cfg, hook, segments, attribution)
        ass = d / "work" / "captions.ass"
        plan_segments = segments + [{"text": attribution, "start": 0.0, "end": dur}]
        report = qc.run_all(store, d / "clip.mp4", plan_segments, ass, source_key=key,
                            source_id=source_id, start=start, end=end)
        plan = {"template": "clip_v1", "source_id": source_id, "start": start, "end": end,
                "score": m["score"], "signal_confidence": round(float(confidence), 3),
                "qc": report, "fact_ids": [], "creator_id": src["creator_id"]}
        (d / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
        if not report["passed"]:
            store.log("clip", "candidate failed QC", level="warning", source_key=key, failures=report["failures"])
            results.append({"source_key": key, "qc": "failed", "failures": report["failures"]})
            continue
        qc.record_fingerprint(store, key, source_id, d / "clip.mp4", start, end)
        queued = publisher.add_short(
            store, source_key=key, template="clip_v1", title=f"Highlight from {src['title'] or 'stream'}",
            description=f"{attribution}\n{src['url'] or ''}".strip(), tags=["GTA VI", "highlights", "shorts"],
            file_path=str(d / "clip.mp4"), duration_s=info["duration_s"],
            quality_score=float(m["score"]), creator_id=src["creator_id"], fact_ids=[])
        results.append({"source_key": key, "qc": "passed", "queued_new": queued, "start": start, "score": m["score"]})
    return results
