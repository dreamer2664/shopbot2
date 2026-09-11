"""Append-only JSONL ledger of business events.

Why: the process will restart, and "which orders did we already fulfill?"
must survive that. The fulfiller's in-memory dedupe only protects one run.
Every fulfillment outcome (sent/duplicate/failed) is appended here, giving:
  - crash-safe audit trail
  - daily revenue/margin reporting
  - a second idempotency layer (was this order already handled on disk?)

Format: one JSON object per line. Never rewritten, only appended — so a
partially written last line (power cut) can be skipped without losing history.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator

from ..providers.base import FulfillmentResult, Order

LEDGER_VERSION = 1


@dataclass
class LedgerEntry:
    ts: str
    kind: str            # "fulfillment" | "launch" | "alert"
    order_id: str | None
    status: str | None
    provider_order_id: str | None
    total: float
    currency: str
    error: str | None
    detail: dict | None = None

    def to_json(self) -> str:
        return json.dumps({"v": LEDGER_VERSION, **self.__dict__},
                          ensure_ascii=False, default=str)

    @classmethod
    def from_json(cls, line: str) -> "LedgerEntry | None":
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            return None      # torn last line after a crash: skip, don't die
        known = {f for f in cls.__dataclass_fields__}  # noqa
        return cls(**{k: v for k, v in raw.items() if k in known and k != "v"})


class Ledger:
    def __init__(self, path: str = "data/ledger.jsonl"):
        self.path = path

    def _ensure_dir(self) -> None:
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)

    def append(self, entry: LedgerEntry) -> None:
        self._ensure_dir()
        # Write to temp file + append content: keeps concurrency simple and
        # avoids interleaved partial lines from the webhook threads.
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(entry.to_json() + "\n")
            f.flush()
            os.fsync(f.fileno())

    def record_fulfillment(self, result: FulfillmentResult,
                           order: Order | None = None) -> None:
        self.append(LedgerEntry(
            ts=datetime.now(timezone.utc).isoformat(),
            kind="fulfillment", order_id=result.order_id, status=result.status,
            provider_order_id=result.provider_order_id,
            total=order.total if order else 0.0,
            currency=order.currency if order else "",
            error=result.error))

    def record_launch(self, design_slug: str, success: bool,
                      detail: dict | None = None, error: str | None = None) -> None:
        self.append(LedgerEntry(
            ts=datetime.now(timezone.utc).isoformat(),
            kind="launch", order_id=None,
            status="ok" if success else "failed", provider_order_id=None,
            total=0.0, currency="", error=error,
            detail={"design": design_slug, **(detail or {})}))

    def entries(self) -> Iterator[LedgerEntry]:
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = LedgerEntry.from_json(line)
                if entry is not None:
                    yield entry

    def fulfilled_order_ids(self) -> set[str]:
        """Orders already SENT to production in a previous run."""
        return {e.order_id for e in self.entries()
                if e.kind == "fulfillment" and e.status == "sent" and e.order_id}

    def daily_summary(self, day: str | None = None) -> dict:
        """day: 'YYYY-MM-DD' (UTC). Returns counts + revenue of sent orders."""
        sent = dup = failed = 0
        revenue = 0.0
        currencies: set[str] = set()
        for e in self.entries():
            if e.kind != "fulfillment":
                continue
            if day and not e.ts.startswith(day):
                continue
            if e.status == "sent":
                sent += 1
                revenue += e.total
                currencies.add(e.currency)
            elif e.status == "duplicate":
                dup += 1
            else:
                failed += 1
        return {"day": day or "all", "sent": sent, "duplicates": dup,
                "failed": failed, "revenue": round(revenue, 2),
                "currencies": sorted(c for c in currencies if c)}
