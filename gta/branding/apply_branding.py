"""Apply the GTA VI banner to the Bounce Rumble channel. Only the banner image changes.

    python -m gta.branding.apply_branding --dry-run
    python -m gta.branding.apply_branding            # uploads banner, sets brandingSettings.image.bannerExternalUrl

Safety: confirms the authenticated channel id first, backs up the current branding settings and the
current banner image into gta/branding/backup/, and keeps every other branding field as it was.
The avatar cannot be set through the YouTube API; upload gta/branding/out/avatar.png in YouTube Studio
(Customisation > Branding > Picture).
"""
import argparse
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from googleapiclient.http import MediaFileUpload

from ..config import load_config, state_path
from ..quota import QuotaLedger
from ..store import Store
from ..yt_client import YouTubeClient

HERE = Path(__file__).resolve().parent
BACKUP = HERE / "backup"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    cfg = load_config()
    banner = HERE / "out" / "banner.png"
    if not banner.exists():
        print("missing banner; run python -m gta.branding.make_assets first", file=sys.stderr)
        return 2

    store = Store(state_path("gta.sqlite"))
    yt = YouTubeClient(QuotaLedger(store, cfg["quota"]))
    mine = yt.request("channels.list", yt.service.channels().list(part="id", mine=True))
    ids = [c["id"] for c in mine.get("items", [])]
    if ids != [cfg["channel"]["id"]]:
        print(f"refusing: authenticated channel(s) {ids} != {cfg['channel']['id']}", file=sys.stderr)
        return 2

    current = yt.request("channels.list", yt.service.channels().list(part="brandingSettings", mine=True))
    branding = current["items"][0]["brandingSettings"]
    BACKUP.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (BACKUP / f"brandingSettings_{ts}.json").write_text(json.dumps(branding, indent=2), encoding="utf-8")
    old_banner = branding.get("image", {}).get("bannerExternalUrl")
    banner_backup = "no previous banner"
    if old_banner:
        # Best effort: the image host may be blocked by the egress proxy. The URL is kept in the JSON backup.
        try:
            r = requests.get(old_banner + "=w2560", timeout=60)
            r.raise_for_status()
            (BACKUP / f"banner_before_{ts}.jpg").write_bytes(r.content)
            banner_backup = "image saved"
        except Exception as e:  # noqa: BLE001 - backup is optional, the URL is preserved in JSON
            banner_backup = f"image not downloaded ({type(e).__name__}); URL kept in brandingSettings backup"

    if args.dry_run:
        print(json.dumps({"dry_run": True, "backup_written": True, "old_banner_url_saved": bool(old_banner),
                          "banner_image_backup": banner_backup, "would_upload": str(banner)}, indent=2))
        return 0

    media = MediaFileUpload(str(banner), mimetype="image/png", resumable=False)
    yt.quota.spend("channelBanners.insert")
    new = yt.service.channelBanners().insert(body={}, media_body=media).execute()
    new_url = new["url"]

    updated = copy.deepcopy(branding)
    updated.setdefault("image", {})["bannerExternalUrl"] = new_url
    body = {"id": cfg["channel"]["id"], "brandingSettings": updated}
    yt.request("channels.update", yt.service.channels().update(part="brandingSettings", body=body))

    # YouTube can take a short while to show the new value: confirm the exact URL, polling briefly.
    import time

    applied = None
    for _ in range(6):
        check = yt.request("channels.list", yt.service.channels().list(part="brandingSettings", mine=True))
        applied = check["items"][0]["brandingSettings"].get("image", {}).get("bannerExternalUrl")
        if applied == new_url:
            break
        time.sleep(20)
    store.log("branding", "banner applied", ok=applied == new_url)
    print(json.dumps({"banner_applied": applied == new_url, "banner_url": applied, "banner_image_backup": banner_backup,
                      "backup_dir": str(BACKUP), "next_step": "upload gta/branding/out/avatar.png in YouTube Studio"},
                     indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
