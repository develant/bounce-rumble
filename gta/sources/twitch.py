"""Twitch Helix: clip discovery (Get Clips) and official clip download (Get Clips Download).

Official pieces used:
  - App access token (client credentials): Get Clips, Get Games/search categories. No creator involved.
  - User access token (OAuth authorization code flow) of the creator: Get Clips Download only.
    Each creator authorizes separately; without authorization nothing is downloaded for them.

Unverified in this environment (dev.twitch.tv is not reachable from here):
  - the exact scope required by Get Clips Download (configured as twitch.download_scopes),
  - the response field names (landscape_download_url is read first, then portrait_download_url).
These are marked as such in PROGRESS.md and should be checked once against the live API.
"""
import json
import time
import urllib.parse

import requests

HELIX = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"


class TwitchError(RuntimeError):
    pass


class TwitchAPI:
    def __init__(self, client_id, client_secret, session=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.session = session or requests.Session()
        self._app_token = None
        self._app_expiry = 0

    @classmethod
    def from_env(cls):
        import os

        cid, secret = os.environ.get("TWITCH_CLIENT_ID"), os.environ.get("TWITCH_CLIENT_SECRET")
        return cls(cid, secret) if cid and secret else None

    # -- tokens -------------------------------------------------------------
    def app_token(self):
        if self._app_token and time.time() < self._app_expiry - 60:
            return self._app_token
        r = self.session.post(TOKEN_URL, data={"client_id": self.client_id, "client_secret": self.client_secret,
                                               "grant_type": "client_credentials"}, timeout=30)
        if r.status_code != 200:
            raise TwitchError(f"app token failed: HTTP {r.status_code}")
        d = r.json()
        self._app_token = d["access_token"]
        self._app_expiry = time.time() + d.get("expires_in", 3600)
        return self._app_token

    def auth_url(self, redirect_uri, scopes, state):
        q = urllib.parse.urlencode({"client_id": self.client_id, "redirect_uri": redirect_uri,
                                    "response_type": "code", "scope": " ".join(scopes), "state": state})
        return f"{AUTHORIZE_URL}?{q}"

    def exchange_code(self, code, redirect_uri):
        r = self.session.post(TOKEN_URL, data={"client_id": self.client_id, "client_secret": self.client_secret,
                                               "code": code, "grant_type": "authorization_code",
                                               "redirect_uri": redirect_uri}, timeout=30)
        if r.status_code != 200:
            raise TwitchError(f"code exchange failed: HTTP {r.status_code}")
        return _token_record(r.json())

    def refresh(self, refresh_token):
        r = self.session.post(TOKEN_URL, data={"client_id": self.client_id, "client_secret": self.client_secret,
                                               "grant_type": "refresh_token", "refresh_token": refresh_token},
                              timeout=30)
        if r.status_code != 200:
            raise TwitchError(f"refresh failed: HTTP {r.status_code}")
        return _token_record(r.json(), fallback_refresh=refresh_token)

    # -- requests -----------------------------------------------------------
    def get(self, path, params, user_token=None):
        token = user_token or self.app_token()
        r = self.session.get(f"{HELIX}/{path}", params=params, timeout=30,
                             headers={"Client-Id": self.client_id, "Authorization": f"Bearer {token}"})
        if r.status_code == 429:
            raise TwitchError("rate limited (429)")
        if r.status_code >= 400:
            raise TwitchError(f"{path}: HTTP {r.status_code}")
        return r.json()

    def game_id(self, name):
        data = self.get("search/categories", {"query": name, "first": 20}).get("data", [])
        for c in data:
            if c.get("name", "").lower() == name.lower():
                return c["id"]
        return None

    def discover_clips(self, game_id, started_at, ended_at=None, max_clips=200):
        """Get Clips for a game in a time window, following pagination up to max_clips."""
        out, cursor = [], None
        while len(out) < max_clips:
            params = {"game_id": game_id, "started_at": started_at, "first": 100}
            if ended_at:
                params["ended_at"] = ended_at
            if cursor:
                params["after"] = cursor
            d = self.get("clips", params)
            out += d.get("data", [])
            cursor = d.get("pagination", {}).get("cursor")
            if not cursor or not d.get("data"):
                break
        return out[:max_clips]

    def download_urls(self, clip_ids, user_token):
        """Official Get Clips Download. Returns {clip_id: url}. Requires the creator's user token."""
        urls = {}
        for cid in clip_ids:
            d = self.get("clips/download", {"id": cid}, user_token=user_token)
            item = (d.get("data") or [{}])[0]
            url = item.get("landscape_download_url") or item.get("portrait_download_url")
            if url:
                urls[cid] = url
        return urls


def _token_record(d, fallback_refresh=None):
    return {
        "access_token": d["access_token"],
        "refresh_token": d.get("refresh_token") or fallback_refresh,
        "scope": d.get("scope", []),
        "expires_at": int(time.time()) + int(d.get("expires_in", 3600)),
    }


def clip_to_candidate(c):
    """Normalize a Helix clip to the fields the pipeline stores."""
    return {
        "id": c["id"],
        "title": c.get("title", ""),
        "url": c.get("url", ""),
        "broadcaster_id": c.get("broadcaster_id"),
        "broadcaster_name": c.get("broadcaster_name", ""),
        "creator_id": f"twitch-user:{c.get('broadcaster_id')}",
        "view_count": int(c.get("view_count", 0)),
        "created_at": c.get("created_at"),
        "duration": float(c.get("duration", 0)),
        "game_id": c.get("game_id"),
    }
