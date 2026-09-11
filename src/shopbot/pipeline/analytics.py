"""Business analytics over the ledger — the bot's memory of what sold.

Turns raw fulfillment history into decisions:
  - daily revenue, volume, failure & duplicate rates
  - per-product performance (cost data is recorded at fulfillment time)
  - margin-erosion alert (research source [B]: re-check pricing quarterly;
    average seller nets ~20%, top performers 40-45% [C])
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..knowledge import AVERAGE_SELLER_MARGIN, MARGIN_FLOOR_BEGINNER
from .ledger import Ledger


@dataclass
class ProductStats:
    units: int = 0
    revenue: float = 0.0
    cost: float = 0.0

    @property
    def profit(self) -> float:
        return self.revenue - self.cost

    @property
    def margin(self) -> float | None:
        return (self.profit / self.revenue) if self.revenue > 0 else None


@dataclass
class BusinessReport:
    days_active: int = 0
    orders_sent: int = 0
    orders_failed: int = 0
    orders_duplicate: int = 0
    revenue: float = 0.0
    cost: float = 0.0
    launches_ok: int = 0
    launches_failed: int = 0
    per_day: dict = field(default_factory=dict)
    per_product: dict = field(default_factory=dict)
    alerts: list[str] = field(default_factory=list)

    @property
    def profit(self) -> float:
        return round(self.revenue - self.cost, 2)

    @property
    def margin(self) -> float | None:
        return (self.profit / self.revenue) if self.revenue > 0 else None

    @property
    def failure_rate(self) -> float:
        total = self.orders_sent + self.orders_failed
        return (self.orders_failed / total) if total else 0.0


def build_report(ledger: Ledger) -> BusinessReport:
    rep = BusinessReport()
    daily: dict[str, dict] = defaultdict(lambda: {"sent": 0, "revenue": 0.0})
    products: dict[str, ProductStats] = defaultdict(ProductStats)

    for e in ledger.entries():
        if e.kind == "launch":
            if e.status == "ok":
                rep.launches_ok += 1
            else:
                rep.launches_failed += 1
            continue
        if e.kind != "fulfillment":
            continue
        day = e.ts[:10]
        if e.status == "sent":
            rep.orders_sent += 1
            rep.revenue += e.total
            daily[day]["sent"] += 1
            daily[day]["revenue"] += e.total
            cost = float((e.detail or {}).get("cost") or 0.0)
            rep.cost += cost
            pid = (e.detail or {}).get("product_id")
            if pid:
                st = products[pid]
                st.units += 1
                st.revenue += e.total
                st.cost += cost
        elif e.status == "duplicate":
            rep.orders_duplicate += 1
        else:
            rep.orders_failed += 1

    rep.days_active = len(daily)
    rep.per_day = dict(daily)
    rep.per_product = dict(products)
    rep.revenue = round(rep.revenue, 2)
    rep.cost = round(rep.cost, 2)

    # --- alerts grounded in the researched benchmarks ---
    m = rep.margin
    if m is not None:
        if m < MARGIN_FLOOR_BEGINNER:
            rep.alerts.append(
                f"net margin {m:.0%} is BELOW the 20% survival floor [A][C] — "
                "raise prices or switch to a cheaper print provider")
        elif m < AVERAGE_SELLER_MARGIN:
            rep.alerts.append(
                f"net margin {m:.0%} under the ~20% average-seller benchmark [C]")
    if rep.orders_sent >= 10 and rep.failure_rate > 0.2:
        rep.alerts.append(
            f"order failure rate {rep.failure_rate:.0%} is high — check "
            "supplier status and webhook health")
    for pid, st in rep.per_product.items():
        if st.units >= 10 and st.margin is not None and st.margin < MARGIN_FLOOR_BEGINNER:
            rep.alerts.append(
                f"product {pid}: margin {st.margin:.0%} below floor on "
                f"{st.units} units — reprice this listing")
    return rep


def best_sellers(rep: BusinessReport, n: int = 5) -> list[tuple[str, ProductStats]]:
    return sorted(rep.per_product.items(), key=lambda kv: kv[1].units,
                  reverse=True)[:n]
