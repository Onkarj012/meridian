"""Point-in-time availability stamps for contracted data."""

from datetime import datetime, timezone

def stamp_availability(record=None, *, available_at=None, source=None, **kwargs):
    item = dict(record or {})
    item["available_at"] = available_at or item.get("available_at") or datetime.now(timezone.utc).isoformat()
    if source is not None:
        item["availability_source"] = source
    item.update(kwargs)
    return item
