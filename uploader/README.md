# Bounce Rumble Uploader

`upload_video.py` is the complete upload script used by the Bounce Rumble channel's daily job.
It uploads videos only to the owner's own channel through the YouTube Data API v3 (resumable upload, optional scheduled publishing).

- Credentials: OAuth 2.0 refresh token of the channel owner, read from environment variables. Never stored in this repository.
- Scopes: `https://www.googleapis.com/auth/youtube`, `https://www.googleapis.com/auth/yt-analytics.readonly`
- Privacy policy: https://develant.github.io/bounce-rumble/privacy.html
