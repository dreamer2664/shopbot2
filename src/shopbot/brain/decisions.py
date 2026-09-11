"""Decision layer: report + market data -> concrete recommended actions.

Deliberately rules-based, not a neural net: every recommendation must be
explainable ("why is the bot telling me this?") and testable. The rules come
from the encoded research (knowledge/) and sharpen as REAL store data
accumulates in the ledger — that's the learning loop:

    act -> record -> analyze -> decide -> act

A future LLM can be layered on for creative tasks (design ideas, copy
variants); decisions that touch money stay rule-based and auditable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..knowledge import AVERAGE_SELLER_MARGIN, MARGIN_FLOOR_BEGINNER
from ..pipeline.analytics import BusinessReport, best_sellers


@dataclass
class Action:
    kind: str          # reprice | investigate | expand | celebrate | none
    target: str        # product id / area
    reason: str        # human-readable, cites the data
    params: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.kind}] {self.target}: {self.reason}"


def decide(report: BusinessReport, min_units_for_opinion: int = 5) -> list[Action]:
    actions: list[Action] = []

    # 1) Margin below survival floor -> reprice or switch provider
    if report.margin is not None and report.margin < MARGIN_FLOOR_BEGINNER:
        actions.append(Action(
            "reprice", "catalog",
            f"overall net margin {report.margin:.0%} is below the 20% survival "
            f"floor; raise prices ~15% or move to a cheaper print provider",
            {"suggested_increase": 0.15}))

    # 2) Per-product margin laggards with real volume
    for pid, st in report.per_product.items():
        if st.units >= min_units_for_opinion and st.margin is not None:
            if st.margin < MARGIN_FLOOR_BEGINNER:
                actions.append(Action(
                    "reprice", pid,
                    f"{st.units} units at {st.margin:.0%} margin — under the "
                    f"floor; reprice this listing",
                    {"units": st.units, "margin": round(st.margin, 3)}))

    # 3) High failure rate -> operational investigation
    if report.orders_sent >= 10 and report.failure_rate > 0.2:
        actions.append(Action(
            "investigate", "fulfillment",
            f"{report.failure_rate:.0%} of orders are failing — check supplier "
            f"status page and webhook logs"))

    # 4) Best sellers -> expand the winning direction
    if report.orders_sent >= min_units_for_opinion:
        for pid, st in best_sellers(report, n=2):
            if st.units >= 2 * min_units_for_opinion and (
                    st.margin or 0) >= AVERAGE_SELLER_MARGIN:
                actions.append(Action(
                    "expand", pid,
                    f"{st.units} units at {st.margin:.0%} margin — make more "
                    f"designs in this style/category",
                    {"units": st.units}))

    # 5) Healthy business with no actions
    if not actions and report.orders_sent >= min_units_for_opinion:
        m = report.margin or 0
        if m >= AVERAGE_SELLER_MARGIN:
            actions.append(Action(
                "celebrate", "catalog",
                f"{report.orders_sent} orders at {m:.0%} net margin — at/above "
                f"the average-seller benchmark; keep the launch cadence"))
    return actions


def format_advice(actions: list[Action]) -> str:
    if not actions:
        return ("no recommendations yet — the brain needs a few more orders "
                "before it has an opinion")
    return "\n".join(f"{i+1}. {a}" for i, a in enumerate(actions))
