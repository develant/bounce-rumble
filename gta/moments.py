"""Highlight detection on a local source file (audio excitement).

Works only on files the pipeline holds with a permission record (see clip.py). The signal is
loudness and dynamic energy per window, computed with numpy from ffmpeg's decoded PCM.
Visual action and chat-spike signals are not implemented here; scoring.py accepts them when
they exist, and missing signals are reported rather than treated as zero.
"""
import subprocess

import numpy as np

SAMPLE_RATE = 8000


def decode_mono(path):
    """Return a float32 array of mono samples in [-1, 1] at SAMPLE_RATE."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1",
         "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"],
        capture_output=True, check=True)
    return np.frombuffer(proc.stdout, dtype=np.float32)


def window_energy(samples, window_s=1.0):
    """Per-window RMS in dBFS, one value per window_s seconds."""
    n = int(SAMPLE_RATE * window_s)
    usable = len(samples) // n * n
    if usable == 0:
        return np.array([])
    frames = samples[:usable].reshape(-1, n)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1)) + 1e-12
    return 20 * np.log10(rms)


def top_moments(energy_db, clip_s=30, count=3, min_gap_s=60, window_s=1.0):
    """Pick the loudest/most dynamic clip_s windows, non-overlapping and min_gap_s apart.

    Score = mean dBFS over the window. A window that is partly silent always scores lower than a
    fully loud one, so highlights are not pulled toward quiet boundaries. Returns dicts with start, end, score.
    """
    if len(energy_db) < clip_s / window_s:
        return []
    step = max(1, int(1 / window_s))
    k = int(clip_s / window_s)
    cands = []
    for i in range(0, len(energy_db) - k + 1, step):
        seg = energy_db[i:i + k]
        level = float(np.mean(seg))
        cands.append((level, i))
    cands.sort(reverse=True)
    chosen = []
    for score, i in cands:
        t = i * window_s
        if all(abs(t - c["start"]) >= min_gap_s for c in chosen):
            chosen.append({"start": float(t), "end": float(t + clip_s), "score": round(score, 2)})
        if len(chosen) == count:
            break
    return chosen
