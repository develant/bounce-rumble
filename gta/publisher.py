"""Publishing queue for rendered Shorts.

A Short is published only when ALL of these hold (checked in `eligibility`):
  1. its creator has a verified, unexpired permission record (rights.py),
  2. every fact it uses is marked primary_source_checked in content/facts.json,
  3. its quality score meets publishing.min_quality_score,
  4. the daily cap (publishing.max_shorts_per_day, Pacific day) is not reached,
  5. the quota ledger can afford videos.insert + playlistItems.insert.

Publishing is dry-run by default (config publishing.dry_run = true). A live upload needs
publishing.enabled = true AND dry_run = false AND an explicit `--live` flag on the runner.
Uploads are private with publishAt when the Short has a scheduled time, and become public
on that time. Each Short is uploaded at most once (unique source_key, and a youtube_id
guard). Failures back off exponentially and are retried by the runner.
"""
import json
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from . import rights
from .quota import QuotaExceeded, pacific_day
from .store import now_iso
from .templates import load_facts

PACIFIC = ZoneInfo("America/Los_Angeles")
RETRYABLE = {500, 502, 503, 504}


class PublishError(RuntimeError):
    pass


def add_short(store, source_key, template, title, description, tags, file_path, duration_s,
              quality_score, creator_id, fact_ids, publish_at=None):
    """Queue a rendered Short once. Returns False if source_key already exists (no duplicates)."""
    ts = now_iso()
    plan_path = str(file_path).rsplit("/", 1)[0] + "/plan.json"
    try:
        cur = store.execute(
            "INSERT OR IGNORE INTO shorts (source_key, template, title, description, tags_json, file_path,"
            " duration_s, quality_score, creator_id, status, publish_at, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,'queued',?,?,?)",
            (source_key, template, title, description, json.dumps(tags), str(file_path), duration_s,
             quality_score, creator_id, publish_at, ts, ts),
        )
    except Exception:
        raise
    if cur.rowcount == 1:
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump({"fact_ids": fact_ids}, f)
        store.enqueue_job(f"publish:{source_key}", "publish", {"source_key": source_key})
        store.log("publisher", "short queued", source_key=source_key)
        return True
    return False


def _pacific_day_start_utc():
    now = datetime.now(PACIFIC)
    start = datetime.combine(now.date(), time(0, 0), tzinfo=PACIFIC)
    return start.astimezone(timezone.utc).isoformat(timespec="seconds")


def published_today(store):
    row = store.fetchone(
        "SELECT COUNT(*) AS n FROM shorts WHERE status='published' AND updated_at>=?",
        (_pacific_day_start_utc(),),
    )
    return int(row["n"])


def _unchecked_facts(fact_ids):
    facts = {f["id"]: f for f in load_facts()}
    return [fid for fid in fact_ids if fid not in facts or not facts[fid].get("primary_source_checked")]


def eligibility(store, cfg, short, quota):
    """Return (ok, reason). Each check is independent and default-deny."""
    pub = cfg["publishing"]
    ok, reason = rights.check(store, short["creator_id"])
    if not ok:
        return False, f"rights: {reason}"
    try:
        fact_ids = json.load(open(short["file_path"].rsplit("/", 1)[0] + "/plan.json", encoding="utf-8"))["fact_ids"]
    except (OSError, KeyError, ValueError):
        return False, "plan.json with fact list missing"
    unchecked = _unchecked_facts(fact_ids)
    if unchecked:
        return False, f"facts not checked against a primary source: {', '.join(unchecked)}"
    if short["quality_score"] is None or short["quality_score"] < pub["min_quality_score"]:
        return False, f"quality {short['quality_score']} below {pub['min_quality_score']}"
    if published_today(store) >= pub["max_shorts_per_day"]:
        return False, "daily cap reached"
    if not quota.can_spend("videos.insert") or not quota.can_spend("playlistItems.insert"):
        return False, "quota: not enough units left today"
    return True, "eligible"


def _body(short, cfg):
    privacy = "private" if short.get("publish_at") else cfg["publishing"]["privacy_status"]
    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False, "containsSyntheticMedia": False}
    if short.get("publish_at"):
        status["publishAt"] = short["publish_at"]
    return {
        "snippet": {
            "title": short["title"][:100],
            "description": short["description"][:5000],
            "tags": json.loads(short["tags_json"] or "[]")[:15],
            "categoryId": cfg["publishing"]["category_id"],
            "defaultLanguage": cfg["publishing"]["default_language"],
            "defaultAudioLanguage": cfg["publishing"]["default_language"],
        },
        "status": status,
    }


def upload(yt, short, cfg):
    """Resumable upload with exponential backoff on transient errors. Returns the video id."""
    import random
    import time as _t

    media = MediaFileUpload(short["file_path"], mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    request = yt.service.videos().insert(part="snippet,status", body=_body(short, cfg), media_body=media)
    yt.quota.spend("videos.insert")
    response, retries = None, 0
    while response is None:
        try:
            _, response = request.next_chunk()
        except HttpError as e:
            if e.resp.status not in RETRYABLE or retries >= cfg["publishing"]["upload_retry_limit"]:
                raise
            retries += 1
            _t.sleep(min(60, 2 ** retries) + random.random())
    return response["id"]


def verify(yt, video_id, cfg, short):
    """Upload verification: the video exists, is processed, and has the expected privacy state."""
    item = yt.request("videos.list", yt.service.videos().list(part="status", id=video_id))
    items = item.get("items", [])
    if not items:
        raise PublishError(f"video {video_id} not found after upload")
    st = items[0]["status"]
    expected = "private" if short.get("publish_at") else cfg["publishing"]["privacy_status"]
    if st.get("privacyStatus") != expected and not short.get("publish_at"):
        raise PublishError(f"privacy {st.get('privacyStatus')} != {expected}")
    return st


def add_to_playlist(yt, playlist_id, video_id):
    body = {"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}
    yt.request("playlistItems.insert", yt.service.playlistItems().insert(part="snippet", body=body))


def publish_one(store, yt, cfg, source_key, live):
    """Process one queued Short. Returns a short result dict. Never publishes without `live`."""
    short = store.fetchone("SELECT * FROM shorts WHERE source_key=?", (source_key,))
    if not short:
        raise PublishError(f"unknown short {source_key}")
    if short["youtube_id"] or short["status"] == "published":
        return {"source_key": source_key, "result": "already_published", "youtube_id": short["youtube_id"]}

    ok, reason = eligibility(store, cfg, short, yt.quota)
    if not ok:
        store.execute("UPDATE shorts SET status='blocked', last_error=?, updated_at=? WHERE source_key=?",
                      (reason, now_iso(), source_key))
        store.mark_job(f"publish:{source_key}", "failed", error=reason, backoff_seconds=3600)
        store.log("publisher", "blocked", level="warning", source_key=source_key, reason=reason)
        return {"source_key": source_key, "result": "blocked", "reason": reason}

    if not (live and cfg["publishing"]["enabled"] and not cfg["publishing"]["dry_run"]):
        return {"source_key": source_key, "result": "dry_run", "would_publish": _body(short, cfg)["snippet"]["title"],
                "status": _body(short, cfg)["status"]}

    store.execute("UPDATE shorts SET status='uploading', attempts=attempts+1, updated_at=? WHERE source_key=?",
                  (now_iso(), source_key))
    try:
        video_id = upload(yt, short, cfg)
    except QuotaExceeded as e:
        # Not a failure of the Short: wait until quota resets (next Pacific midnight).
        store.execute("UPDATE shorts SET status='queued', last_error=?, updated_at=? WHERE source_key=?",
                      (str(e), now_iso(), source_key))
        store.mark_job(f"publish:{source_key}", "failed", error=str(e), backoff_seconds=3600)
        return {"source_key": source_key, "result": "deferred_quota", "reason": str(e)}
    except (HttpError, PublishError) as e:
        attempts = short["attempts"] + 1
        store.execute("UPDATE shorts SET status='failed', last_error=?, updated_at=? WHERE source_key=?",
                      (str(e)[:500], now_iso(), source_key))
        store.mark_job(f"publish:{source_key}", "failed", error=str(e)[:500],
                       backoff_seconds=min(6 * 3600, 60 * 2 ** attempts))
        store.log("publisher", "upload failed", level="error", source_key=source_key, error=str(e)[:300])
        return {"source_key": source_key, "result": "failed", "reason": str(e)[:300]}

    # Record the id immediately so a crash after upload is never re-uploaded.
    store.execute("UPDATE shorts SET youtube_id=?, status='published', updated_at=? WHERE source_key=?",
                  (video_id, now_iso(), source_key))
    store.mark_job(f"publish:{source_key}", "done")
    store.log("publisher", "uploaded", source_key=source_key, youtube_id=video_id)

    playlist_id = cfg["playlist"]["playlist_id"]
    playlist_note = "added"
    try:
        if playlist_id and yt.quota.can_spend("playlistItems.insert"):
            add_to_playlist(yt, playlist_id, video_id)
        else:
            playlist_note = "pending"
            store.enqueue_job(f"playlist:{video_id}", "playlist", {"video_id": video_id})
    except (HttpError, QuotaExceeded) as e:
        playlist_note = "pending"
        store.enqueue_job(f"playlist:{video_id}", "playlist", {"video_id": video_id})
        store.log("publisher", "playlist add deferred", level="warning", youtube_id=video_id, error=str(e)[:300])
    try:
        verify(yt, video_id, cfg, short)
        verified = True
    except (HttpError, PublishError) as e:
        verified = False
        store.log("publisher", "verification failed", level="warning", youtube_id=video_id, error=str(e)[:300])
    return {"source_key": source_key, "result": "published", "youtube_id": video_id,
            "playlist": playlist_note, "verified": verified,
            "url": f"https://youtube.com/shorts/{video_id}"}


def schedule_slots(cfg, n, start_utc=None):
    """Next n publish times from publishing.schedule_utc_hours, one per slot, never in the past."""
    hours = cfg["publishing"].get("schedule_utc_hours", [14, 18, 22])
    t = (start_utc or datetime.now(timezone.utc)) + timedelta(minutes=10)
    out = []
    day = t.date()
    while len(out) < n:
        for h in hours:
            slot = datetime.combine(day, time(h, 0), tzinfo=timezone.utc)
            if slot > t and len(out) < n:
                out.append(slot.isoformat(timespec="seconds").replace("+00:00", "Z"))
        day += timedelta(days=1)
    return out
