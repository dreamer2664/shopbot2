"""Poll checkpoint: remember how far we've processed orders.

Webhooks are the primary path; polling is the safety net (missed webhook,
receiver downtime). Without a checkpoint, every poll refetches everything —
with one, each poll only looks at orders updated since the last successful run.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Checkpoint:
    path: str = "data/checkpoint.json"

    def load_last_run(self) -> datetime:
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
            return datetime.fromisoformat(raw["last_run"])
        except (OSError, ValueError, KeyError):
            # Corrupt or missing checkpoint: fall back to "beginning of time".
            # Duplicate protection (fulfiller + ledger) makes a wide window safe.
            return datetime(2000, 1, 1, tzinfo=timezone.utc)

    def save_last_run(self, when: datetime | None = None) -> None:
        when = when or datetime.now(timezone.utc)
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"last_run": when.isoformat()}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)   # atomic on POSIX
