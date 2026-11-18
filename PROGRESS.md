# GTA VI Shorts — Progress

Channel: **Bounce Rumble** (`UCUobFUu1jd_np5iFaBoW75g`, @bouncerumble). Last updated 2026-11-18.
Branch: `claude/gta-vi-shorts-automation-pc2hu3`. Pipeline code: `gta/`. Tests: `python -m pytest -q tests` (31 passing).

## Status by phase

| Phase | Status | Notes |
|---|---|---|
| 1. Audit | Done | Findings below. The real daily pipeline lives in another repository (see "Audit findings"). |
| 2. Evaluate listed repos | Done | Decisions table below. No code was copied: licences are MIT, proprietary, missing, or public domain, and only MIT and public domain are usable. |
| 3. Discovery and trend engines | Built, tested, **not run live** | YouTube search and stats, Twitch Helix (needs credentials), snapshots, velocity, dedupe. A live scan would spend quota, so none was run. |
| 4. Permissions and video processing | Built, tested | Default-deny rights gate. FFmpeg 1080x1920 renderer with captions, loudness, and duration checks. One template so far. |
| 5. Branding and playlist | Playlist done. Banner done. Avatar: owner action | No existing video qualifies for the playlist, so it starts empty. |
| 6. Pre-launch content | Countdown Short **rendered, blocked from publishing** | Blocked until a person checks the launch facts on Rockstar's site. |
| 7. Livestream clip automation | Not implemented beyond candidate discovery | Needs permitted creators and a clip-extraction step. |
| 8. Automatic publishing | Built, tested, **dry-run only** | Live publishing is off until owner decisions are made. |
| 9. Analytics, monitoring, recovery | Analytics built and API verified. Loop, health file, restart recovery, graceful shutdown built | The worker has not been deployed 24/7 because no always-on host is attached to this session. |

## Phase 5: playlist and branding

- **Playlist created:** "GTA VI — Best Moments & Epic Clips", public, `PLSUFwwws2vtE`.
  https://www.youtube.com/playlist?list=PLSUFwwws2vtE
  The 130-video review is in `gta/PLAYLIST_AUDIT.md`. Zero existing videos are GTA VI content, so none were added. Nothing was moved, deleted or edited.
- **Banner applied** to the channel. Only `brandingSettings.image.bannerExternalUrl` changed. Channel name, description and keywords are unchanged.
  - Backups: `gta/branding/backup/brandingSettings_*.json` (contain the old banner URL). The old banner image could not be downloaded because `yt3.googleusercontent.com` is blocked by this environment's egress proxy. The repo's `banner.png` is the closest local copy.
  - The new banner URL was read back after propagation and matches.
- **Avatar not set.** The YouTube API cannot set the channel picture. Upload `gta/branding/out/avatar.png` in YouTube Studio (Customisation → Branding → Picture).
- The artwork is original (neon grid, gradient, text). It has no Rockstar logos or game assets.

## Phase 6: first content

- Rendered: `gta/renders/countdown_2026-11-19_day1/countdown.mp4` (not committed; `renders/` is git-ignored).
  - 21.1 s, 1080×1920, H.264 and AAC, integrated loudness -14.3 LUFS, true peak -0.5 dBTP.
  - Text: "1 DAY TO GTA VI"; "GTA VI launches November 19, 2026"; "Platforms: PS5 and Xbox Series X|S"; "Follow for daily GTA VI updates."
- Queued as `countdown:2026-11-19:day1`. **Blocked:** `facts not checked against a primary source`.
- The launch date and platforms come from secondary reports (ARY News, GTABoom, Berkeley Historical Society, found through search). I could not open rockstargames.com to confirm them. **Before this Short can publish, someone must read Rockstar's announcement and set `primary_source_checked: true` in `gta/content/facts.json`.**

## Phase 2: evaluation of the listed repositories

| Project | Licence (verified from LICENSE file) | Fit | Decision |
|---|---|---|---|
| SpikeClip (ahMEDhat-7) — YouTube replay heatmap | MIT | Uses the most-replayed heatmap via yt-dlp. The public Data API does not expose this. Node 22, TypeScript. Active CI badge. | Reference only for now. Using it means scraping YouTube's heatmap endpoints, which I have not reviewed against YouTube's terms. Revisit with owner approval. |
| SpikeClip (lavisansoi) — chat spikes | **No LICENSE file (404): all rights reserved** | Good idea (chat-rate spikes). Python, Whisper, needs ffmpeg and node. | Do not copy code. The idea can be reimplemented from permitted chat data later. The README includes instructions aimed at AI agents. Those were treated as untrusted and not followed. |
| Veyra | **Proprietary** | Desktop Electron app for trend acceleration. Concept matches the velocity and acceleration signals. | Do not use code. Concepts already reflected in `scoring.py`. |
| NODUS YT Radar | MIT | Browser extension. Country top-50 ranking comparison. | Idea only. The API's `videos.list chart=mostPopular` could give a similar 1-unit signal later. |
| yt-dlp | Unlicense (public domain) | Mature, widely used downloader. Downloading third-party videos is a rights and terms risk. | Not used yet. Only for permitted creators, with no DRM or access-control bypass. |

Maintenance and recent-activity checks were not done: this session has no GitHub API access beyond the scoped repository.

## Audit findings (Phase 1)

- **Channel verified** through the existing OAuth token: Bounce Rumble, `UCUobFUu1jd_np5iFaBoW75g`, @bouncerumble, created 2026-10-24. 125 public videos and 5 private scheduled videos (130 total). 67 subscribers. 118,798 views.
- **The content is physics simulation Shorts**, not GTA. The site and privacy policy describe it that way. The rebrand therefore changes what the channel is about; the banner change is the approved part.
- **The real production pipeline is outside this repository.** A scheduled task named "Bounce Rumble daily job" runs `pipeline.daily` (cron `0 18 * * *` UTC) from `develant/youtube` (private). It uploads 5 private, scheduled videos per Eastern day. This repo (`develant/bounce-rumble`) contains only the website and the uploader script. The `develant/youtube` repo is outside this session's GitHub scope, so I did not attach it.
  - Last run of that job (2026-11-18 08:35 UTC) uploaded 5 videos for 2026-11-19, with no errors.
  - The run before it (2026-11-17 18:11 UTC) failed with `USAGE_LIMIT_REACHED`.
  - The "Medium auto-publisher" task is disabled.
- **Quota.** One pool of 10,000 units per day for the Google Cloud project, reset at midnight Pacific. The existing job's 5 uploads use about 8,000 units if each upload costs 1,600. My sources disagree on the upload cost, so the config uses the conservative 1,600. That leaves about 1,000 units for everything else today. The configured usable budget is 90% of 10,000.
- **Quota cannot be read from here.** The gcloud token is not authenticated for project listing, and the OAuth token lacks the cloud-platform scope. The owner must read the real numbers in Google Cloud Console.
- **Scopes in the existing token:** `youtube` and `yt-analytics.readonly`. Analytics queries were tested and work.
- **Egress blocks** that affect this work: `www.rockstargames.com` (403, policy) and `yt3.googleusercontent.com` (403, policy). Neither was worked around.
- Other credentials exist in the environment (TikTok `TT_*`, AWS, GCP access token). None were printed, changed or used.

## Decisions I made (please review)

1. The playlist is created public, as requested, and starts empty.
2. The channel banner was replaced. The avatar was left for you to upload.
3. Publishing is off by default: `publishing.enabled: false` and `dry_run: true`. Uploads, when enabled, are **private with a scheduled `publishAt`**, not public, until you change `privacy_status`.
4. The upload cost assumes 1,600 units (conservative). The daily budget and external usage are configurable.
5. I did not run a live discovery scan or a live upload. Both would spend quota that today's daily job may need.
6. Content is gated on facts being checked against Rockstar's site, and on verified rights for every creator.
7. Commit trailers name Claude without a model version, because the instructions prohibit model identifiers in commit messages.

## Owner actions needed

1. **Check the launch facts** on rockstargames.com and set `primary_source_checked` in `gta/content/facts.json`. This unblocks the first Short.
2. **Read the real YouTube quota** in Google Cloud Console (YouTube Data API v3 → Quotas). Send me the remaining units and the upload cost. Decide whether to request a quota increase.
3. **Decide about today's daily job.** Its next run is 2026-11-18 18:10 UTC. If it tries 5 more uploads, it will probably hit the quota limit. Its own fallback is "deliver mode". Decide whether to pause it, reduce it, or let it run.
4. **Upload the avatar** (`gta/branding/out/avatar.png`) in YouTube Studio.
5. **Decide where the 24/7 worker runs.** It needs an always-on host. Options: attach `develant/youtube` and fold this into the existing daily job, or deploy `gta/` with the systemd unit in `gta/deploy/`.
6. **Optional: Twitch.** Create a Twitch developer app (free) and provide `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET` to enable Twitch discovery.
7. **Permissions.** Before any third-party creator's footage is clipped, a permission record must be created and verified. Nothing from third parties is clipped yet.
8. **Enable live publishing** only after you have reviewed the first batch: set `publishing.enabled` and `dry_run` in `gta/config.json`, and run the worker with `--live`.

## Not yet done (and why)

- **Clip extraction from streams** (Phase 7): needs permitted creators first.
- **Visual action recognition, audio excitement, streamer reaction scoring**: not implemented. The scoring module already accepts these signals and renormalizes when they are missing.
- **More templates and voiceover.** Only one template is built, and the Shorts carry on-screen captions only. No text-to-speech engine is installed.
- **24/7 deployment.** The worker is complete and tested, but nothing is running. This container is ephemeral.

## Risk notes

- The daily job and this pipeline share one quota pool. Both must be managed together.
- The brief says "5 to 7 Shorts a day". The conservative cost supports only about 5 uploads a day, and only when no other job uses the quota. Quality gates take priority over the number.

## Quota model (revised 2026-11-18)

Previous versions assumed 1600 units per `videos.insert` in one pool. That is replaced by three buckets, with the values in `gta/config.json` marked **unverified**:

| Bucket | Limit/day | Methods |
|---|---|---|
| uploads | 100 calls | `videos.insert` (1 per call) |
| search | 100 calls | `search.list` (1 per call) |
| shared | 10,000 units | everything else (per-method units in config) |

**Verification status.** `developers.google.com` is blocked by this environment's egress proxy (HTTP 403), so the official cost table and revision history could not be read. Secondary sources disagree on `videos.insert` (1, 100 or 1600 units) and on `search.list` (1 or 100). The owner's model is the one used here. Google's Cloud Console quota page for the project could not be read either: Service Usage returned 404 with the OAuth token.

**Daily job audit (`develant/youtube`, `pipeline/`).** Calls per day: `videos.insert` once per video (5), `videos.list` read-back once per video (5), `channels.list` and `playlistItems.list` pages for analytics (about 3–4), and `videos.update` only in lockcheck or deliver paths (0 on normal days). The analytics reports call is a separate API. The daily job's code still uses `INSERT_COST = 1600` for its own estimate, which is a leftover assumption. Under the owner's model it uses 5/100 upload calls and roughly 10–15 shared units per day.

**Feasibility for 5–7 Shorts per day (`plan_day`):**

| Model | Shorts/day | uploads | search (3 queries) | shared |
|---|---|---|---|---|
| owner's model | 5 | 5/85 fits | 3/90 fits | 364/8985 fits |
| owner's model | 7 | 7/85 fits | 3/90 fits | 364/8985 fits |
| 1600 units per upload (legacy, shared pool) | 5 | — | — | 8364 needed, 1000 left: does not fit |

The 364 shared units include 50 per playlist add (`playlistItems.insert`) and 1 per verify read. Under the owner's model, 5–7 Shorts a day is feasible with the quota far from the limit. Under the legacy model it is not.

**Monitoring.** `QuotaLedger` tracks all three buckets separately. `remaining()` and `summary()` report usable, external, used and remaining per bucket. `spend()` refuses a call that does not fit its bucket. `plan_day()` checks a whole day before any run. Config has `external_usage_by_day` per bucket, which must be filled in from the daily job's real usage.

**Still open:** read the real limits and per-method costs in Cloud Console (YouTube Data API v3 → Quotas) and compare with `gta/config.json`. The daily job's quota is outside this ledger, and it adds to the `external_usage_by_day` estimate.

## Clipping pipeline (added 2026-11-18)

- `gta/clip.py`: registers local source files with a creator and an acquisition type (`owned` or `creator_file`). Renders vertical 1080×1920 clips: blurred background fill, a hook card, timed captions, a source attribution line, and -14 LUFS loudness. Every clip is queued with the source's `creator_id`.
- `gta/moments.py`: finds highlight windows from the loudness of the source's own audio (per-second dBFS, top non-overlapping windows). Visual action and chat-spike signals are not implemented.
- Publishing is still gated by `gta/publisher.py`. Clips whose creator has no verified permission record stay queued and blocked. They are prepared, never published.

**Acquisition.** The pipeline does not download streams from YouTube or Twitch. Both platforms' terms do not allow it, and the pipeline would then depend on bypassing platform restrictions. Discovery (metadata, search, live status) is API-based. Clippable input is limited to files the owner holds or a creator has supplied under a verified record.

**Tests.** `tests/test_clip.py` covers moment detection (the loud-burst case, and the minimum gap between highlights) and the registration rules (metadata-only sources rejected, creator required). End-to-end rendering was run once on a synthetic generated source for testing only. That source and its output are not published.

## Source discovery and acquisition (added 2026-11-18)

**Where footage can come from (the end-to-end answer).**
- **Twitch clips, official download (`gta/sources/twitch.py`, `gta/authz.py`).** Discovery uses Helix Get Clips for the game (app token, no creator involved). A clip file is downloaded only through `clips/download` with the *creator's* OAuth user token. Each creator authorizes separately (`gta.authz connect-twitch` / `complete-twitch`). Without authorization a clip stays discovered and ranked (`ranked_no_file`); nothing is blocked globally.
- **YouTube live streams (`gta/sources/youtube_live.py`).** Used as a trend and discovery source only: `search.list` with `eventType=live`, `videos.list` for concurrency, and `liveChatMessages` for chat spikes (messages counted in 10 s buckets and compared with the stream's own median). No video is downloaded from YouTube.
- **Owner recordings (`gta/ingest.py`, `ingest_owner_folder`).** Every `.mp4` in `gta/ingest/owner/` is registered as an owned source for `self:bounce-rumble`, idempotently.
- **Creator-supplied files.** Registered with `acquisition=creator_file`.

**Pipeline.** Discovery -> popularity/velocity scoring (`scoring.popularity_score`, `velocity_per_hour`) -> acquisition if authorized or local -> the existing local clip pipeline (audio, visual, moment scoring, QC, fingerprint, queue). Each stage is independent. Authorization enables *downloading only*. Publishing still needs a verified permission record (`rights.py`), and the publisher's gate is unchanged.

**Tests.** `tests/test_sources.py` (10 tests): pagination, download URL parsing, per-creator authorization independence, token file permissions, unauthorized creators stay ranked, authorized creators download and register, chat spike detection, owner ingest idempotency, each source processed once. All use fakes; no network.

**Status of each piece**

| Piece | Status |
|---|---|
| Twitch Get Clips discovery | Implemented, tested with fakes. **Not run live**: no `TWITCH_CLIENT_ID`/`TWITCH_CLIENT_SECRET` in this environment. |
| Twitch OAuth (per creator) | Implemented (auth URL, code exchange, refresh, per-creator token files, revocation). Live test needs a Twitch app and a creator to approve it. |
| Twitch `clips/download` | Implemented. **Unverified**: the request path (`/helix/clips/download?id=`) and response fields (`landscape_download_url`, `portrait_download_url`) come from a community forum post, and the required scope (`channel:manage:clips`, configured) could not be checked against Twitch's reference. Confirm against the live API before relying on it. |
| YouTube live discovery | Implemented, tested with fakes. Not run live (uses quota; not run in this session). |
| Chat spike detection | Implemented on recorded buckets, tested. Requires a live `liveChatId` and recording while the stream is live. |
| Velocity and popularity | Implemented and tested (single snapshot: confidence 0.5). |
| Owner recording ingest | Implemented and tested. Put `.mp4` files in `gta/ingest/owner/`. |
| Local clip pipeline | Existing, with two fixes: the hook is limited to the clip length (an unlimited looped image plus `-shortest` never finished), and loudness uses a linear two-pass correction (single-pass gave -12 LUFS on short clips). |
| End-to-end clip on synthetic source | Done: 20.0 s, 1080x1920, AAC, -13.99 LUFS, framing and subtitle QC pass. Rendering takes about 17 s per 20 s clip. The file is a test output and not published. |

**Rockstar fan-content policy: what I could verify.** `support.rockstargames.com` is reachable only intermittently from this environment (the tunnel opens, then the origin closes the connection), so the page could not be read directly. From the search summary of Rockstar's "Policy on posting copyrighted Rockstar Games material" (page last updated 10 January 2025, per the summary):
- Fan gameplay videos on YouTube are allowed for **non-commercial** use, without spoilers.
- Playthroughs and narrated let's plays may include cutscenes. Videos made only of cutscenes are removed.
- Spoiler and early-unboxing videos and online cheating content are prohibited.
- Rockstar and 2K may review videos case by case and remove them even when they comply.
- Rockstar may change the policy at any time.

**What this means for monetization.** Non-commercial fan videos are the permitted case. A monetized Short built from GTA VI footage is not covered by that policy. It is a gray area that can trigger Content ID claims or removal, even when the source creator has authorized the download. Creator authorization covers only the creator's own recording. The game's visuals and music remain Rockstar's. Before any monetized publication of game footage, read the current page directly (once the host is reachable) and decide whether to get written terms from Rockstar. Until then, footage-based content should stay non-monetized or be kept out of automatic publication.

**Actions needed from you**
1. Create a free Twitch developer application and provide `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET` (use the secret store; do not paste them in chat).
2. Confirm the Twitch `clips/download` request and scope on the live reference, or allow me to test it with your own authorized channel.
3. Open the Rockstar policy page in a browser, and confirm or correct the summary above.
4. Put your own recordings in `gta/ingest/owner/`.

**Network.** The environment allowlist now includes `id.twitch.tv`, `api.twitch.tv`, and `support.rockstargames.com`. This change was approved through the environment tool and applies to all future sessions that use it.

