"""Content rights management. Default-deny.

A creator's footage may enter the automatic publishing pipeline only when a permission
record exists with status 'verified', is not expired, has a permission source and date,
records attribution, and (for monetized uploads) has monetization_allowed set.

Public availability is never treated as permission. Pending, denied and expired records
block publishing, and so does a missing record.
"""
from datetime import date

from .store import now_iso

STATUSES = {"pending", "verified", "denied", "expired"}
REQUIRED_FOR_VERIFIED = ("permission_source", "permission_date", "attribution", "expires_at")


class RightsError(ValueError):
    pass


def upsert_permission(store, creator_id, status, **fields):
    """Create or update a creator's permission record. Verified records must be complete."""
    if status not in STATUSES:
        raise RightsError(f"unknown status {status!r}")
    if status == "verified":
        missing = [k for k in REQUIRED_FOR_VERIFIED if not fields.get(k)]
        if missing:
            raise RightsError(f"cannot verify {creator_id}: missing {', '.join(missing)}")
    current = store.fetchone("SELECT * FROM permissions WHERE creator_id=?", (creator_id,)) or {}
    merged = {**current, **fields, "status": status, "updated_at": now_iso()}
    cols = [
        "display_name", "channel_url", "permission_source", "permission_date", "status",
        "monetization_allowed", "attribution", "restrictions", "expires_at", "updated_at",
    ]
    values = [merged.get(c) for c in cols]
    values[cols.index("monetization_allowed")] = int(bool(merged.get("monetization_allowed")))
    placeholders = ",".join("?" * (len(cols) + 1))
    store.execute(
        f"INSERT INTO permissions (creator_id, {','.join(cols)}) VALUES ({placeholders})"
        f" ON CONFLICT(creator_id) DO UPDATE SET " + ",".join(f"{c}=excluded.{c}" for c in cols),
        [creator_id, *values],
    )
    store.log("rights", f"permission {status}", creator_id=creator_id)


def check(store, creator_id, need_monetization=True, today=None):
    """Return (allowed: bool, reason: str). Never raises for a normal denial."""
    rec = store.fetchone("SELECT * FROM permissions WHERE creator_id=?", (creator_id,))
    if not rec:
        return False, "no permission record"
    if rec["status"] != "verified":
        return False, f"permission status is {rec['status']}"
    today = today or date.today().isoformat()
    if rec["expires_at"] and rec["expires_at"][:10] < today:
        store.execute("UPDATE permissions SET status='expired', updated_at=? WHERE creator_id=?",
                      (now_iso(), creator_id))
        return False, "permission expired"
    if not rec["permission_source"] or not rec["permission_date"] or not rec["attribution"]:
        return False, "permission record incomplete"
    if need_monetization and not rec["monetization_allowed"]:
        return False, "monetization not permitted"
    return True, "verified"
