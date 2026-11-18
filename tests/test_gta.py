"""Tests for the GTA VI pipeline. Run with: python -m pytest -q tests

No network access: YouTube and Twitch are replaced by fakes. Rendering uses the local ffmpeg.
"""
import copy
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from gta import discovery, publisher, rights, scoring, templates
from gta.config import load_config
from gta.quota import QuotaExceeded, QuotaLedger
from gta.store import Store

CFG = load_config()


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "t.sqlite"))


# ------------------------------------------------------------ fakes
class FakeCall:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return self._data(None) if callable(self._data) else self._data


class FakeResource:
    def __init__(self, responses, name):
        self.responses, self.name = responses, name

    def _call(self, verb, **kw):
        handler = self.responses.get((self.name, verb))
        if handler is None:
            raise AssertionError(f"unexpected call {self.name}.{verb}")
        return FakeCall(handler(kw))

    def list(self, **kw):
        return self._call("list", **kw)

    def insert(self, **kw):
        return self._call("insert", **kw)


class FakeService:
    def __init__(self, responses):
        self.responses = responses

    def search(self):
        return FakeResource(self.responses, "search")

    def videos(self):
        return FakeResource(self.responses, "videos")

    def playlists(self):
        return FakeResource(self.responses, "playlists")

    def playlistItems(self):
        return FakeResource(self.responses, "playlistItems")


class FakeYT:
    """Mimics YouTubeClient: quota is enforced by the real ledger."""

    def __init__(self, store, responses=None, quota_cfg=None):
        # External usage is zeroed here so tests exercise publishing logic, not today's real budget.
        qc = copy.deepcopy(quota_cfg or CFG["quota"])
        if quota_cfg is None:
            qc["external_usage_by_day"] = {}
        self.quota = QuotaLedger(store, qc)
        self.service = FakeService(responses or {})

    def request(self, method, call, times=1):
        self.quota.spend(method, times=times)
        return call.execute()


# ------------------------------------------------------------ rights
def test_missing_record_is_denied(store):
    ok, reason = rights.check(store, "creator-x")
    assert not ok and "no permission" in reason


def test_pending_and_denied_never_publish(store):
    rights.upsert_permission(store, "c1", "pending")
    assert rights.check(store, "c1")[0] is False
    rights.upsert_permission(store, "c1", "denied")
    assert rights.check(store, "c1")[0] is False


def test_verified_requires_complete_fields(store):
    with pytest.raises(rights.RightsError):
        rights.upsert_permission(store, "c2", "verified", permission_source="email")


def test_verified_then_expired(store):
    future = (date.today() + timedelta(days=30)).isoformat()
    rights.upsert_permission(store, "c3", "verified", permission_source="written licence",
                             permission_date="2026-11-01", attribution="@creator",
                             expires_at=future, monetization_allowed=1)
    assert rights.check(store, "c3") == (True, "verified")
    past = (date.today() - timedelta(days=1)).isoformat()
    rights.upsert_permission(store, "c3", "verified", permission_source="written licence",
                             permission_date="2026-11-01", attribution="@creator",
                             expires_at=past, monetization_allowed=1)
    ok, reason = rights.check(store, "c3")
    assert not ok and "expired" in reason


def test_monetization_must_be_granted(store):
    rights.upsert_permission(store, "c4", "verified", permission_source="s", permission_date="2026-11-01",
                             attribution="a", expires_at="2099-01-01", monetization_allowed=0)
    ok, reason = rights.check(store, "c4")
    assert not ok and "monetization" in reason


# ------------------------------------------------------------ quota
def _tight_quota(shared_limit=1000, uploads_limit=100, search_limit=100, external=None):
    q = copy.deepcopy(CFG["quota"])
    q["safety_margin"] = 1.0
    q["buckets"]["shared"]["daily_limit"] = shared_limit
    q["buckets"]["uploads"]["daily_limit"] = uploads_limit
    q["buckets"]["search"]["daily_limit"] = search_limit
    q["external_usage_by_day"] = external or {}
    return q


def test_shared_quota_blocks_calls_that_do_not_fit(store):
    q = QuotaLedger(store, _tight_quota(shared_limit=40))
    with pytest.raises(QuotaExceeded) as e:
        q.spend("playlists.insert")  # 50 shared units, only 40 left
    assert e.value.bucket == "shared"
    assert q.used("shared") == 0  # nothing reserved for a refused call


def test_upload_bucket_is_separate_from_shared(store):
    q = QuotaLedger(store, _tight_quota(shared_limit=10000, uploads_limit=2))
    q.spend("videos.insert"); q.spend("videos.insert")
    with pytest.raises(QuotaExceeded) as e:
        q.spend("videos.insert")
    assert e.value.bucket == "uploads"
    assert q.remaining("shared") == 10000


def test_search_bucket_counts_calls_not_shared_units(store):
    q = QuotaLedger(store, _tight_quota(search_limit=3))
    for _ in range(3):
        q.spend("search.list")
    assert q.remaining("search") == 0 and q.remaining("shared") == 1000


def test_external_usage_counted_per_bucket(store):
    from gta.quota import pacific_day
    q = QuotaLedger(store, _tight_quota(external={pacific_day(): {"uploads": 5, "shared": 15}}))
    assert q.remaining("uploads") == 100 - 5
    assert q.remaining("shared") == 1000 - 15


def test_plan_day_for_publishing_and_discovery():
    from gta.quota import plan_day
    q = CFG["quota"]
    methods, limits = q["methods"], q["buckets"]
    fit = plan_day(7, 3, {"videos.list": 7, "playlistItems.insert": 7, "channels.list": 4},
                   methods, limits, margin=0.9, external={"shared": 15})
    assert fit["uploads"]["fits"] and fit["search"]["fits"] and fit["shared"]["fits"]
    # Legacy assumption: 1600 units per upload in the shared pool would not fit 7 Shorts.
    legacy = {**methods, "videos.insert": {"bucket": "shared", "units": 1600}}
    bad = plan_day(7, 0, {}, legacy, limits, margin=0.9, external={"shared": 8000})
    assert bad["shared"]["fits"] is False


def test_unknown_method_has_no_cost(store):
    q = QuotaLedger(store, CFG["quota"])
    with pytest.raises(KeyError):
        q.cost("made.up.method")


# ------------------------------------------------------------ store / dedupe
def test_jobs_are_inserted_once(store):
    assert store.enqueue_job("publish:x", "publish", {}) is True
    assert store.enqueue_job("publish:x", "publish", {}) is False


def test_restart_recovers_running_jobs(store):
    store.enqueue_job("scan:1", "scan")
    store.execute("UPDATE jobs SET status='running' WHERE job_key='scan:1'")
    assert store.requeue_stale_running() == 1
    assert store.fetchone("SELECT status FROM jobs WHERE job_key='scan:1'")["status"] == "queued"


# ------------------------------------------------------------ scoring
def test_missing_signals_are_excluded_not_fatal():
    res = scoring.compute({"view_velocity": 0.8, "chat_spike": None, "replay_heatmap": None})
    assert res["score"] == 80.0
    assert res["confidence"] == pytest.approx(0.2, abs=1e-3)


def test_no_signals_gives_no_score():
    assert scoring.compute({})["score"] is None


def test_score_in_range_and_weighted():
    res = scoring.compute({k: 1.0 for k in scoring.WEIGHTS})
    assert res["score"] == 100.0 and res["confidence"] == 1.0
    res = scoring.compute({k: 0.0 for k in scoring.WEIGHTS})
    assert res["score"] == 0.0


def test_velocity_and_acceleration_normalization():
    assert scoring.normalize_velocity(0) == 0
    assert scoring.normalize_velocity(1e9) == 1.0
    assert scoring.normalize_acceleration(100, 100) == 0
    assert scoring.normalize_acceleration(100, 500) == 1.0
    assert scoring.normalize_acceleration(None, 500) is None


def test_signals_from_samples_needs_points():
    sig = scoring.signals_from_samples([(0, 100), (1, 1000), (2, 3000)], likes=50, comments=10, views=3000)
    assert sig["view_velocity"] is not None and sig["view_acceleration"] is not None
    assert sig["engagement"] == pytest.approx(60 / 3000 / 0.08, rel=1e-3)
    assert scoring.signals_from_samples([], None, None, None)["view_velocity"] is None


# ------------------------------------------------------------ discovery
def test_youtube_scan_dedupes_and_snapshots(store):
    video_item = {"id": "abc123", "snippet": {"title": "GTA chase", "channelId": "UCx"},
                  "statistics": {"viewCount": "1000", "likeCount": "50", "commentCount": "5"},
                  "liveStreamingDetails": {}}
    responses = {
        ("search", "list"): lambda kw: {"items": [{"id": {"videoId": "abc123"}}]},
        ("videos", "list"): lambda kw: {"items": [video_item]},
    }
    yt = FakeYT(store, responses)
    discovery.youtube_scan(store, yt, ["GTA 6 chase"], 25)
    discovery.youtube_scan(store, yt, ["GTA 6 chase"], 25)  # same video again
    rows = store.fetchall("SELECT * FROM candidates")
    assert len(rows) == 1 and rows[0]["id"] == "youtube:abc123"
    assert store.fetchone("SELECT COUNT(*) AS n FROM snapshots")["n"] >= 1
    # two scans: each = one search.list (search bucket) + one videos.list (shared, 1 unit)
    assert yt.quota.used("search") == 2 and yt.quota.used("shared") == 2


class FakeTwitchSession:
    def __init__(self):
        self.calls = []

    def post(self, url, **kw):
        return _Resp({"access_token": "t"})

    def get(self, url, params=None, headers=None, **kw):
        self.calls.append(url)
        if url.endswith("search/categories"):
            return _Resp({"data": [{"id": "999", "name": "Grand Theft Auto VI"}]})
        return _Resp({"data": [{"id": "s1", "user_id": "u1", "user_login": "streamer", "title": "heist",
                                "viewer_count": 1234}]})


class _Resp:
    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._d


def test_twitch_scan_with_mocked_session(store):
    tw = discovery.TwitchClient("id", "secret", session=FakeTwitchSession())
    assert discovery.twitch_scan(store, tw, "Grand Theft Auto VI", 50) == 1
    row = store.fetchone("SELECT * FROM candidates WHERE id='twitch:s1'")
    assert row["is_live"] == 1 and row["creator_id"] == "twitch-user:u1"


def test_twitch_skipped_without_credentials(monkeypatch):
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)
    assert discovery.TwitchClient.from_env() is None


# ------------------------------------------------------------ templates & facts
def test_countdown_plan_structure():
    facts = templates.load_facts()
    plan = templates.countdown_plan(1, facts)
    assert 20 <= plan["duration_s"] <= 45
    assert plan["hook"] == ["1 DAY", "TO GTA VI"]
    assert plan["fact_ids"] == [f["id"] for f in facts]
    assert all(s["sources"] for s in plan["sources"] and facts)


def test_every_fact_has_sources():
    for f in templates.load_facts():
        assert f["sources"], f["id"]
        assert all(s["url"].startswith("https://") for s in f["sources"])


# ------------------------------------------------------------ publisher
def _add_fake_short(store, tmp_path, quality=75, fact_ids=("launch_date_2026_11_19",), creator="self:bounce-rumble"):
    d = tmp_path / "short"
    d.mkdir(exist_ok=True)
    video = d / "countdown.mp4"
    video.write_bytes(b"not really a video")
    return publisher.add_short(store, source_key=f"k:{len(fact_ids)}:{quality}", template="t", title="T",
                               description="D", tags=["a"], file_path=str(video), duration_s=21,
                               quality_score=quality, creator_id=creator, fact_ids=list(fact_ids))


def test_short_blocked_when_facts_unchecked(store, tmp_path, monkeypatch):
    monkeypatch.setattr(publisher, "load_facts", lambda: [{"id": "launch_date_2026_11_19", "primary_source_checked": False},
                                                          {"id": "platforms_ps5_xbox_series", "primary_source_checked": False}])
    rights.upsert_permission(store, "self:bounce-rumble", "verified", permission_source="own work",
                             permission_date="2026-11-18", attribution="own", expires_at="2099-01-01",
                             monetization_allowed=1)
    _add_fake_short(store, tmp_path)
    yt = FakeYT(store)
    cfg = copy.deepcopy(CFG)
    cfg["publishing"]["enabled"] = True
    cfg["publishing"]["dry_run"] = False
    res = publisher.publish_one(store, yt, cfg, "k:1:75", live=True)
    assert res["result"] == "blocked" and "primary source" in res["reason"]


def test_pending_rights_block_even_with_checked_facts(store, tmp_path, monkeypatch):
    monkeypatch.setattr(templates, "load_facts", lambda: [{"id": "f1", "primary_source_checked": True}])
    monkeypatch.setattr(publisher, "load_facts", lambda: [{"id": "f1", "primary_source_checked": True}])
    rights.upsert_permission(store, "creatorZ", "pending")
    _add_fake_short(store, tmp_path, fact_ids=("f1",), creator="creatorZ")
    res = publisher.publish_one(store, FakeYT(store), copy.deepcopy(CFG), "k:1:75", live=True)
    assert res["result"] == "blocked" and "rights" in res["reason"]


def test_dry_run_never_uploads(store, tmp_path, monkeypatch):
    # Hermetic: force dry-run regardless of the live config.
    monkeypatch.setattr(publisher, "load_facts", lambda: [{"id": "f1", "primary_source_checked": True}])
    rights.upsert_permission(store, "self:bounce-rumble", "verified", permission_source="own work",
                             permission_date="2026-11-18", attribution="own", expires_at="2099-01-01",
                             monetization_allowed=1)
    _add_fake_short(store, tmp_path, fact_ids=("f1",))
    called = []
    monkeypatch.setattr(publisher, "upload", lambda *a, **k: called.append(1) or "vid")
    dry = copy.deepcopy(CFG)
    dry["publishing"]["dry_run"] = True
    res = publisher.publish_one(store, FakeYT(store), dry, "k:1:75", live=True)
    assert res["result"] == "dry_run" and called == []


def test_live_publish_uploads_once_and_adds_to_playlist(store, tmp_path, monkeypatch):
    monkeypatch.setattr(publisher, "load_facts", lambda: [{"id": "f1", "primary_source_checked": True}])
    rights.upsert_permission(store, "self:bounce-rumble", "verified", permission_source="own work",
                             permission_date="2026-11-18", attribution="own", expires_at="2099-01-01",
                             monetization_allowed=1)
    _add_fake_short(store, tmp_path, fact_ids=("f1",))
    cfg = copy.deepcopy(CFG)
    cfg["publishing"]["enabled"] = True
    cfg["publishing"]["dry_run"] = False
    cfg["playlist"]["playlist_id"] = "PLtest"
    uploads = []
    monkeypatch.setattr(publisher, "upload", lambda yt, s, c: uploads.append(s["source_key"]) or "VID1")
    inserted = []
    responses = {
        ("playlistItems", "insert"): lambda kw: inserted.append(kw["body"]["snippet"]["resourceId"]["videoId"]) or {},
        # Default config privacy is "private" (owner reviews before going public), so verify against it.
        ("videos", "list"): lambda kw: {"items": [{"status": {"privacyStatus": cfg["publishing"]["privacy_status"]}}]},
    }
    yt = FakeYT(store, responses)
    res = publisher.publish_one(store, yt, cfg, "k:1:75", live=True)
    assert res["result"] == "published" and res["youtube_id"] == "VID1" and res["verified"] is True
    assert inserted == ["VID1"]
    # A second run must not upload again.
    again = publisher.publish_one(store, yt, cfg, "k:1:75", live=True)
    assert again["result"] == "already_published" and uploads == ["k:1:75"]


def test_daily_cap_blocks_extra_publishes(store, tmp_path, monkeypatch):
    monkeypatch.setattr(publisher, "load_facts", lambda: [{"id": "f1", "primary_source_checked": True}])
    rights.upsert_permission(store, "self:bounce-rumble", "verified", permission_source="own work",
                             permission_date="2026-11-18", attribution="own", expires_at="2099-01-01",
                             monetization_allowed=1)
    cfg = copy.deepcopy(CFG)
    cfg["publishing"]["max_shorts_per_day"] = 1
    store.execute("INSERT INTO shorts (source_key, status, updated_at, creator_id) VALUES ('old','published',?, 'x')",
                  (publisher.now_iso(),))
    _add_fake_short(store, tmp_path, fact_ids=("f1",))
    ok, reason = publisher.eligibility(store, cfg, store.fetchone("SELECT * FROM shorts WHERE source_key='k:1:75'"),
                                       FakeYT(store).quota)
    assert not ok and "daily cap" in reason


def test_schedule_slots_are_future_and_ordered():
    cfg = copy.deepcopy(CFG)
    slots = publisher.schedule_slots(cfg, 4)
    assert len(slots) == 4 and slots == sorted(slots)


# ------------------------------------------------------------ render
def test_render_countdown_meets_spec(tmp_path):
    from gta.render import render

    plan = templates.countdown_plan(1, templates.load_facts())
    info = render(plan, tmp_path / "out.mp4", tmp_path / "work", CFG)
    assert (info["width"], info["height"]) == (1080, 1920)
    assert 20 <= info["duration_s"] <= 45
    assert info["audio_codec"] == "aac"
    assert abs(float(info["loudness"]["input_i"]) + 14) < 1.5


def test_render_rejects_out_of_range_duration(tmp_path):
    from gta.render import RenderError, render

    plan = templates.countdown_plan(1, templates.load_facts())
    plan["duration_s"] = 10
    with pytest.raises(RenderError):
        render(plan, tmp_path / "x.mp4", tmp_path / "w", CFG)
