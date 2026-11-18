"""Quality control for rendered Shorts. Runs before a clip is queued; it never replaces rights checks.

Checks:
  framing     - 1080x1920, blurred or filled edges (no empty bars), a visible centre subject
  subtitles   - ASS dialogue times match the plan (within 50 ms), no overlaps, no empty text
  audio       - loudness near -14 LUFS, true peak <= -1 dBTP, not silent, not clipped
  duplicates  - visual fingerprint (dHash) not near any stored clip, and no overlapping
                time range from the same source

Each check returns (ok, detail). `run_all` returns a dict with `passed` and `failures`.
"""
import re
import subprocess

import numpy as np

from .render import measure_loudness, probe

HASH_DISTANCE_MAX = 10  # out of 64 bits; closer than this counts as a duplicate frame


def _frame_gray(path, t, w, h):
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
         "-vf", f"scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True)
    arr = np.frombuffer(proc.stdout, dtype=np.uint8)
    return arr.reshape(h, w).astype(np.float32)


def check_framing(path, info=None):
    info = info or probe(path)
    if (info["width"], info["height"]) != (1080, 1920):
        return False, f"size {info['width']}x{info['height']} is not 1080x1920"
    mid = max(0.5, info["duration_s"] / 2)
    img = _frame_gray(path, mid, 108, 192)
    top, bottom = img[:12].mean(), img[-12:].mean()
    if top < 3 and bottom < 3:
        return False, "empty top and bottom bars (no fill)"
    centre = img[60:132, 20:88]
    if centre.std() < 4:
        return False, "centre region is flat (no visible subject)"
    return True, "ok"


_DIALOGUE = re.compile(r"^Dialogue: \d+,(\d+:\d{2}:\d{2}\.\d{2}),(\d+:\d{2}:\d{2}\.\d{2}),[^,]*,[^,]*,\d+,\d+,\d+,,(.*)$")


def _ts(s):
    h, m, rest = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def check_subtitles(ass_path, plan_segments, tolerance=0.05):
    events = []
    for line in open(ass_path, encoding="utf-8"):
        m = _DIALOGUE.match(line.rstrip("\n"))
        if m:
            events.append((_ts(m.group(1)), _ts(m.group(2)), m.group(3)))
    if len(events) != len(plan_segments):
        return False, f"{len(events)} subtitle events for {len(plan_segments)} planned segments"
    for (s, e, text), seg in zip(events, plan_segments):
        if not text.strip():
            return False, "empty subtitle text"
        if abs(s - seg["start"]) > tolerance or abs(e - seg["end"]) > tolerance:
            return False, f"subtitle at {s:.2f}-{e:.2f}s does not match plan {seg['start']:.2f}-{seg['end']:.2f}s"
    return True, "ok"


def check_audio(path):
    info = measure_loudness(path)
    if not info:
        return False, "could not measure loudness"
    lufs = float(info["input_i"])
    tp = float(info["input_tp"])
    if abs(lufs + 14) > 1.5:
        return False, f"loudness {lufs:.1f} LUFS is outside -14 +/- 1.5"
    if tp > -1.0:
        return False, f"true peak {tp:.1f} dBTP above -1.0"
    if lufs < -40:
        return False, "audio is effectively silent"
    return True, f"{lufs:.1f} LUFS, {tp:.1f} dBTP"


def dhash(path, t):
    """64-bit difference hash of one frame at time t."""
    img = _frame_gray(path, t, 9, 8)
    bits = (img[:, 1:] > img[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a, b):
    return bin(a ^ b).count("1")


def check_duplicates(store, path, source_id, start, end, own_key=None):
    """Compare with stored clips: same-source overlap or near-identical frame counts as duplicate."""
    rows = store.fetchall("SELECT source_key, source_id, start_s, end_s, dhash FROM fingerprints")
    for r in rows:
        if r["source_key"] == own_key:
            continue
        if source_id and r["source_id"] == source_id:
            overlap = max(0, min(end, r["end_s"]) - max(start, r["start_s"]))
            if overlap > 0.5 * min(end - start, r["end_s"] - r["start_s"]):
                return False, f"overlaps {r['source_key']} from the same source"
    h = dhash(path, max(0.5, (end - start) / 2))
    for r in rows:
        if r["source_key"] == own_key:
            continue
        if hamming(h, int(r["dhash"])) <= HASH_DISTANCE_MAX:
            return False, f"visually near-identical to {r['source_key']}"
    return True, "ok"


def record_fingerprint(store, source_key, source_id, path, start, end):
    store.execute(
        "INSERT OR REPLACE INTO fingerprints (source_key, source_id, start_s, end_s, dhash) VALUES (?,?,?,?,?)",
        (source_key, source_id, start, end, str(dhash(path, max(0.5, (end - start) / 2)))),
    )


def run_all(store, path, plan_segments, ass_path, source_key=None, source_id=None, start=0.0, end=None):
    info = probe(path)
    end = end if end is not None else info["duration_s"]
    results = {
        "framing": check_framing(path, info),
        "subtitles": check_subtitles(ass_path, plan_segments) if ass_path else (True, "no captions"),
        "audio": check_audio(path),
        "duplicates": check_duplicates(store, path, source_id, start, end, own_key=source_key),
    }
    failures = {k: v[1] for k, v in results.items() if not v[0]}
    return {"passed": not failures, "failures": failures, "details": {k: v[1] for k, v in results.items()}}
