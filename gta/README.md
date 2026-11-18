# GTA VI Shorts pipeline (Bounce Rumble)

Original, rights-checked GTA VI Shorts for the existing **Bounce Rumble** channel
(`UCUobFUu1jd_np5iFaBoW75g`). The channel is an independent fan channel and is not affiliated
with Rockstar Games or Take-Two Interactive. Read `../PROGRESS.md` for status and owner actions.

## Modules

| File | What it does |
|---|---|
| `config.json` | Channel, playlist, quota costs and budget, publishing limits, discovery schedule, render spec |
| `store.py` | SQLite state (WAL): permissions, candidates, snapshots, jobs (unique keys), shorts, quota ledger, metrics, events |
| `quota.py` | Reserves YouTube API units before each call; refuses calls that do not fit the daily budget (Pacific day) |
| `yt_client.py` | Data API client from env credentials; every call goes through the quota ledger |
| `rights.py` | Default-deny permission records (status, source, date, attribution, monetization, expiry) |
| `scoring.py` | Multi-signal 0-100 score; missing signals are excluded and reported as `confidence` |
| `discovery.py` | YouTube search + stats; Twitch Helix streams (only with credentials); dedupe; snapshots |
| `templates.py` | Countdown template; facts file gate; descriptions with sources and disclaimer |
| `content/facts.json` | Facts used on screen. Each needs sources and `primary_source_checked` |
| `render.py` | FFmpeg 1080x1920 renderer: zoom background, hook card, ASS captions with keyword highlight, EBU R128 loudness, duration check |
| `publisher.py` | Eligibility (rights, facts, quality, daily cap, quota), upload with backoff, verification, playlist add |
| `analytics.py` | YouTube Analytics per-video metrics and performance by template |
| `runner.py` | CLI and 24/7 loop (scan 6h, refresh 60m, analytics 6h, publish queue) with health file and SIGTERM handling |
| `setup_playlist.py` | Idempotent playlist creation with a channel-ID guard |
| `branding/` | Avatar and banner generator; banner applier with backup and channel-ID guard |
| `deploy/gta-worker.service` | systemd unit for a 24/7 host |

## Commands

```bash
python -m gta.runner status                 # quota, shorts by status, jobs, playlist
python -m gta.runner seed-rights            # owner's own work: verified record, expires in 1 year
python -m gta.runner produce-countdown      # render the countdown Short and queue it
python -m gta.runner publish                # dry-run: shows what would publish, uploads nothing
python -m gta.runner discover               # YouTube (+Twitch with credentials). Spends search.list units
python -m gta.runner refresh
python -m gta.runner analytics
python -m gta.runner loop --live            # 24/7 (see deploy/); live needs config flags, see below
python -m pytest -q tests                   # 31 tests, no network needed
```

Credentials come only from environment variables (`YT_CLIENT_ID`, `YT_CLIENT_SECRET`,
`YT_REFRESH_TOKEN`; optional `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`). Nothing is written to the repository.

## Going live (owner decisions, in order)

1. Confirm each fact in `content/facts.json` on rockstargames.com, then set `primary_source_checked: true`,
   `checked_by`, and `checked_on`. Until then the publisher blocks every Short that uses that fact.
2. Read the real YouTube quota in Google Cloud Console (APIs & Services > YouTube Data API v3 > Quotas), set
   `quota.daily_budget_units` and `quota.external_units_by_day` (usage by the existing daily job), and confirm
   the upload cost. The default config assumes 1600 units per `videos.insert`, the conservative figure.
3. Set `publishing.enabled: true` and `publishing.dry_run: false` in `config.json`, then start the worker with `--live`.
   Consider `publishing.privacy_status: "private"` with `publish_at` set (already the default) for the first batch.
4. Discovery of third-party streams only creates candidates. A clip is produced only for a creator whose
   permission record is `verified` (`rights.upsert_permission`). Clip extraction from permitted sources is not
   implemented yet.

## Limits, stated plainly

- The YouTube Data API quota is one pool per day (10,000 units by default) for all methods on the project.
  The existing daily job uses most of it. Search (100 units each) and uploads (1600 units each under the
  conservative assumption) are the expensive calls. Check the current Google documentation for the real costs.
- A `videos.insert` cannot fit in today's remaining quota if the daily job has used about 8000 units.
- `rockstargames.com` is blocked by this environment's egress proxy, so launch facts can only be checked by a person.
- Channel avatars cannot be set through the YouTube API. Upload `branding/out/avatar.png` in YouTube Studio.
- Replay heatmaps and Twitch chat spikes are not exposed to this pipeline. They are scored as missing signals
  until a permitted source is added. See `../PROGRESS.md` for the evaluation of the five listed repositories.
