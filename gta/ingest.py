"""Orchestration: discovery -> scoring -> acquisition -> existing local clip pipeline.

Stages are independent. A candidate without a file stays discovered and ranked. Acquisition needs
both (a) the creator's own authorization for official download, when the source is Twitch clips,
and (b) a local file. Publishing is not decided here: clips reach publisher.py, and the rights
gate there blocks anything without a verified permission record.
"""
import datetime as dt
import json
import os
import time
from pathlib import Path

import requests

from . import authz, clip, scoring
from .config import PKG_DIR
from .store import now_iso

OWNER_CREATOR = "self:bounce-rumble"
MAX_DOWNLOAD_BYTES = 300 * 1024 * 1024
DOWNLOAD_DIR = PKG_DIR / "ingest" / "downloads"
OWNER_DIR = PKG_DIR / "ingest" / "owner"


def _epoch(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def discover_twitch(store, cfg, tw, game_name=None):
    """Helix Get Clips for the configured game. Uses only the app token. Returns clips recorded."""
    tcfg = cfg["twitch"]
    name = game_name or tcfg["game_name"]
    game_id = tw.game_id(name) or tw.game_id(tcfg["game_name_fallback"])
    if not game_id:
        store.log("discovery", "twitch game not found", level="warning", game=name)
        return 0
    started = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=tcfg["discovery_window_days"]))
    clips = tw.discover_clips(game_id, started.strftime("%Y-%m-%dT%H:%M:%SZ"), max_clips=tcfg["max_clips_per_scan"])
    n = 0
    for raw in clips:
        c = authz_free_clip(raw)
        store.execute(
            "INSERT INTO candidates (id, platform, creator_id, title, url, is_live, first_seen, last_seen, status)"
            " VALUES (?,?,?,?,?,0,?,?,'discovered') ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen,"
            " title=excluded.title",
            (f"twitch_clip:{c['id']}", "twitch_clip", c["creator_id"], c["title"], c["url"], now_iso(), now_iso()))
        store.execute("INSERT OR REPLACE INTO snapshots (candidate_id, ts, views, likes, comments, concurrent_viewers)"
                      " VALUES (?,?,?,NULL,NULL,NULL)",
                      (f"twitch_clip:{c['id']}", now_iso(), c["view_count"]))
        n += 1
    return n


def authz_free_clip(raw):
    from .sources.twitch import clip_to_candidate

    return clip_to_candidate(raw)


def score_all(store):
    """Popularity and velocity for every discovered candidate. Candidates without snapshots are skipped."""
    rows = store.fetchall("SELECT id FROM candidates WHERE platform IN ('twitch_clip','youtube_live','youtube')")
    out = 0
    for r in rows:
        snaps = store.fetchall("SELECT ts, views, concurrent_viewers FROM snapshots WHERE candidate_id=? ORDER BY ts",
                               (r["id"],))
        if not snaps:
            continue
        samples = [(_epoch(s["ts"]), s["views"] if s["views"] is not None else (s["concurrent_viewers"] or 0))
                   for s in snaps]
        views = samples[-1][1]
        vel = scoring.velocity_per_hour(samples)
        res = scoring.popularity_score(views, vel)
        store.execute("UPDATE candidates SET score=?, score_confidence=?, signals_json=?, status='scored'"
                      " WHERE id=?", (res["score"], res["confidence"], json.dumps(res["used"]), r["id"]))
        out += 1
    return out


def _download(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        size = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                size += len(chunk)
                if size > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError("download exceeds size limit")
                f.write(chunk)
    return dest


def acquire_twitch(store, tw, candidate_id, twitch_clip_id, creator_id):
    """Download one clip only if its creator has authorized us. Otherwise record and keep ranking it."""
    token = authz.user_token(store, tw, creator_id)
    if not token:
        store.execute("UPDATE candidates SET status='ranked_no_file' WHERE id=?", (candidate_id,))
        return "no_authorization"
    urls = tw.download_urls([twitch_clip_id], token)
    if twitch_clip_id not in urls:
        store.execute("UPDATE candidates SET status='ranked_no_file' WHERE id=?", (candidate_id,))
        return "no_download_url"
    dest = _download(urls[twitch_clip_id], DOWNLOAD_DIR / f"{twitch_clip_id}.mp4")
    clip.register_source(store, creator_id, dest, "creator_file", title=candidate_id, url=urls[twitch_clip_id])
    store.execute("UPDATE candidates SET status='acquired' WHERE id=?", (candidate_id,))
    return "acquired"


def ingest_owner_folder(store, folder=None):
    """Register every .mp4 in the owner's recording folder as an owned source (idempotent)."""
    folder = Path(folder or OWNER_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    added = []
    for f in sorted(folder.glob("*.mp4")):
        sid = clip.register_source(store, OWNER_CREATOR, f, "owned", title=f.stem)
        added.append(sid)
    return added


def process_sources(store, cfg, cuts=2, clip_s=30):
    """Run the existing local clip pipeline once per registered source (job-keyed, never twice)."""
    results = {}
    for src in store.fetchall("SELECT source_id FROM sources WHERE acquisition IN ('owned','creator_file')"):
        if not store.enqueue_job(f"process:{src['source_id']}", "process", {"source_id": src["source_id"]}):
            continue
        try:
            results[src["source_id"]] = clip.build_candidates(store, cfg, src["source_id"], top=cuts, clip_s=clip_s)
            store.mark_job(f"process:{src['source_id']}", "done")
        except Exception as e:  # keep other sources running
            store.mark_job(f"process:{src['source_id']}", "failed", error=str(e)[:300], backoff_seconds=3600)
            results[src["source_id"]] = {"error": str(e)[:300]}
    return results


def main(argv=None):
    import argparse
    import sys

    from .config import load_config, state_path
    from .sources.twitch import TwitchAPI
    from .store import Store

    ap = argparse.ArgumentParser(prog="gta.ingest", description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["discover-twitch", "discover-youtube-live", "score", "owner-ingest",
                                        "process"])
    ap.add_argument("--folder", default=None)
    args = ap.parse_args(argv)
    cfg = load_config()
    store = Store(str(state_path("gta.sqlite")))
    out = {}
    if args.command == "discover-twitch":
        tw = TwitchAPI.from_env()
        if not tw:
            print("TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET not set: Twitch discovery skipped", file=sys.stderr)
            return 2
        out["clips_recorded"] = discover_twitch(store, cfg, tw)
    elif args.command == "discover-youtube-live":
        from .sources import youtube_live
        from .yt_client import YouTubeClient
        from .quota import QuotaLedger

        yt = YouTubeClient(QuotaLedger(store, cfg["quota"]))
        out["live_recorded"] = youtube_live.discover_live(store, yt, cfg["live"]["search_queries"])
    elif args.command == "score":
        out["scored"] = score_all(store)
    elif args.command == "owner-ingest":
        out["owned_sources"] = ingest_owner_folder(store, args.folder)
    elif args.command == "process":
        out["processed"] = process_sources(store, cfg)
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
