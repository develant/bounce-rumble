"""YouTube Data API quota ledger.

Costs and the daily budget come from config.json, never hard-coded assumptions
about the project. Google resets quota at midnight Pacific time, so the ledger
keys its days by Pacific date. `external_units_by_day` lets the owner account for
quota already spent by other jobs on the same Google Cloud project (for
example the existing daily upload job), which this ledger cannot see.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")


class QuotaExceeded(RuntimeError):
    pass


def pacific_day(now=None):
    return (now or datetime.now(PACIFIC)).astimezone(PACIFIC).strftime("%Y-%m-%d")


class QuotaLedger:
    def __init__(self, store, quota_config):
        self.store = store
        self.costs = dict(quota_config["costs"])
        self.daily_budget = int(quota_config["daily_budget_units"])
        # Units already spent on this Google Cloud project by other jobs, keyed by Pacific date.
        self.external_by_day = {k: int(v) for k, v in quota_config.get("external_units_by_day", {}).items()}
        self.margin = float(quota_config.get("safety_margin", 0.9))

    @property
    def usable_budget(self):
        return int(self.daily_budget * self.margin)

    def cost(self, method):
        if method not in self.costs:
            raise KeyError(f"no quota cost configured for {method!r}; add it to config.json")
        return int(self.costs[method])

    def used_today(self, day=None):
        row = self.store.fetchone(
            "SELECT COALESCE(SUM(units),0) AS u FROM quota_ledger WHERE day=?", (day or pacific_day(),)
        )
        return int(row["u"])

    def external_used(self, day=None):
        return self.external_by_day.get(day or pacific_day(), 0)

    def remaining_today(self, day=None):
        return self.usable_budget - self.external_used(day) - self.used_today(day)

    def can_spend(self, method, times=1):
        return self.remaining_today() >= self.cost(method) * times

    def spend(self, method, times=1):
        """Reserve units before the call. Raises QuotaExceeded instead of making a call that cannot fit."""
        units = self.cost(method) * times
        if self.remaining_today() < units:
            raise QuotaExceeded(
                f"{method} needs {units} units; {self.remaining_today()} usable units left today"
            )
        day = pacific_day()
        self.store.execute(
            "INSERT INTO quota_ledger (day, method, units, calls) VALUES (?,?,?,?)"
            " ON CONFLICT(day, method) DO UPDATE SET units=units+excluded.units, calls=calls+excluded.calls",
            (day, method, units, times),
        )

    def summary(self):
        day = pacific_day()
        return {
            "day_pacific": day,
            "daily_budget": self.daily_budget,
            "usable_budget": self.usable_budget,
            "external_units_estimate": self.external_used(day),
            "used_by_this_pipeline": self.used_today(day),
            "remaining": self.remaining_today(day),
        }
