"""Create the public GTA VI playlist on the Bounce Rumble channel (idempotent).

    python -m gta.setup_playlist            # create, or reuse an existing playlist with the same title
    python -m gta.setup_playlist --dry-run  # show what would happen, spend no quota

Nothing is moved or deleted. Existing videos are only added when they are genuinely GTA VI
content; see gta/PLAYLIST_AUDIT.md for the review of the current catalogue.
"""
import argparse
import json
import sys

from .config import load_config, save_config, state_path
from .quota import QuotaLedger
from .store import Store
from .yt_client import YouTubeClient


def find_existing(yt, channel_id, title):
    page = None
    while True:
        call = yt.service.playlists().list(part="snippet,status", channelId=channel_id, maxResults=50,
                                           **({"pageToken": page} if page else {}))
        data = yt.request("playlists.list", call)
        for item in data.get("items", []):
            if item["snippet"]["title"] == title:
                return item
        page = data.get("nextPageToken")
        if not page:
            return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config()
    title = cfg["playlist"]["title"]
    channel_id = cfg["channel"]["id"]
    if args.dry_run:
        print(json.dumps({"would_create_or_reuse": title, "channel": channel_id,
                          "privacy": cfg["playlist"]["privacy_status"]}, ensure_ascii=False))
        return 0

    store = Store(state_path("gta.sqlite"))
    quota = QuotaLedger(store, cfg["quota"])
    yt = YouTubeClient(quota)

    # Confirm we are on the intended channel before writing anything.
    mine = yt.request("channels.list", yt.service.channels().list(part="id,snippet", mine=True))
    ids = [c["id"] for c in mine.get("items", [])]
    if ids != [channel_id]:
        print(f"refusing to continue: authenticated channel(s) {ids} do not match {channel_id}", file=sys.stderr)
        return 2

    playlist = find_existing(yt, channel_id, title)
    if playlist is None:
        body = {
            "snippet": {
                "title": title,
                "description": (
                    "The best GTA VI moments, epic clips and launch coverage from Bounce Rumble.\n"
                    "Only clips with verified permission are added. "
                    + cfg["channel"]["disclaimer"]
                ),
                "defaultLanguage": "en",
            },
            "status": {"privacyStatus": cfg["playlist"]["privacy_status"]},
        }
        playlist = yt.request("playlists.insert", yt.service.playlists().insert(part="snippet,status", body=body))
        action = "created"
    else:
        action = "reused"

    cfg["playlist"]["playlist_id"] = playlist["id"]
    save_config(cfg)
    store.log("playlist", f"playlist {action}", playlist_id=playlist["id"], title=title)
    print(json.dumps({"action": action, "playlist_id": playlist["id"],
                      "url": f"https://www.youtube.com/playlist?list={playlist['id']}",
                      "quota": quota.summary()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
