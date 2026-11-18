"""Fast tests for moment detection and clip registration rules. No rendering here."""
import numpy as np
import pytest

from gta import clip, moments
from gta.store import Store


def test_window_energy_levels_follow_amplitude():
    sr = moments.SAMPLE_RATE
    quiet = np.full(sr * 2, 0.01, dtype=np.float32)
    loud = np.full(sr * 2, 0.5, dtype=np.float32)
    e = moments.window_energy(np.concatenate([quiet, loud]), window_s=1.0)
    assert len(e) == 4
    assert e[2] > e[0] + 20  # loud windows are much louder in dB


def test_top_moments_picks_the_loud_burst():
    energy = np.full(300, -40.0)
    energy[120:160] = -5.0  # a 40-second excited stretch
    picks = moments.top_moments(energy, clip_s=30, count=1, window_s=1.0)
    assert picks and 120 <= picks[0]["start"] <= 130


def test_top_moments_respects_minimum_gap():
    energy = np.full(400, -40.0)
    energy[50:90] = -5.0
    energy[200:240] = -6.0
    picks = moments.top_moments(energy, clip_s=30, count=3, min_gap_s=60, window_s=1.0)
    starts = sorted(p["start"] for p in picks)
    assert all(b - a >= 60 for a, b in zip(starts, starts[1:]))


def test_top_moments_too_short_returns_nothing():
    assert moments.top_moments(np.full(10, -20.0), clip_s=30) == []


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "c.sqlite"))


def test_metadata_only_sources_cannot_be_clipped(store, tmp_path):
    f = tmp_path / "x.mp4"
    f.write_bytes(b"0" * 10)
    with pytest.raises(ValueError):
        clip.register_source(store, "creator:a", str(f), "metadata_only")


def test_registered_source_needs_a_creator(store, tmp_path):
    f = tmp_path / "x.mp4"
    f.write_bytes(b"0" * 10)
    with pytest.raises(ValueError):
        clip.register_source(store, "", str(f), "creator_file")


def test_registration_is_recorded(store, tmp_path):
    f = tmp_path / "x.mp4"
    f.write_bytes(b"0" * 10)
    sid = clip.register_source(store, "creator:a", str(f), "creator_file", title="T")
    row = store.fetchone("SELECT * FROM sources WHERE source_id=?", (sid,))
    assert row["creator_id"] == "creator:a" and row["acquisition"] == "creator_file"
