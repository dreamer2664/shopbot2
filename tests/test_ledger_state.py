"""Ledger + checkpoint tests (crash-safety, reporting, poll windows)."""
import json
import os
from datetime import datetime, timedelta, timezone

from shopbot.pipeline.ledger import Ledger, LedgerEntry
from shopbot.pipeline.state import Checkpoint
from shopbot.providers.base import (Address, FulfillmentResult, Order,
                                    OrderLine)


def _order(oid="o1", total=25.5, currency="EUR"):
    return Order(oid, [OrderLine("p", "v", 1)],
                 Address("a", "b", "e", "s", "c", "z", "IT"),
                 total=total, currency=currency)


def test_ledger_roundtrip(tmp_path):
    led = Ledger(str(tmp_path / "l.jsonl"))
    led.record_fulfillment(FulfillmentResult("o1", "sent", "ful_1"), _order("o1"))
    led.record_fulfillment(FulfillmentResult("o2", "failed", error="bad zip"),
                           _order("o2"))
    led.record_launch("tee-1", True, detail={"price": 19.99})
    entries = list(led.entries())
    assert len(entries) == 3
    assert entries[0].kind == "fulfillment" and entries[0].status == "sent"
    assert entries[2].kind == "launch" and entries[2].detail["design"] == "tee-1"


def test_ledger_dedupe_ids(tmp_path):
    led = Ledger(str(tmp_path / "l.jsonl"))
    led.record_fulfillment(FulfillmentResult("o1", "sent", "f1"), _order("o1"))
    led.record_fulfillment(FulfillmentResult("o1", "duplicate"), _order("o1"))
    led.record_fulfillment(FulfillmentResult("o2", "failed", error="x"), _order("o2"))
    assert led.fulfilled_order_ids() == {"o1"}   # failed/duplicate don't count


def test_ledger_daily_summary_filters_by_day(tmp_path):
    led = Ledger(str(tmp_path / "l.jsonl"))
    led.record_fulfillment(FulfillmentResult("o1", "sent", "f"),
                           _order("o1", total=20.0, currency="EUR"))
    led.record_fulfillment(FulfillmentResult("o2", "sent", "f"),
                           _order("o2", total=10.0, currency="EUR"))
    led.record_fulfillment(FulfillmentResult("o3", "duplicate"), _order("o3"))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    s = led.daily_summary(today)
    assert s["sent"] == 2 and s["revenue"] == 30.0
    assert s["duplicates"] == 1 and s["currencies"] == ["EUR"]
    assert led.daily_summary("1999-01-01")["sent"] == 0


def test_ledger_skips_torn_lines(tmp_path):
    path = str(tmp_path / "l.jsonl")
    led = Ledger(path)
    led.record_fulfillment(FulfillmentResult("o1", "sent", "f"), _order("o1"))
    with open(path, "a") as f:
        f.write('{"v": 1, "kind": "fulfill')     # simulate power cut
    assert led.fulfilled_order_ids() == {"o1"}   # torn line skipped, no raise
    assert led.daily_summary()["sent"] == 1


def test_ledger_missing_file_is_empty(tmp_path):
    led = Ledger(str(tmp_path / "nope.jsonl"))
    assert list(led.entries()) == []
    assert led.daily_summary() == {"day": "all", "sent": 0, "duplicates": 0,
                                   "failed": 0, "revenue": 0, "currencies": []}


def test_ledger_unicode_safe(tmp_path):
    led = Ledger(str(tmp_path / "l.jsonl"))
    led.record_launch("té-ø-design-日本", True)
    assert list(led.entries())[0].detail["design"] == "té-ø-design-日本"


# ---- Checkpoint ----

def test_checkpoint_roundtrip(tmp_path):
    cp = Checkpoint(str(tmp_path / "cp.json"))
    assert cp.load_last_run().year == 2000        # missing -> epoch fallback
    when = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
    cp.save_last_run(when)
    assert cp.load_last_run() == when


def test_checkpoint_corrupt_file_falls_back(tmp_path):
    path = str(tmp_path / "cp.json")
    with open(path, "w") as f:
        f.write("{corrupt")
    cp = Checkpoint(path)
    assert cp.load_last_run().year == 2000        # no crash, safe fallback


def test_checkpoint_overwrite_is_atomic(tmp_path):
    cp = Checkpoint(str(tmp_path / "cp.json"))
    cp.save_last_run(datetime(2026, 1, 1, tzinfo=timezone.utc))
    cp.save_last_run(datetime(2026, 6, 1, tzinfo=timezone.utc))
    assert cp.load_last_run().month == 6
    assert not os.path.exists(str(tmp_path / "cp.json.tmp"))
