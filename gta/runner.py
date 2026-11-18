"""Command line and long-running worker for the GTA VI Shorts pipeline.

    python -m gta.runner status
    python -m gta.runner seed-rights                  # owner's own original work, verified record
    python -m gta.runner produce-countdown [--quality 75]
    python -m gta.runner discover                     # YouTube (+ Twitch when credentials exist)
    python -m gta.runner refresh                      # refresh recent candidates (cheap videos.list)
    python -m gta.runner publish [--live]             # process the publish queue once (dry-run by default)
    python -m gta.runner analytics                    # per-video metrics + performance by template
    python -m gta.runner loop [--live]                # 24/7 worker: scan 6h, refresh 60m, analytics 6h, publish queue

The loop writes gta/state/health.json on every tick, recovers jobs left "running" by a crash,
retries failed jobs with exponential backoff, and exits cleanly on SIGTERM/SIGINT after the
current job finishes.
"""
import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import discovery, publisher, rights, templates
from .config import load_config, state_path
from .quota import PACIFIC, QuotaLedger
from .render import render
from .store import Store, now_iso

log = logging.getLogger("gta")
TICK_SECONDS = 30
_stop = False


def _on_signal(signum, _frame):
    global _stop
    _stop = True
    log.info("signal %s received, stopping after the current job", signum)


def open_store():
    return Store(state_path("gta.sqlite"))


def build_yt(store, cfg):
    from .yt_client import YouTubeClient

    return YouTubeClient(QuotaLedger(store, cfg["quota"]))


# ------------------------------------------------------------ recurring schedule
def ensure_recurring(store, key, kind, interval_minutes):
    store.execute(
        "INSERT OR IGNORE INTO jobs (job_key, kind, status, attempts, next_run_at, payload_json, updated_at)"
        " VALUES (?,?,'queued',0,?,?,?)",
        (key, kind, now_iso(), json.dumps({"interval_minutes": interval_minutes}), now_iso()),
    )


def complete_recurring(store, key, interval_minutes, error=None):
    nxt = (datetime.now(timezone.utc) + timedelta(minutes=interval_minutes)).isoformat(timespec="seconds")
    store.execute(
        "UPDATE jobs SET status='queued', attempts=0, last_error=?, next_run_at=?, updated_at=? WHERE job_key=?",
        (error, nxt, now_iso(), key),
    )


def backoff_recurring(store, key, error):
    row = store.fetchone("SELECT attempts FROM jobs WHERE job_key=?", (key,))
    attempts = (row["attempts"] if row else 0) + 1
    delay = min(3600, 60 * 2 ** min(attempts, 6))
    nxt = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec="seconds")
    store.execute(
        "UPDATE jobs SET status='failed', attempts=?, last_error=?, next_run_at=?, updated_at=? WHERE job_key=?",
        (attempts, str(error)[:500], nxt, now_iso(), key),
    )


# ------------------------------------------------------------ actions
def action_seed_rights(store, cfg):
    today = date.today()
    rights.upsert_permission(
        store, cfg["content"]["self_creator_id"], "verified",
        display_name="Bounce Rumble (channel owner)",
        channel_url="https://www.youtube.com/@BounceRumble",
        permission_source="Original work by the channel owner: generated graphics, original captions and editing",
        permission_date=today.isoformat(),
        monetization_allowed=1,
        attribution="Facts cite their sources in each description",
        restrictions="Do not present as an official Rockstar Games channel. No game footage without a separate record.",
        expires_at=(today + timedelta(days=365)).isoformat(),
    )
    return {"creator_id": cfg["content"]["self_creator_id"], "status": "verified"}


def action_produce_countdown(store, cfg, quality):
    launch = date.fromisoformat(cfg["content"]["launch_date"])
    today = datetime.now(PACIFIC).date()
    days_left = (launch - today).days
    if days_left < 0:
        return {"result": "skipped", "reason": "launch date has passed"}
    facts = templates.load_facts()
    plan = templates.countdown_plan(days_left, facts)
    key = f"countdown:{launch.isoformat()}:day{days_left}"
    out_dir = Path(os.environ.get("GTA_RENDER_DIR", Path(__file__).parent / "renders")) / key.replace(":", "_")
    info = render(plan, out_dir / "countdown.mp4", out_dir / "work", cfg)
    (out_dir / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    slots = publisher.schedule_slots(cfg, 1)
    queued = publisher.add_short(
        store, source_key=key, template=plan["template"], title=plan["title"],
        description=templates.description_for(plan, cfg["channel"]["disclaimer"]),
        tags=["GTA VI", "GTA 6", "GTA VI countdown", "Vice City", "shorts"],
        file_path=str(out_dir / "countdown.mp4"), duration_s=info["duration_s"], quality_score=quality,
        creator_id=plan["creator_id"], fact_ids=plan["fact_ids"], publish_at=None,
    )
    return {"source_key": key, "file": str(out_dir / "countdown.mp4"), "duration_s": info["duration_s"],
            "loudness_lufs": info["loudness"] and info["loudness"]["input_i"], "queued_new": queued,
            "suggested_publish_slot_utc": slots[0]}


def action_discover(store, cfg):
    yt = build_yt(store, cfg)
    out = {}
    try:
        out["youtube_candidates"] = discovery.youtube_scan(
            store, yt, cfg["discovery"]["youtube_queries"], cfg["discovery"]["youtube_search_max_results"])
    except Exception as e:  # keep the other sources running
        store.log("discovery", "youtube scan failed", level="error", error=str(e)[:300])
        out["youtube_error"] = str(e)[:300]
        raise
    tw = discovery.TwitchClient.from_env()
    if tw:
        out["twitch_candidates"] = discovery.twitch_scan(
            store, tw, cfg["discovery"]["twitch"]["category_name"], cfg["discovery"]["twitch"]["max_streams"])
    else:
        out["twitch"] = "skipped: TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET not set"
        store.log("discovery", "twitch skipped (no credentials)")
    return out


def action_refresh(store, cfg):
    yt = build_yt(store, cfg)
    return {"youtube_refreshed": discovery.youtube_refresh(store, yt)}


def action_publish(store, cfg, live):
    yt = build_yt(store, cfg)
    results = []
    for job in store.due_jobs("publish", limit=cfg["publishing"]["max_shorts_per_day"]):
        if _stop:
            break
        key = job["job_key"].split(":", 1)[1]
        results.append(publisher.publish_one(store, yt, cfg, key, live))
    for job in store.due_jobs("playlist", limit=10):
        vid = json.loads(job["payload_json"])["video_id"]
        try:
            if cfg["playlist"]["playlist_id"] is None:
                raise RuntimeError("playlist not created yet")
            publisher.add_to_playlist(yt, cfg["playlist"]["playlist_id"], vid)
            store.mark_job(job["job_key"], "done")
            results.append({"playlist_add": vid, "result": "added"})
        except Exception as e:
            store.mark_job(job["job_key"], "failed", error=str(e)[:300], backoff_seconds=1800)
            results.append({"playlist_add": vid, "result": "deferred", "reason": str(e)[:200]})
    return results


def action_analytics(store, cfg):
    from . import analytics
    from .yt_client import credentials_from_env

    svc = analytics.make_service(credentials_from_env())
    ids = [r["youtube_id"] for r in store.fetchall("SELECT youtube_id FROM shorts WHERE youtube_id IS NOT NULL")]
    rows = analytics.fetch_video_metrics(svc, ids)
    return {"rows_stored": analytics.store_metrics(store, rows),
            "by_template": analytics.performance_by_template(store)}


def action_status(store, cfg):
    quota = QuotaLedger(store, cfg["quota"])
    counts = {r["status"]: r["n"] for r in store.fetchall("SELECT status, COUNT(*) AS n FROM shorts GROUP BY status")}
    return {
        "quota": quota.summary(),
        "shorts_by_status": counts,
        "published_today_pacific": publisher.published_today(store),
        "candidates": {r["platform"]: r["n"] for r in store.fetchall(
            "SELECT platform, COUNT(*) AS n FROM candidates GROUP BY platform")},
        "jobs_queued": store.fetchone("SELECT COUNT(*) AS n FROM jobs WHERE status='queued'")["n"],
        "jobs_failed": store.fetchone("SELECT COUNT(*) AS n FROM jobs WHERE status='failed'")["n"],
        "publishing": {"enabled": cfg["publishing"]["enabled"], "dry_run": cfg["publishing"]["dry_run"]},
        "playlist_id": cfg["playlist"]["playlist_id"],
    }


def write_health(store, cfg, extra=None):
    path = state_path("health.json")
    data = {"ts": now_iso(), "pid": os.getpid(), "status": "ok", **(extra or {})}
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def loop(store, cfg, live):
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    recovered = store.requeue_stale_running()
    d = cfg["discovery"]
    ensure_recurring(store, "recurring:scan", "scan", d["scan_interval_minutes"])
    ensure_recurring(store, "recurring:refresh", "refresh", d["refresh_interval_minutes"])
    ensure_recurring(store, "recurring:analytics", "analytics", 360)
    store.log("runner", "worker started", live=live, recovered_jobs=recovered)
    while not _stop:
        tick_errors = []
        for kind, key, interval, fn in [
            ("scan", "recurring:scan", d["scan_interval_minutes"], lambda: action_discover(store, cfg)),
            ("refresh", "recurring:refresh", d["refresh_interval_minutes"], lambda: action_refresh(store, cfg)),
            ("analytics", "recurring:analytics", 360, lambda: action_analytics(store, cfg)),
        ]:
            due = store.fetchone("SELECT * FROM jobs WHERE job_key=? AND status IN ('queued','failed')"
                                 " AND next_run_at<=?", (key, now_iso()))
            if due:
                try:
                    fn()
                    complete_recurring(store, key, interval)
                except Exception as e:
                    backoff_recurring(store, key, e)
                    tick_errors.append(f"{kind}: {e}"[:300])
                    log.exception("%s failed", kind)
        try:
            action_publish(store, cfg, live)
        except Exception as e:
            tick_errors.append(f"publish: {e}"[:300])
            log.exception("publish failed")
        write_health(store, cfg, {"live": live, "errors": tick_errors[-5:],
                                  "quota_remaining": QuotaLedger(store, cfg["quota"]).remaining_today()})
        for _ in range(TICK_SECONDS):
            if _stop:
                break
            time.sleep(1)
    store.log("runner", "worker stopped cleanly")
    write_health(store, cfg, {"status": "stopped"})
    return 0


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["status", "seed-rights", "produce-countdown", "discover", "refresh",
                                        "publish", "analytics", "loop"])
    ap.add_argument("--live", action="store_true", help="allow real uploads (also needs config publishing.enabled and dry_run false)")
    ap.add_argument("--quality", type=float, default=70.0, help="curated quality score for produced templates")
    args = ap.parse_args(argv)

    cfg = load_config()
    store = open_store()
    if args.command == "status":
        out = action_status(store, cfg)
    elif args.command == "seed-rights":
        out = action_seed_rights(store, cfg)
    elif args.command == "produce-countdown":
        out = action_produce_countdown(store, cfg, args.quality)
    elif args.command == "discover":
        out = action_discover(store, cfg)
    elif args.command == "refresh":
        out = action_refresh(store, cfg)
    elif args.command == "publish":
        out = action_publish(store, cfg, args.live)
    elif args.command == "analytics":
        out = action_analytics(store, cfg)
    elif args.command == "loop":
        return loop(store, cfg, args.live)
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
