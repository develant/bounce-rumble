"""Combine visual and audio signals into 0-100 scores for candidate windows (20-45 s).

Per second, entertainment = weighted sum of:
  visual   (sharp visual events: flashes, fire, motion, cuts)  0.40
  reaction (voice-band bursts: shouting, laughing, surprise)   0.35
  impact   (noise-like loud bursts: explosions, crashes)       0.25
and a penalty for background-music-only seconds (music high, visual and reaction low).

A window's score mixes its average and its best 3 seconds, so a single strong moment still counts.
Missing signals are excluded and reported through `confidence`, not treated as zero.

Near-duplicate windows are removed in two ways: time overlap (windows may not overlap more than
half their length, and chosen windows are at least `min_gap_s` apart), and similarity of the
per-second profile (cosine similarity above `max_similarity` counts as the same moment).
"""
import numpy as np

WEIGHTS = {"visual": 0.40, "reaction": 0.35, "impact": 0.25}
MUSIC_PENALTY = 0.15


def _as(arr, n):
    a = np.asarray(arr, dtype=float) if arr is not None and len(arr) else None
    if a is None:
        return None
    if len(a) < n:
        a = np.pad(a, (0, n - len(a)), mode="edge")
    return a[:n]


def per_second_entertainment(visual=None, reaction=None, impact=None, music=None):
    n = max(len(x) for x in (visual, reaction, impact, music) if x is not None and len(x))
    sigs = {"visual": _as(visual, n), "reaction": _as(reaction, n), "impact": _as(impact, n)}
    available = {k: v for k, v in sigs.items() if v is not None}
    if not available:
        return np.zeros(0), 0.0
    total_w = sum(WEIGHTS[k] for k in WEIGHTS)
    avail_w = sum(WEIGHTS[k] for k in available)
    ent = sum(WEIGHTS[k] * v for k, v in available.items()) / avail_w
    m = _as(music, n)
    if m is not None:
        quiet_for_music = np.clip(1 - np.maximum(sigs["visual"] if sigs["visual"] is not None else 0,
                                                 sigs["reaction"] if sigs["reaction"] is not None else 0), 0, 1)
        ent = np.clip(ent - MUSIC_PENALTY * m * quiet_for_music, 0, 1)
    return ent, avail_w / total_w


def score_windows(ent, clip_s=30, count=3, min_gap_s=45, max_overlap=0.5, max_similarity=0.9, min_clip_s=20):
    """Return up to `count` non-duplicate windows with 0-100 scores.

    clip_s is clamped to [min_clip_s, 45]: the requested range for Shorts.
    """
    clip_s = int(max(min_clip_s, min(45, clip_s)))
    n = len(ent)
    if n < clip_s:
        return []
    cands = []
    for i in range(0, n - clip_s + 1):
        seg = ent[i:i + clip_s]
        best3 = np.sort(seg)[-3:].mean()
        score = 100 * (0.5 * seg.mean() + 0.5 * best3)
        cands.append((score, i))
    cands.sort(reverse=True)
    chosen = []
    for score, i in cands:
        ok = True
        for c in chosen:
            cs = int(c["start"])
            overlap = max(0, min(i + clip_s, cs + clip_s) - max(i, cs)) / clip_s
            gap = abs(i - cs)
            if overlap > max_overlap or gap < min_gap_s:
                ok = False
                break
            a = ent[i:i + clip_s]
            b = ent[cs:cs + clip_s]
            denom = np.linalg.norm(a) * np.linalg.norm(b)
            if denom > 0 and float(a @ b / denom) > max_similarity:
                ok = False
                break
        if ok:
            chosen.append({"start": float(i), "end": float(i + clip_s), "score": round(float(score), 1)})
        if len(chosen) == count:
            break
    return chosen
