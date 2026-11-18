"""YouTube Data API quota ledger with three independent buckets.

Buckets (each resets at midnight Pacific time):
  uploads  - videos.insert calls per day
  search   - search.list calls per day
  shared   - units per day for every other method (one pool)

Each method maps to (bucket, units per call). The limits, the per-method costs and the
external usage come from config.json. The values in config are the owner's stated model
(2026-11-18) and are NOT verified against Google's documentation, which this environment
cannot reach. Change them after checking the Cloud Console quota page and the official
reference. Nothing here assumes 1600 units per upload.

External usage is quota spent by other jobs on the same Google Cloud project (for example
the existing daily upload job). This ledger cannot see it, so it is entered per day in config.
"""
import math
from datetime import datetime
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
BUCKETS = ("uploads", "search", "shared")


class QuotaExceeded(RuntimeError):
    def __init__(self, message, bucket=None):
        super().__init__(message)
        self.bucket = bucket


def pacific_day(now=None):
    return (now or datetime.now(PACIFIC)).astimezone(PACIFIC).strftime("%Y-%m-%d")


class QuotaLedger:
    def __init__(self, store, quota_config):
        self.store = store
        self.buckets = {}
        for name in BUCKETS:
            self.buckets[name] = int(quota_config["buckets"][name]["daily_limit"])
        self.methods = {m: (v["bucket"], int(v["units"])) for m, v in quota_config["methods"].items()}
        self.margin = float(quota_config.get("safety_margin", 0.9))
        self.external = {day: {b: int(v.get(b, 0)) for b in BUCKETS}
                         for day, v in quota_config.get("external_usage_by_day", {}).items()}
        self.model = quota_config.get("model", "unspecified")

    def usable(self, bucket):
        return math.floor(self.buckets[bucket] * self.margin)

    def cost(self, method):
        if method not in self.methods:
            raise KeyError(f"no quota cost configured for {method!r}; add it to config.json")
        return self.methods[method]

    def used(self, bucket, day=None):
        day = day or pacific_day()
        rows = self.store.fetchall(
            "SELECT method, units FROM quota_ledger WHERE day=?", (day,))
        total = 0
        for r in rows:
            b, _ = self.methods.get(r["method"], (None, 0))
            if b == bucket:
                total += r["units"]
        return total

    def external_used(self, bucket, day=None):
        return self.external.get(day or pacific_day(), {}).get(bucket, 0)

    def remaining(self, bucket, day=None):
        return self.usable(bucket) - self.external_used(bucket, day) - self.used(bucket, day)

    # Compatibility: the shared pool is what the older code called "remaining today".
    def remaining_today(self, day=None):
        return self.remaining("shared", day)

    def can_spend(self, method, times=1):
        bucket, units = self.cost(method)
        return self.remaining(bucket) >= units * times

    def spend(self, method, times=1):
        """Reserve units in the method's bucket before the call. Refuses calls that do not fit."""
        bucket, per = self.cost(method)
        need = per * times
        left = self.remaining(bucket)
        if left < need:
            raise QuotaExceeded(f"{method} needs {need} {bucket} units; {left} left today", bucket=bucket)
        day = pacific_day()
        self.store.execute(
            "INSERT INTO quota_ledger (day, method, units, calls) VALUES (?,?,?,?)"
            " ON CONFLICT(day, method) DO UPDATE SET units=units+excluded.units, calls=calls+excluded.calls",
            (day, method, need, times),
        )

    def summary(self, day=None):
        day = day or pacific_day()
        out = {"day_pacific": day, "model": self.model, "buckets": {}}
        for b in BUCKETS:
            out["buckets"][b] = {
                "daily_limit": self.buckets[b],
                "usable": self.usable(b),
                "external_estimate": self.external_used(b, day),
                "used_by_pipeline": self.used(b, day),
                "remaining": self.remaining(b, day),
            }
        return out


def plan_day(shorts, searches, other_calls, methods, buckets_limits, margin=0.9, external=None):
    """Feasibility check for a whole day (no ledger needed).

    shorts:    uploads per day (each costs one videos.insert)
    searches:  search.list calls per day
    other_calls: dict method -> calls per day for every other method
    Returns per-bucket demand, usable capacity and whether it fits.
    """
    demand = {"uploads": 0, "search": 0, "shared": 0}
    for m, calls in [("videos.insert", shorts), ("search.list", searches), *other_calls.items()]:
        if calls:
            b, units = methods[m]["bucket"], methods[m]["units"]
            demand[b] += calls * units
    result = {}
    for b in BUCKETS:
        cap = math.floor(buckets_limits[b]["daily_limit"] * margin) - (external or {}).get(b, 0)
        result[b] = {"demand": demand[b], "capacity": cap, "fits": demand[b] <= cap}
    return result
