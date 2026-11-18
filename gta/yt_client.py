"""YouTube Data API v3 client wrapper.

Credentials come only from environment variables (YT_CLIENT_ID, YT_CLIENT_SECRET,
YT_REFRESH_TOKEN) and are never printed or written to disk. Every call goes through
`request()`, which reserves quota first, so no call is made that the ledger cannot afford.
"""
import os

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TOKEN_URI = "https://oauth2.googleapis.com/token"


def credentials_from_env():
    missing = [k for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"missing environment variables: {', '.join(missing)}")
    return Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri=TOKEN_URI,
    )


class YouTubeClient:
    def __init__(self, quota, service=None):
        self.quota = quota
        self.service = service or build(
            "youtube", "v3", credentials=credentials_from_env(), cache_discovery=False
        )

    def request(self, method, call, times=1):
        """Reserve quota for `method`, then execute the googleapiclient request `call`."""
        self.quota.spend(method, times=times)
        return call.execute()
