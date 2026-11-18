"""Multi-signal quality score, normalized to 0-100.

Each signal is normalized to [0, 1] and weighted. Signals that are unavailable
(for example Twitch chat or a YouTube replay heatmap that the API does not expose) are
excluded and the remaining weights are renormalized. The result carries a `confidence`
value, the share of total weight that was actually observed. A candidate is never dropped
just because one signal is missing; it is only ranked with lower confidence.
"""
import math

WEIGHTS = {
    "replay_heatmap": 0.20,      # YouTube most-replayed data (not exposed by the public API)
    "chat_spike": 0.15,          # Twitch / livestream chat message-rate spikes
    "view_velocity": 0.20,       # views (or concurrent viewers) per hour, log-scaled
    "view_acceleration": 0.10,   # growth of velocity between samples
    "engagement": 0.10,          # (likes + comments) / views
    "audio_excitement": 0.10,    # loudness and crowd energy from the clip's audio
    "visual_action": 0.10,       # action-frame density (explosions, chases, shootouts)
    "streamer_reaction": 0.05,   # reaction keywords in chat or the streamer's audio
}


def _clamp(x):
    return max(0.0, min(1.0, x))


def normalize_velocity(per_hour):
    """0 at 0/h, ~1 at 100k/h."""
    if per_hour is None:
        return None
    return _clamp(math.log10(1 + max(per_hour, 0)) / math.log10(1 + 100_000))


def normalize_acceleration(prev_velocity, velocity):
    """Growth ratio: 1x no change -> 0, 5x or more -> 1."""
    if prev_velocity is None or velocity is None or prev_velocity <= 0:
        return None
    return _clamp((velocity / prev_velocity - 1) / 4)


def normalize_engagement(likes, comments, views):
    if views is None or views <= 0 or likes is None:
        return None
    rate = ((likes or 0) + (comments or 0)) / views
    return _clamp(rate / 0.08)  # 8% engagement counts as maximum


def compute(signals):
    """signals: dict name -> float in [0,1] or None. Returns dict(score, confidence, used)."""
    used = {k: _clamp(float(v)) for k, v in signals.items() if k in WEIGHTS and v is not None}
    total = sum(WEIGHTS.values())
    if not used:
        return {"score": None, "confidence": 0.0, "used": {}}
    avail = sum(WEIGHTS[k] for k in used)
    score = sum(WEIGHTS[k] * v for k, v in used.items()) / avail * 100
    return {"score": round(score, 1), "confidence": round(avail / total, 3), "used": used}


def signals_from_samples(samples, likes=None, comments=None, views=None):
    """samples: list of (hours_since_start, views) ascending, at least one point.

    Velocity uses the newest two samples; acceleration needs three samples.
    """
    out = {"view_velocity": None, "view_acceleration": None, "engagement": None}
    if len(samples) >= 2:
        (t0, v0), (t1, v1) = samples[-2], samples[-1]
        dt = max(t1 - t0, 1e-6)
        vel = (v1 - v0) / dt
        out["view_velocity"] = normalize_velocity(vel)
        if len(samples) >= 3:
            (ta, va), (tb, vb) = samples[-3], samples[-2]
            prev_vel = (vb - va) / max(tb - ta, 1e-6)
            out["view_acceleration"] = normalize_acceleration(prev_vel, vel)
    out["engagement"] = normalize_engagement(likes, comments, views)
    return out
