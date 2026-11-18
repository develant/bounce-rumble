"""Persistent state for the GTA VI pipeline (SQLite, stdlib only).

Tables:
  permissions  - per-creator rights records (default-deny; see rights.py)
  candidates   - discovered videos/streams, deduplicated by platform:id
  snapshots    - view/like/comment samples used for growth velocity
  jobs         - background work items with unique keys (duplicate prevention,
                 retry with exponential backoff, restart recovery)
  shorts       - rendered Shorts and their publishing state (unique source_key)
  quota_ledger - YouTube API units spent per Pacific day and method
  events       - append-only publishing and error log
"""
import json
import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS permissions (
    creator_id TEXT PRIMARY KEY,
    display_name TEXT,
    channel_url TEXT,
    permission_source TEXT,
    permission_date TEXT,
    status TEXT NOT NULL DEFAULT 'pending',      -- pending | verified | denied | expired
    monetization_allowed INTEGER NOT NULL DEFAULT 0,
    attribution TEXT,
    restrictions TEXT,
    expires_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,                          -- "<platform>:<native id>"
    platform TEXT NOT NULL,
    creator_id TEXT,
    title TEXT,
    url TEXT,
    is_live INTEGER DEFAULT 0,
    first_seen TEXT,
    last_seen TEXT,
    score REAL,
    score_confidence REAL,
    signals_json TEXT,
    status TEXT NOT NULL DEFAULT 'new'             -- new | scored | selected | rejected | rendered
);
CREATE TABLE IF NOT EXISTS snapshots (
    candidate_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    views INTEGER,
    likes INTEGER,
    comments INTEGER,
    concurrent_viewers INTEGER,
    PRIMARY KEY (candidate_id, ts)
);
CREATE TABLE IF NOT EXISTS jobs (
    job_key TEXT PRIMARY KEY,                      -- unique key = duplicate prevention
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',         -- queued | running | done | failed
    attempts INTEGER NOT NULL DEFAULT 0,
    next_run_at TEXT,
    payload_json TEXT,
    last_error TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS shorts (
    source_key TEXT PRIMARY KEY,                   -- unique: one Short per source clip/template
    template TEXT,
    title TEXT,
    description TEXT,
    tags_json TEXT,
    file_path TEXT,
    duration_s REAL,
    quality_score REAL,
    creator_id TEXT,
    status TEXT NOT NULL DEFAULT 'rendered',       -- rendered | queued | uploading | published | failed | blocked
    youtube_id TEXT,
    publish_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS quota_ledger (
    day TEXT NOT NULL,
    method TEXT NOT NULL,
    units INTEGER NOT NULL DEFAULT 0,
    calls INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, method)
);
CREATE TABLE IF NOT EXISTS video_metrics (
    youtube_id TEXT NOT NULL,
    day TEXT NOT NULL,
    views INTEGER,
    likes INTEGER,
    comments INTEGER,
    subscribers_gained INTEGER,
    avg_view_duration_s REAL,
    avg_view_pct REAL,
    fetched_at TEXT,
    PRIMARY KEY (youtube_id, day)
);
CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,                    -- "<platform>:<native id>" or "file:<sha1>"
    creator_id TEXT NOT NULL,
    title TEXT,
    url TEXT,
    local_path TEXT,
    acquisition TEXT NOT NULL,                     -- owned | creator_file | metadata_only
    added_at TEXT
);
CREATE TABLE IF NOT EXISTS fingerprints (
    source_key TEXT PRIMARY KEY,
    source_id TEXT,
    start_s REAL,
    end_s REAL,
    dhash TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    component TEXT NOT NULL,
    message TEXT NOT NULL,
    detail_json TEXT
);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # -- generic helpers -------------------------------------------------
    def execute(self, sql, params=()):
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def fetchone(self, sql, params=()):
        with self._lock:
            row = self.conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def fetchall(self, sql, params=()):
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def log(self, component, message, level="info", **detail):
        self.execute(
            "INSERT INTO events (ts, level, component, message, detail_json) VALUES (?,?,?,?,?)",
            (now_iso(), level, component, message, json.dumps(detail, default=str) if detail else None),
        )

    # -- jobs (duplicate prevention + retry state) -------------------------
    def enqueue_job(self, job_key, kind, payload=None, run_at=None):
        """Insert a job once. Returns False if the key already exists (no duplicate work)."""
        cur = self.execute(
            "INSERT OR IGNORE INTO jobs (job_key, kind, status, attempts, next_run_at, payload_json, updated_at)"
            " VALUES (?,?,'queued',0,?,?,?)",
            (job_key, kind, run_at or now_iso(), json.dumps(payload or {}), now_iso()),
        )
        return cur.rowcount == 1

    def due_jobs(self, kind, limit=10):
        return self.fetchall(
            "SELECT * FROM jobs WHERE kind=? AND status IN ('queued','failed') AND next_run_at<=?"
            " ORDER BY next_run_at LIMIT ?",
            (kind, now_iso(), limit),
        )

    def mark_job(self, job_key, status, error=None, backoff_seconds=None):
        from datetime import timedelta

        next_run = None
        if backoff_seconds is not None:
            next_run = (datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)).isoformat(timespec="seconds")
        self.execute(
            "UPDATE jobs SET status=?, attempts=attempts+1, last_error=?, updated_at=?,"
            " next_run_at=COALESCE(?, next_run_at) WHERE job_key=?",
            (status, error, now_iso(), next_run, job_key),
        )

    def requeue_stale_running(self):
        """Restart recovery: jobs left 'running' by a crashed worker go back to 'queued'."""
        return self.execute("UPDATE jobs SET status='queued' WHERE status='running'").rowcount
