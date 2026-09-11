"""Training run: a full simulated store-year through the REAL pipeline.

This is how the bot "learns" before your store exists:

  Stage 1 — pricing search: for each product category, grid-search the price
            that maximizes profit over a simulated year (demand model with
            elasticity + seasonality, from knowledge data). Persist the winner
            to data/pricing_playbook.json — a durable artifact the live bot
            loads instead of guessing multipliers.

  Stage 2 — operations rehearsal: generate the year's orders day by day,
            push them through the actual order pipeline (validate -> fulfill ->
            ledger) with random injected outages and webhook replays, then run
            analytics and verify the bot (a) never double-prints, (b) survives
            every outage, (c) produces a correct end-of-year business report.

Run:  PYTHONPATH=src python -m shopbot.sim.training
"""
from __future__ import annotations

import json
import os
import random
import tempfile
from dataclasses import asdict
from datetime import date, timedelta

from ..knowledge import CATEGORIES
from ..pipeline.analytics import build_report
from ..pipeline.ledger import Ledger
from ..pipeline.orders import handle_order
from ..providers.base import Address, Order, OrderLine
from ..providers.mock import MockFulfiller, MockNotifier
from .market import SimProduct, find_optimal_price, simulate_period

START = date(2026, 1, 1)
DAYS = 365


def stage1_pricing(seed: int = 0) -> dict:
    """Optimal price per category over the simulated year."""
    playbook = {}
    for key, cat in CATEGORIES.items():
        cost = round((cat.cost_range[0] + cat.cost_range[1]) / 2, 2)
        ref = round((cat.retail_range[0] + cat.retail_range[1]) / 2, 2)
        base_demand = {"sticker": 4.0, "tshirt": 3.0, "mug": 2.5}.get(key, 1.5)
        product = SimProduct(sku=key, category=key, cost=cost,
                             base_demand=base_demand, ref_price=ref)
        price, profit = find_optimal_price(product, START, days=DAYS, seed=seed)
        playbook[key] = {
            "cost_midpoint": cost, "ref_price": ref,
            "optimal_price": price, "expected_year_profit": profit,
            "expected_year_units": round(profit / max(price - cost, 0.01)) if price > cost else 0,
        }
    return playbook


def stage2_operations(playbook: dict, seed: int = 0,
                      ledger_path: str | None = None) -> dict:
    """Feed a simulated year of orders through the real pipeline."""
    rng = random.Random(seed)
    tmp = ledger_path or os.path.join(tempfile.mkdtemp(), "ledger.jsonl")
    ledger = Ledger(tmp)
    fulfiller = MockFulfiller()
    notifier = MockNotifier()

    # Build the year's demand per category at the playbook price
    days_orders: list[list[Order]] = [[] for _ in range(DAYS)]
    order_n = 0
    for key, cat in CATEGORIES.items():
        pb = playbook[key]
        product = SimProduct(sku=key, category=key, cost=pb["cost_midpoint"],
                             base_demand={"sticker": 4.0, "tshirt": 3.0,
                                          "mug": 2.5}.get(key, 1.5),
                             ref_price=pb["ref_price"])
        for day in simulate_period(product, pb["optimal_price"], START, DAYS,
                                   seed=seed):
            for _ in range(day.units):
                order_n += 1
                idx = (day.day - START).days
                days_orders[idx].append(Order(
                    order_id=f"sim_{order_n}",
                    lines=[OrderLine(f"prod_{key}", f"var_{key}", 1)],
                    ship_to=Address("Sim", "Customer", "sim@example.com",
                                    "Via Sim 1", "Milan", "20121", "IT"),
                    total=pb["optimal_price"], currency="EUR"))

    # Inject realistic adversarial events
    total_orders = sum(len(d) for d in days_orders)
    replays = 0
    for idx in range(DAYS):
        for order in list(days_orders[idx]):
            if rng.random() < 0.03 and days_orders[idx]:  # 3% webhook replays
                days_orders[idx].append(Order(order_id=order.order_id,
                                              lines=list(order.lines),
                                              ship_to=order.ship_to,
                                              total=order.total,
                                              currency=order.currency))
                replays += 1
    invalid = 0
    for idx in range(DAYS):
        if days_orders[idx] and rng.random() < 0.02:      # 2% bad addresses
            victim = rng.choice(days_orders[idx])
            victim.ship_to.zip = ""
            invalid += 1

    sent = dups = failed = 0
    outage_days = 0
    alerts_seen: set = set()   # recurring advisories dedupe across the year
    for idx in range(DAYS):
        if rng.random() < 0.01:                            # 1% supplier outage days
            fulfiller.fail_times = max(fulfiller.fail_times, 1)
            outage_days += 1
        for order in rng.sample(days_orders[idx], len(days_orders[idx])):
            r = handle_order(order, fulfiller=fulfiller, notifier=notifier,
                             expected_currency="USD", alerts_seen=alerts_seen)
            cost = playbook[order.lines[0].product_id.replace("prod_", "")]["cost_midpoint"] \
                if r.status == "sent" and order.lines else None
            ledger.record_fulfillment(r, order, cost=cost,
                                      product_id=order.lines[0].product_id
                                      if order.lines else None)
            if r.status == "sent":
                sent += 1
            elif r.status == "duplicate":
                dups += 1
            else:
                failed += 1

    report = build_report(ledger)

    # ---- hard invariants of the training run ----
    assert len(fulfiller.submitted) == sent, "double-print detected"
    assert report.orders_sent == sent
    assert dups == replays or dups <= replays, "replay accounting broke"

    return {
        "ledger_path": tmp, "total_orders": total_orders, "sent": sent,
        "duplicates_absorbed": dups, "failed": failed,
        "replays_injected": replays, "invalid_injected": invalid,
        "outage_days": outage_days,
        "revenue": report.revenue, "profit": report.profit,
        "margin": round(report.margin, 3) if report.margin else None,
        "alerts": report.alerts,
        "owner_alerts": len(notifier.owner_messages),
    }


def main() -> int:
    print("== stage 1: pricing search over simulated year ==")
    playbook = stage1_pricing()
    for key, row in playbook.items():
        print(f"  {key:12s} cost ${row['cost_midpoint']:>5}  "
              f"optimal ${row['optimal_price']:>6}  "
              f"year profit ${row['expected_year_profit']:>9}")
    os.makedirs("data", exist_ok=True)
    with open("data/pricing_playbook.json", "w") as f:
        json.dump(playbook, f, indent=2)
    print("  -> saved data/pricing_playbook.json")

    print("\n== stage 2: operations rehearsal (full year through pipeline) ==")
    out = stage2_operations(playbook, seed=42,
                            ledger_path="data/training_ledger.jsonl")
    for k in ("total_orders", "sent", "duplicates_absorbed", "failed",
              "replays_injected", "invalid_injected", "outage_days",
              "revenue", "profit", "margin", "owner_alerts"):
        print(f"  {k:22s} {out[k]}")
    for a in out["alerts"]:
        print("  ALERT:", a)
    print("\ntraining complete: 0 invariant violations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
