"""YouTube livestreams as a trend and discovery source. No video is downloaded.

Official calls only:
  search.list (eventType=live)          -> live GTA VI streams (search bucket, 1 call each)
  videos.list (liveStreamingDetails)    -> concurrent viewers, activeLiveChatId
  liveChatMessages.list                 -> live chat messages while the stream is live

Chat spikes: messages are counted in fixed buckets over time and compared with the stream's own
median rate. A spike is a bucket with many more messages than usual. This signal exists only
while a stream is live, so buckets must be recorded during the stream. It is stored in
chat_buckets and read later.
"""
import statistics

from ..store import now_iso


def discover_live(store, yt, queries, max_results=25):
    found = 0
    for q in queries:
        data = yt.request("search.list", yt.service.search().list(
            part="snippet", q=q, type="video", eventType="live", order="viewCount", maxResults=max_results))
        ids = [i["id"]["videoId"] for i in data.get("items", []) if i.get("id", {}).get("videoId")]
        if not ids:
            continue
        details = yt.request("videos.list", yt.service.videos().list(
            part="snippet,statistics,liveStreamingDetails", id=",".join(ids[:50])))
        for v in details.get("items", []):
            live = v.get("liveStreamingDetails", {})
            if not live.get("actualStartTime") or live.get("actualEndTime"):
                continue
            vid = v["id"]
            sn = v["snippet"]
            cid = f"youtube-live:{vid}"
            store.execute(
                "INSERT INTO candidates (id, platform, creator_id, title, url, is_live, first_seen, last_seen, status)"
                " VALUES (?,?,?,?,?,1,?,?,'discovered') ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen,"
                " title=excluded.title",
                (cid, "youtube_live", f"youtube-channel:{sn['channelId']}", sn["title"],
                 f"https://www.youtube.com/watch?v={vid}", now_iso(), now_iso()))
            concurrent = int(live["concurrentViewers"]) if "concurrentViewers" in live else None
            store.execute(
                "INSERT OR REPLACE INTO snapshots (candidate_id, ts, views, likes, comments, concurrent_viewers)"
                " VALUES (?,?,?,?,?,?)",
                (cid, now_iso(), int(v.get("statistics", {}).get("viewCount", 0)), None, None, concurrent))
            found += 1
    return found


def live_chat_id(yt, video_id):
    d = yt.request("videos.list", yt.service.videos().list(part="liveStreamingDetails", id=video_id))
    items = d.get("items", [])
    return items[0].get("liveStreamingDetails", {}).get("activeLiveChatId") if items else None


def record_chat_messages(store, video_id, timestamps, bucket_s=10):
    """Count messages per bucket. timestamps are epoch seconds of each message."""
    counts = {}
    for t in timestamps:
        b = int(t) // bucket_s * bucket_s
        counts[b] = counts.get(b, 0) + 1
    for b, n in counts.items():
        store.execute(
            "INSERT INTO chat_buckets (video_id, bucket_ts, messages) VALUES (?,?,?)"
            " ON CONFLICT(video_id, bucket_ts) DO UPDATE SET messages = messages + excluded.messages",
            (video_id, b, n))
    return len(timestamps)


def detect_spikes(store, video_id, min_messages=20, ratio=3.0, bucket_s=10):
    """Return spike windows [{start, end, messages, ratio}] from recorded buckets for one video."""
    rows = store.fetchall("SELECT bucket_ts, messages FROM chat_buckets WHERE video_id=? ORDER BY bucket_ts",
                          (video_id,))
    if len(rows) < 3:
        return []
    counts = [r["messages"] for r in rows]
    base = statistics.median(counts) or 1
    spikes = []
    for r in rows:
        n = r["messages"]
        if n >= min_messages and n >= ratio * base:
            spikes.append({"start": r["bucket_ts"], "end": r["bucket_ts"] + bucket_s,
                           "messages": n, "ratio": round(n / base, 2)})
    return spikes
