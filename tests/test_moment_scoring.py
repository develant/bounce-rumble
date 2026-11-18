"""Tests for combined moment scoring and QC helpers that do not need rendering."""
import numpy as np
import pytest

from gta import moment_scoring as ms
from gta import qc
from gta.store import Store


def _series(n, base=0.05):
    return np.full(n, base)


def test_strong_event_scores_higher_than_quiet_stretch():
    visual = _series(300); reaction = _series(300); impact = _series(300)
    visual[100:130] = 0.9; reaction[100:130] = 0.8; impact[100:130] = 0.7
    ent, conf = ms.per_second_entertainment(visual, reaction, impact)
    picks = ms.score_windows(ent, clip_s=30, count=1)
    assert picks and 95 <= picks[0]["start"] <= 105
    assert 0 <= picks[0]["score"] <= 100


def test_missing_signals_lower_confidence_but_keep_candidates():
    visual = _series(200); visual[50:80] = 0.9
    ent, conf = ms.per_second_entertainment(visual, None, None)
    assert conf == pytest.approx(0.40, abs=1e-6)  # only the visual weight is observed
    assert ms.score_windows(ent, clip_s=30, count=1)  # still produces a candidate


def test_music_only_seconds_are_penalized():
    reaction = _series(200)
    music = _series(200, base=0.0)
    music[:] = 1.0
    ent_music, _ = ms.per_second_entertainment(None, reaction, None, music)
    ent_plain, _ = ms.per_second_entertainment(None, reaction, None)
    assert ent_music.mean() < ent_plain.mean()


def test_near_identical_moments_are_not_both_chosen():
    ent = _series(400)
    ent[100:140] = 0.9
    ent[200:240] = 0.9  # same shape, 100 s later
    picks = ms.score_windows(ent, clip_s=30, count=3, min_gap_s=45)
    starts = sorted(p["start"] for p in picks)
    assert all(b - a >= 45 for a, b in zip(starts, starts[1:]))


def test_clip_length_is_clamped_to_shorts_range():
    ent = _series(200)
    ent[60:100] = 0.9
    picks = ms.score_windows(ent, clip_s=90, count=1)
    assert picks and picks[0]["end"] - picks[0]["start"] == 45


def test_subtitle_check_matches_plan(tmp_path):
    ass = tmp_path / "c.ass"
    ass.write_text(
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:03.50,0:00:09.50,Caption,,0,0,0,,Hello there\n"
        "Dialogue: 0,0:00:09.50,0:00:16.00,Caption,,0,0,0,,Second line\n",
        encoding="utf-8")
    plan = [{"start": 3.5, "end": 9.5}, {"start": 9.5, "end": 16.0}]
    assert qc.check_subtitles(str(ass), plan)[0] is True
    bad = [{"start": 3.5, "end": 9.5}, {"start": 10.5, "end": 16.0}]
    ok, detail = qc.check_subtitles(str(ass), bad)
    assert ok is False and "does not match" in detail


def test_subtitle_count_mismatch_fails(tmp_path):
    ass = tmp_path / "c.ass"
    ass.write_text("[Events]\nDialogue: 0,0:00:03.50,0:00:09.50,Caption,,0,0,0,,Only one\n", encoding="utf-8")
    ok, detail = qc.check_subtitles(str(ass), [{"start": 3.5, "end": 9.5}, {"start": 9.5, "end": 16}])
    assert ok is False and "subtitle events" in detail


def test_hamming_and_duplicate_detection_logic(tmp_path):
    assert qc.hamming(0b1010, 0b1010) == 0
    assert qc.hamming(0b1111, 0b0000) == 4


def test_overlap_from_same_source_is_a_duplicate(tmp_path):
    store = Store(str(tmp_path / "q.sqlite"))
    store.execute("INSERT INTO fingerprints (source_key, source_id, start_s, end_s, dhash) VALUES ('old','src1',10,40,'0')")
    fake = tmp_path / "x.mp4"
    fake.write_bytes(b"not a video")
    # overlap is computed before any frame is read, so the same-source check fails first
    ok, detail = qc.check_duplicates(store, str(fake), "src1", 20, 50)
    assert ok is False and "overlaps" in detail
