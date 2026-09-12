"""Cursor state so we never reprocess a transaction across ticks or restarts.

Rho's feed is newest-first and its page token is an opaque cursor into history,
so the "cursor" we track is a client-side watermark:
  - `seen`: transaction id -> last status we emitted (status changes => "updated" event)
  - `high_water_initiated_at`: newest initiated_at seen (handy for an initiated_after filter later)
Persisted as JSON under STATE_DIR (gitignored).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from app.models import EventKind, Transaction

log = logging.getLogger(__name__)

STATE_VERSION = 1


@dataclass
class CursorState:
    seen: dict[str, str] = field(default_factory=dict)
    high_water_initiated_at: str | None = None
    backfill_done: bool = False
    last_poll_at: str | None = None

    def classify(self, tx: Transaction) -> tuple[EventKind | None, str | None]:
        """Return (event kind or None if already seen unchanged, previous status)."""
        previous = self.seen.get(tx.id)
        if previous is None:
            return "new", None
        if previous != tx.status:
            return "updated", previous
        return None, previous

    def record(self, tx: Transaction) -> None:
        self.seen[tx.id] = tx.status
        stamp = tx.initiated_at.isoformat()
        if self.high_water_initiated_at is None or stamp > self.high_water_initiated_at:
            self.high_water_initiated_at = stamp

    @property
    def seen_count(self) -> int:
        return len(self.seen)

    # -- persistence ---------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> "CursorState":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("cursor file %s unreadable (%s); starting fresh", path, exc)
            return cls()
        return cls(
            seen=dict(data.get("seen") or {}),
            high_water_initiated_at=data.get("high_water_initiated_at"),
            backfill_done=bool(data.get("backfill_done", False)),
            last_poll_at=data.get("last_poll_at"),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": STATE_VERSION,
            "seen": self.seen,
            "high_water_initiated_at": self.high_water_initiated_at,
            "backfill_done": self.backfill_done,
            "last_poll_at": self.last_poll_at,
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=1))
        os.replace(tmp, path)  # atomic on POSIX

    def summary(self) -> dict:
        return {
            "seen": self.seen_count,
            "high_water_initiated_at": self.high_water_initiated_at,
            "backfill_done": self.backfill_done,
            "last_poll_at": self.last_poll_at,
        }
