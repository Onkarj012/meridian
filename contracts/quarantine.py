"""Quarantine registry for unpromoted sources, features, and signals."""

from dataclasses import dataclass, field
from datetime import datetime, timezone

@dataclass
class QuarantineRegistry:
    entries: dict[str, dict] = field(default_factory=dict)

    def register(self, key, *, reason, status="quarantined", evidence=None, **metadata):
        entry = {
            "key": str(key),
            "reason": reason,
            "status": status,
            "evidence": evidence or {},
            "registered_at": datetime.now(timezone.utc).isoformat(),
            **metadata,
        }
        self.entries[str(key)] = entry
        return entry

    def promote(self, key, *, evidence=None):
        entry = self.entries.setdefault(str(key), {"key": str(key)})
        entry["status"] = "promoted"
        entry["evidence"] = evidence or entry.get("evidence", {})
        return entry
