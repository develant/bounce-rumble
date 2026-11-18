"""Tests for Twitch discovery/authorization, YouTube live chat spikes, owner ingest, and orchestration.
All network access is replaced with fakes."""
import json
import os

import pytest

from gta import authz, ingest, scoring
from gta.sources import twitch, youtube_live
from gta.store import Store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("GTA_STATE_DIR", str(tmp_path / "state"))
    return Store(str(tmp_path / "s.sqlite"))


class R:
    def __init__(self, status, data=None):
        self.status_code, self._d = status, data or {}

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def post(self, url, **kw):
        self.calls.append(("POST", url))
        return self.routes["POST " + url](kw)

    def get(self, url, params=None, headers=None, **kw):
        self.calls.append(("GET", url, params))
        return self.routes["GET " + url](params)


def _app_token(kw):
    return R(200, {"access_token": "APP", "expires_in": 3600})


def test_discover_clips_follows_pagination():
    pages = [{"data": [{"id": "a"}], "pagination": {"cursor": "c1"}},
             {"data": [{"id": "b"}], "pagination": {}}]
    seq = iter(pages)
    s = FakeSession({"POST https://id.twitch.tv/oauth2/token": _app_token,
                     "GET https://api.twitch.tv/helix/clips": lambda p: R(200, next(seq))})
    tw = twitch.TwitchAPI("cid", "sec", session=s)
    clips = tw.discover_clips("999", "2026-11-11T00:00:00Z")
    assert [c["id"] for c in clips] == ["a", "b"]


def test_download_urls_prefers_landscape_and_handles_missing():
    def route(params):
        if params["id"] == "good":
            return R(200, {"data": [{"landscape_download_url": "https://x/land.mp4"}]})
        return R(200, {"data": []})
    s = FakeSession({"POST https://id.twitch.tv/oauth2/token": _app_token,
                     "GET https://api.twitch.tv/helix/clips/download": route})
    tw = twitch.TwitchAPI("cid", "sec", session=s)
    assert tw.download_urls(["good", "missing"], "USER") == {"good": "https://x/land.mp4"}


def test_clip_to_candidate_normalizes():
    c = twitch.clip_to_candidate({"id": "1", "broadcaster_id": "42", "view_count": "17", "duration": "30.5",
                                  "title": "t", "url": "u"})
    assert c["creator_id"] == "twitch-user:42" and c["view_count"] == 17 and c["duration"] == 30.5


def test_authorization_is_per_creator_and_optional(store):
    assert authz.status(store, "twitch-user:1") == "none"
    authz.grant(store, "twitch-user:1", "twitch", {"access_token": "t", "refresh_token": "r", "expires_at": 9e12})
    assert authz.status(store, "twitch-user:1") == "granted"
    assert authz.status(store, "twitch-user:2") == "none"  # other creators unaffected
    tokfile = authz._token_path("twitch-user:1")
    assert oct(os.stat(tokfile).st_mode & 0o777) == "0o600"
    authz.revoke(store, "twitch-user:1")
    assert authz.status(store, "twitch-user:1") == "revoked"
    assert not tokfile.exists()


def test_popularity_and_velocity():
    vel = scoring.velocity_per_hour([(0, 1000), (3600, 3000)])
    assert vel == pytest.approx(2000)
    high = scoring.popularity_score(500_000, 50_000)["score"]
    low = scoring.popularity_score(500, 0)["score"]
    assert high > low
    res = scoring.popularity_score(5000)
    assert res["confidence"] == 0.5  # no velocity: views only, reported as lower confidence


def test_twitch_candidate_without_authorization_stays_ranked(store):
    store.execute("INSERT INTO candidates (id, platform, creator_id, title, url, status) VALUES "
                  "('twitch_clip:c1','twitch_clip','twitch-user:9','t','u','discovered')")
    s = FakeSession({"POST https://id.twitch.tv/oauth2/token": _app_token})
    tw = twitch.TwitchAPI("cid", "sec", session=s)
    result = ingest.acquire_twitch(store, tw, "twitch_clip:c1", "c1", "twitch-user:9")
    assert result == "no_authorization"
    row = store.fetchone("SELECT status FROM candidates WHERE id='twitch_clip:c1'")
    assert row["status"] == "ranked_no_file"  # still discovered and ranked, not blocked globally


def test_authorized_creator_downloads_and_registers_source(store, tmp_path, monkeypatch):
    authz.grant(store, "twitch-user:5", "twitch", {"access_token": "T", "refresh_token": "r", "expires_at": 9e12})
    store.execute("INSERT INTO candidates (id, platform, creator_id, title, url, status) VALUES "
                  "('twitch_clip:c5','twitch_clip','twitch-user:5','t','u','discovered')")
    s = FakeSession({"POST https://id.twitch.tv/oauth2/token": _app_token,
                     "GET https://api.twitch.tv/helix/clips/download":
                         lambda p: R(200, {"data": [{"landscape_download_url": "https://x/c5.mp4"}]})})
    tw = twitch.TwitchAPI("cid", "sec", session=s)
    dest = tmp_path / "c5.mp4"
    dest.write_bytes(b"0" * 100)
    monkeypatch.setattr(ingest, "_download", lambda url, d: dest)
    assert ingest.acquire_twitch(store, tw, "twitch_clip:c5", "c5", "twitch-user:5") == "acquired"
    src = store.fetchone("SELECT * FROM sources WHERE creator_id='twitch-user:5'")
    assert src["acquisition"] == "creator_file"
    assert store.fetchone("SELECT status FROM candidates WHERE id='twitch_clip:c5'")["status"] == "acquired"


def test_chat_spikes_detected_from_recorded_buckets(store):
    stamps = [1000 + i for i in range(60)] * 1  # 1 message/second baseline for 60 s = 6 per 10 s bucket
    youtube_live.record_chat_messages(store, "v1", stamps, bucket_s=10)
    burst = [1200 + (i % 10) for i in range(120)]  # 120 messages in one 10 s bucket
    youtube_live.record_chat_messages(store, "v1", burst, bucket_s=10)
    youtube_live.record_chat_messages(store, "v1", [1300 + i * 10 for i in range(5)], bucket_s=10)
    spikes = youtube_live.detect_spikes(store, "v1", min_messages=20, ratio=3.0)
    assert spikes and spikes[0]["start"] == 1200 and spikes[0]["messages"] >= 120


def test_owner_folder_ingest_is_idempotent(store, tmp_path):
    folder = tmp_path / "owner"
    folder.mkdir()
    (folder / "rec1.mp4").write_bytes(b"x" * 50)
    first = ingest.ingest_owner_folder(store, folder)
    second = ingest.ingest_owner_folder(store, folder)
    assert first == second and len(first) == 1
    row = store.fetchone("SELECT acquisition, creator_id FROM sources WHERE source_id=?", (first[0],))
    assert row["acquisition"] == "owned" and row["creator_id"] == ingest.OWNER_CREATOR


def test_process_sources_runs_each_source_once(store, tmp_path, monkeypatch):
    folder = tmp_path / "owner"
    folder.mkdir()
    (folder / "rec.mp4").write_bytes(b"x" * 50)
    ingest.ingest_owner_folder(store, folder)
    calls = []
    monkeypatch.setattr(ingest.clip, "build_candidates", lambda *a, **k: calls.append(1) or [])
    ingest.process_sources(store, {}, cuts=1)
    ingest.process_sources(store, {}, cuts=1)
    assert len(calls) == 1  # job key prevents processing the same source twice
