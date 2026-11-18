"""Discovery of popular GTA VI streams and videos.

Sources:
  * YouTube Data API: search.list (100 units each, so the number of queries is capped)
    plus videos.list (1 unit per 50 ids) for statistics and live concurrency.
  * Twitch Helix API: only when TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET are set.
    Without them the Twitch source is skipped and logged, and nothing else changes.

Discovery only records candidates and snapshots. It never downloads or publishes anything.
Clipping a candidate requires a verified permission record for its creator (see rights.py).
"""
import os
from datetime import datetime, timezone

import requests

from . import scoring
from .store import now_iso

TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
TWITCH_API = "https://api.twitch.tv/helix"


def _hours_between(a_iso, b_iso):
    a = datetime.fromisoformat(a_iso)
    b = datetime.fromisoformat(b_iso)
    return max((b - a).total_seconds() / 3600, 1e-6)


def upsert_candidate(store, cid, platform, creator_id, title, url, is_live):
    ts = now_iso()
    store.execute(
        "INSERT INTO candidates (id, platform, creator_id, title, url, is_live, first_seen, last_seen, status)"
        " VALUES (?,?,?,?,?,?,?,?, 'new')"
        " ON CONFLICT(id) DO UPDATE SET title=excluded.title, url=excluded.url, is_live=excluded.is_live,"
        " last_seen=excluded.last_seen",
        (cid, platform, creator_id, title, url, int(bool(is_live)), ts, ts),
    )
    store.enqueue_job(f"score:{cid}", "score", {"candidate_id": cid})


def record_snapshot_and_score(store, cid, views, likes=None, comments=None, concurrent=None):
    ts = now_iso()
    store.execute(
        "INSERT OR REPLACE INTO snapshots (candidate_id, ts, views, likes, comments, concurrent_viewers)"
        " VALUES (?,?,?,?,?,?)",
        (cid, ts, views, likes, comments, concurrent),
    )
    rows = store.fetchall(
        "SELECT ts, COALESCE(views, concurrent_viewers, 0) AS v FROM snapshots WHERE candidate_id=? ORDER BY ts",
        (cid,),
    )
    first_ts = rows[0]["ts"]
    samples = [(_hours_between(first_ts, r["ts"]), r["v"]) for r in rows]
    if len(samples) == 1:
        # One sample: no velocity yet. Engagement alone is still a usable signal.
        samples_for_signals = []
    else:
        samples_for_signals = samples
    sig = scoring.signals_from_samples(samples_for_signals, likes=likes, comments=comments, views=views)
    if concurrent is not None and sig["view_velocity"] is None:
        sig["view_velocity"] = scoring.normalize_velocity(concurrent)
    result = scoring.compute(sig)
    store.execute(
        "UPDATE candidates SET score=?, score_confidence=?, signals_json=?, status='scored', last_seen=?"
        " WHERE id=?",
        (result["score"], result["confidence"], _json(result["used"]), ts, cid),
    )
    return result


def _json(obj):
    import json

    return json.dumps(obj, sort_keys=True)


# ---------------------------------------------------------------- YouTube
def youtube_scan(store, yt, queries, max_results=25):
    """Run each query once per scan. Returns the number of new or updated candidates."""
    found = 0
    for q in queries:
        call = yt.service.search().list(
            part="snippet", q=q, type="video", order="viewCount", maxResults=max_results, safeSearch="none",
        )
        data = yt.request("search.list", call)
        ids = [i["id"]["videoId"] for i in data.get("items", []) if i.get("id", {}).get("videoId")]
        for k in range(0, len(ids), 50):
            found += _ingest_youtube_videos(store, yt, ids[k:k + 50])
    return found


def _ingest_youtube_videos(store, yt, ids):
    data = yt.request(
        "videos.list",
        yt.service.videos().list(part="snippet,statistics,liveStreamingDetails", id=",".join(ids), maxResults=50),
    )
    n = 0
    for v in data.get("items", []):
        vid = v["id"]
        sn = v["snippet"]
        live = v.get("liveStreamingDetails", {})
        cid = f"youtube:{vid}"
        upsert_candidate(store, cid, "youtube", f"youtube-channel:{sn['channelId']}", sn["title"],
                         f"https://www.youtube.com/watch?v={vid}", bool(live.get("actualStartTime") and not live.get("actualEndTime")))
        st = v.get("statistics", {})
        record_snapshot_and_score(
            store, cid,
            views=int(st.get("viewCount", 0)),
            likes=int(st["likeCount"]) if "likeCount" in st else None,
            comments=int(st["commentCount"]) if "commentCount" in st else None,
            concurrent=int(live["concurrentViewers"]) if "concurrentViewers" in live else None,
        )
        n += 1
    return n


def youtube_refresh(store, yt, limit=50):
    """Refresh the most recently seen candidates. One videos.list call covers up to 50 ids."""
    rows = store.fetchall(
        "SELECT id FROM candidates WHERE platform='youtube' AND status IN ('new','scored')"
        " ORDER BY last_seen DESC LIMIT ?", (limit,),
    )
    ids = [r["id"].split(":", 1)[1] for r in rows]
    if not ids:
        return 0
    return _ingest_youtube_videos(store, yt, ids)


# ---------------------------------------------------------------- Twitch
class TwitchClient:
    def __init__(self, client_id, client_secret, session=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.session = session or requests.Session()
        self._token = None

    @classmethod
    def from_env(cls):
        cid, secret = os.environ.get("TWITCH_CLIENT_ID"), os.environ.get("TWITCH_CLIENT_SECRET")
        return cls(cid, secret) if cid and secret else None

    def _auth(self):
        if self._token is None:
            r = self.session.post(TWITCH_TOKEN_URL, data={
                "client_id": self.client_id, "client_secret": self.client_secret,
                "grant_type": "client_credentials"}, timeout=30)
            r.raise_for_status()
            self._token = r.json()["access_token"]
        return {"Client-Id": self.client_id, "Authorization": f"Bearer {self._token}"}

    def get(self, path, params):
        r = self.session.get(f"{TWITCH_API}/{path}", params=params, headers=self._auth(), timeout=30)
        r.raise_for_status()
        return r.json()

    def category_id(self, name):
        for c in self.get("search/categories", {"query": name, "first": 10}).get("data", []):
            if c.get("name", "").lower() == name.lower():
                return c["id"]
        return None

    def live_streams(self, game_id, first=100):
        return self.get("streams", {"game_id": game_id, "first": min(first, 100)}).get("data", [])


def twitch_scan(store, client, category_name, max_streams=50):
    game_id = client.category_id(category_name)
    if not game_id:
        store.log("discovery", "twitch category not found", level="warning", category=category_name)
        return 0
    n = 0
    for s in client.live_streams(game_id, first=max_streams):
        cid = f"twitch:{s['id']}"
        upsert_candidate(store, cid, "twitch", f"twitch-user:{s['user_id']}", s.get("title", ""),
                         f"https://www.twitch.tv/{s['user_login']}", True)
        record_snapshot_and_score(store, cid, views=None, concurrent=int(s.get("viewer_count", 0)))
        n += 1
    return n
