"""Per-second audio features from a local file, used to separate likely streamer reactions
from music and game sound effects. These are spectral heuristics, not speech or emotion models.

Per second (mono 8 kHz PCM):
  level      - RMS in dBFS
  speech     - share of energy in the 300-3400 Hz band (voice lives here; music and explosions spread wider)
  onset      - sharp rises in level within the second (shouts, laughs, impacts)
  flatness   - spectral flatness 0..1 (high = noise-like: explosions, crashes; low = tonal: music, voice)
  steadiness - 1 minus normalized level variation (high for steady background music)

Derived labels (heuristic):
  reaction   - voice-band energy with loud, bursty onsets (shouting, laughing, surprise)
  music      - steady, tonal, not very loud (background track)
  impact     - noise-like loud bursts (explosions, crashes, gunfire)
"""
import numpy as np

from .moments import SAMPLE_RATE, decode_mono


def per_second(samples):
    n = SAMPLE_RATE
    seconds = len(samples) // n
    out = {k: np.zeros(seconds) for k in ("level", "speech", "onset", "flatness", "steadiness")}
    prev_level = None
    for s in range(seconds):
        x = samples[s * n:(s + 1) * n].astype(np.float64)
        rms = np.sqrt(np.mean(x * x)) + 1e-12
        level = 20 * np.log10(rms)
        spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2 + 1e-12
        freqs = np.fft.rfftfreq(len(x), 1.0 / n)
        voice = spec[(freqs >= 300) & (freqs <= 3400)].sum()
        out["speech"][s] = voice / spec.sum()
        geo = np.exp(np.mean(np.log(spec[freqs > 20])))
        out["flatness"][s] = np.clip(geo / np.mean(spec[freqs > 20]), 0, 1)
        # onset: short-window RMS rise inside the second
        w = 400  # 50 ms
        frames = x[: len(x) // w * w].reshape(-1, w)
        short = 20 * np.log10(np.sqrt(np.mean(frames * frames, axis=1)) + 1e-12)
        rises = np.diff(short)
        out["onset"][s] = np.clip(np.max(rises) / 20.0, 0, 1) if rises.size else 0.0
        out["level"][s] = level
        prev_level = level
    level = out["level"]
    span = max(1.0, np.percentile(level, 95) - np.percentile(level, 5)) if seconds else 1.0
    local_std = np.array([np.std(level[max(0, i - 3):i + 4]) for i in range(seconds)]) if seconds else np.array([])
    out["steadiness"] = np.clip(1 - local_std / (span / 2 + 1e-9), 0, 1) if seconds else out["steadiness"]
    return out


def labels(feat):
    loud = np.clip((feat["level"] + 40) / 30, 0, 1)
    reaction = np.clip(feat["speech"] * loud * (0.5 + 0.5 * feat["onset"]), 0, 1)
    music = np.clip(feat["steadiness"] * (1 - feat["flatness"]) * np.clip(1 - loud, 0, 1), 0, 1)
    impact = np.clip(feat["flatness"] * loud * (0.4 + 0.6 * feat["onset"]), 0, 1)
    return {"reaction": reaction, "music": music, "impact": impact}


def analyze(path):
    samples = decode_mono(path)
    if len(samples) < SAMPLE_RATE:
        return {"features": {}, "labels": {}}
    feat = per_second(samples)
    return {"features": feat, "labels": labels(feat)}
