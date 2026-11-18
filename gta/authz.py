"""Per-creator authorization for official downloads. Independent for each creator.

A creator's authorization (OAuth grant for Twitch Get Clips Download) only enables *downloading*
their clips. It is NOT a publishing right. Publishing still requires a verified permission record
(rights.py). Authorizations are optional: a creator without one stays in discovery and ranking.

Tokens are stored under gta/state/tokens/ (git-ignored, mode 0600). Never committed, never printed.
"""
import json
import os
import secrets
import sys
from pathlib import Path

from .config import state_path
from .store import now_iso

SCOPE_ALIASES = {"download": "twitch_download"}


def _token_path(creator_id):
    d = state_path("tokens")
    d.mkdir(parents=True, exist_ok=True)
    safe = creator_id.replace(":", "_").replace("/", "_")
    return d / f"{safe}.json"


def grant(store, creator_id, platform, token_record, scope_name="twitch_download"):
    p = _token_path(creator_id)
    p.write_text(json.dumps(token_record), encoding="utf-8")
    os.chmod(p, 0o600)
    store.execute(
        "INSERT INTO authorizations (creator_id, platform, scope_name, status, granted_at, updated_at)"
        " VALUES (?,?,?,'granted',?,?) ON CONFLICT(creator_id, scope_name) DO UPDATE SET"
        " status='granted', updated_at=excluded.updated_at",
        (creator_id, platform, scope_name, now_iso(), now_iso()),
    )
    store.log("authz", "authorization granted", creator_id=creator_id, scope=scope_name)


def revoke(store, creator_id, scope_name="twitch_download"):
    p = _token_path(creator_id)
    if p.exists():
        p.unlink()
    store.execute("UPDATE authorizations SET status='revoked', updated_at=? WHERE creator_id=? AND scope_name=?",
                  (now_iso(), creator_id, scope_name))
    store.log("authz", "authorization revoked", creator_id=creator_id, scope=scope_name)


def status(store, creator_id, scope_name="twitch_download"):
    row = store.fetchone("SELECT status FROM authorizations WHERE creator_id=? AND scope_name=?",
                         (creator_id, scope_name))
    return row["status"] if row else "none"


def user_token(store, twitch, creator_id, scope_name="twitch_download"):
    """Return a valid access token for the creator, refreshing it if needed. None if not authorized."""
    if status(store, creator_id, scope_name) != "granted":
        return None
    p = _token_path(creator_id)
    if not p.exists():
        return None
    rec = json.loads(p.read_text(encoding="utf-8"))
    import time

    if rec["expires_at"] - 60 < time.time():
        if not rec.get("refresh_token"):
            return None
        rec = twitch.refresh(rec["refresh_token"])
        p.write_text(json.dumps(rec), encoding="utf-8")
        os.chmod(p, 0o600)
    return rec["access_token"]


def new_state():
    return secrets.token_urlsafe(16)


def cli_connect(argv=None):
    """Print the official authorization URL for one creator. The creator approves in their browser."""
    import argparse

    from .config import load_config
    from .sources.twitch import TwitchAPI

    ap = argparse.ArgumentParser(prog="gta.authz connect-twitch")
    ap.add_argument("--creator", required=True, help="e.g. twitch-user:12345")
    args = ap.parse_args(argv)
    cfg = load_config()
    tw = TwitchAPI.from_env()
    if not tw:
        print("TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET not set", file=sys.stderr)
        return 2
    state = new_state()
    url = tw.auth_url(cfg["twitch"]["redirect_uri"], cfg["twitch"]["download_scopes"], state)
    print(json.dumps({"creator": args.creator, "authorize_url": url, "state": state}, indent=2))
    return 0


def cli_complete(argv=None):
    """Exchange the code the creator obtained after approving. Stores the token per creator."""
    import argparse

    from .config import load_config
    from .sources.twitch import TwitchAPI
    from .store import Store

    ap = argparse.ArgumentParser(prog="gta.authz complete-twitch")
    ap.add_argument("--creator", required=True)
    ap.add_argument("--code", required=True)
    args = ap.parse_args(argv)
    cfg = load_config()
    tw = TwitchAPI.from_env()
    rec = tw.exchange_code(args.code, cfg["twitch"]["redirect_uri"])
    store = Store(str(state_path("gta.sqlite")))
    grant(store, args.creator, "twitch", rec)
    print(json.dumps({"creator": args.creator, "status": "granted"}, indent=2))
    return 0
