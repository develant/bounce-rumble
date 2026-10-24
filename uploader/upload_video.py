"""Bounce Rumble Uploader: upload one video to the Bounce Rumble channel.

Complete, working upload script used by the channel's daily job. It uploads only to the
channel of the signed-in owner (Bounce Rumble). Credentials come from environment variables
(YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN), created once by the owner via OAuth 2.0
with the scopes youtube and yt-analytics.readonly.

    python upload_video.py video.mp4 --title "Which color escapes first?" \
        --description-file desc.txt --publish-at 2026-11-02T12:00:00Z

Requires: google-api-python-client, google-auth
"""
import argparse
import os
import random
import sys
import time

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

RETRYABLE_STATUS = {500, 502, 503, 504}
MAX_RETRIES = 8
CHUNK = 8 * 1024 * 1024


def youtube_client():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("youtube", "v3", credentials=creds)


def upload(yt, path, title, description, tags, publish_at=None):
    status = {
        "privacyStatus": "private" if publish_at else "public",
        "selfDeclaredMadeForKids": False,
        "containsSyntheticMedia": False,
    }
    if publish_at:
        status["publishAt"] = publish_at
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "24",
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": status,
    }
    media = MediaFileUpload(path, mimetype="video/mp4", chunksize=CHUNK, resumable=True)
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    # Resumable upload: only the failed chunk is retried, never the whole upload
    response, retries = None, 0
    while response is None:
        try:
            progress, response = request.next_chunk()
            if progress:
                print(f"uploaded {int(progress.progress() * 100)}%")
        except HttpError as e:
            if e.resp.status not in RETRYABLE_STATUS:
                raise
            retries += 1
            if retries > MAX_RETRIES:
                raise
            time.sleep(min(60, 2 ** retries) + random.random())
    return response


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("video")
    ap.add_argument("--title", required=True)
    ap.add_argument("--description-file")
    ap.add_argument("--tags", default="shorts,physics,satisfying,simulation")
    ap.add_argument("--publish-at", help="RFC3339 UTC time; video stays private until then")
    args = ap.parse_args()

    description = open(args.description_file).read() if args.description_file else ""
    yt = youtube_client()
    video = upload(yt, args.video, args.title, description, args.tags.split(","), args.publish_at)
    check = yt.videos().list(part="status", id=video["id"]).execute()["items"][0]["status"]
    print(f"video id: {video['id']}  https://youtube.com/shorts/{video['id']}")
    print(f"status: {check.get('uploadStatus')} / {check.get('privacyStatus')}"
          + (f" / publishAt {check['publishAt']}" if check.get("publishAt") else ""))


if __name__ == "__main__":
    sys.exit(main())
