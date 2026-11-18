"""Performance analytics for published Shorts (YouTube Analytics API, read-only).

Pulls per-video daily metrics for the channel's own videos and stores them in video_metrics.
`performance_by_template` joins them to the shorts table so the owner can see which editing
template and which content type perform best. This uses the yt-analytics.readonly scope that
the channel's existing OAuth grant already includes.

Retention and average view duration come from the API's averageViewPercentage and
averageViewDuration. "Viewed vs swiped away" is the Shorts feed metric and is not in this report.
"""
import datetime as dt

from googleapiclient.discovery import build

from .store import now_iso

METRICS = "views,likes,comments,subscribersGained,averageViewDuration,averageViewPercentage"


def make_service(creds):
    return build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)


def fetch_video_metrics(service, video_ids, days=28, end=None):
    """Return a list of dict rows, one per (video, day). Uses the analytics quota, not Data API units."""
    if not video_ids:
        return []
    end = end or dt.date.today()
    start = end - dt.timedelta(days=days)
    rows = []
    for k in range(0, len(video_ids), 200):
        chunk = video_ids[k:k + 200]
        resp = service.reports().query(
            ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(),
            metrics=METRICS, dimensions="video,day",
            filters="video==" + ",".join(chunk), maxResults=500,
        ).execute()
        names = [c["name"] for c in resp.get("columnHeaders", [])]
        for r in resp.get("rows", []):
            rows.append(dict(zip(names, r)))
    return rows


def store_metrics(store, rows):
    ts = now_iso()
    for r in rows:
        store.execute(
            "INSERT OR REPLACE INTO video_metrics (youtube_id, day, views, likes, comments, subscribers_gained,"
            " avg_view_duration_s, avg_view_pct, fetched_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (r["video"], r["day"], r.get("views"), r.get("likes"), r.get("comments"),
             r.get("subscribersGained"), r.get("averageViewDuration"), r.get("averageViewPercentage"), ts),
        )
    return len(rows)


def performance_by_template(store):
    """Average views, average view percentage and subscribers gained per template (published Shorts only)."""
    return store.fetchall(
        "SELECT s.template AS template, COUNT(DISTINCT s.youtube_id) AS shorts,"
        " SUM(m.views) AS views, AVG(m.avg_view_pct) AS avg_view_pct,"
        " SUM(m.subscribers_gained) AS subscribers_gained"
        " FROM shorts s JOIN video_metrics m ON m.youtube_id = s.youtube_id"
        " WHERE s.status='published' GROUP BY s.template ORDER BY views DESC"
    )
