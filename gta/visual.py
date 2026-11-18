"""Visual event signals from a local video file (no paid vision API).

Frames are decoded at low resolution (default 160x90 at 4 fps) and reduced to per-frame
features, then aggregated per second:
  flash   - sudden brightness rise (explosions, muzzle flashes, lightning-like events)
  fire    - sudden rise in warm orange/red pixels (fire, explosions, sunset-like warm spikes)
  motion  - mean absolute frame difference (chases, crashes, fast camera or action)
  cut     - scene change: share of pixels that change strongly between frames

These are heuristics. They detect *that something changed sharply*, not *what happened*.
Recognising specific events (police chase, shootout, crash) needs a classifier. That is not
implemented here, and the output says so.
"""
import subprocess

import numpy as np

W, H, FPS = 160, 90, 4


def decode_frames(path, fps=FPS):
    """Yield frames as uint8 arrays (H, W, 3)."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-an",
         "-vf", f"fps={fps},scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True)
    frame_size = W * H * 3
    data = np.frombuffer(proc.stdout, dtype=np.uint8)
    n = len(data) // frame_size
    return data[: n * frame_size].reshape(n, H, W, 3)


def frame_features(frames):
    """Per-frame features. Returns dict of arrays with length len(frames)."""
    f = frames.astype(np.float32)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    warm = ((r > 150) & (r > g * 1.3) & (g > b)).mean(axis=(1, 2))
    mean_luma = luma.mean(axis=(1, 2))
    diff = np.zeros(len(frames), dtype=np.float32)
    cut = np.zeros(len(frames), dtype=np.float32)
    if len(frames) > 1:
        d = np.abs(np.diff(luma, axis=0))
        diff[1:] = d.mean(axis=(1, 2))
        cut[1:] = (d > 40).mean(axis=(1, 2))
    return {"luma": mean_luma, "warm": warm, "motion": diff, "cut": cut}


def per_second(features, fps=FPS):
    """Aggregate frame features to per-second signals in [0, 1]."""
    seconds = max(1, len(features["luma"]) // fps)
    out = {k: np.zeros(seconds) for k in ("flash", "fire", "motion", "cut")}
    for s in range(seconds):
        sl = slice(s * fps, (s + 1) * fps)
        # Jump relative to the previous second, so steady bright scenes are not "flashes".
        prev = slice(max(0, (s - 1) * fps), max(1, s * fps))
        lum_now = features["luma"][sl].max()
        lum_prev = features["luma"][prev].mean()
        out["flash"][s] = np.clip((lum_now - lum_prev) / 40.0, 0, 1)
        warm_now = features["warm"][sl].max()
        warm_prev = features["warm"][prev].mean()
        out["fire"][s] = np.clip((warm_now - warm_prev) / 0.15, 0, 1)
        out["motion"][s] = np.clip(features["motion"][sl].mean() / 30.0, 0, 1)
        out["cut"][s] = np.clip(features["cut"][sl].max() / 0.4, 0, 1)
    return out


def combined(signals):
    """Single visual excitement value per second: sharp events weigh more than steady motion."""
    return np.clip(0.35 * signals["flash"] + 0.25 * signals["fire"]
                   + 0.25 * signals["motion"] + 0.15 * signals["cut"], 0, 1)


def analyze(path):
    frames = decode_frames(path)
    if len(frames) < 2:
        return {"per_second": {}, "combined": np.array([]), "frames": len(frames)}
    sig = per_second(frame_features(frames))
    return {"per_second": sig, "combined": combined(sig), "frames": len(frames)}
