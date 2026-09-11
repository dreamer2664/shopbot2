"""Training-run tests: short horizon, real pipeline, invariants asserted."""
from datetime import date

import pytest

from shopbot.sim import training
from shopbot.sim.market import SimProduct, find_optimal_price


def test_stage1_pricing_covers_every_category():
    # shorten the horizon so the test stays fast
    original_days = training.DAYS
    training.DAYS = 30
    try:
        pb = training.stage1_pricing(seed=1)
    finally:
        training.DAYS = original_days
    from shopbot.knowledge import CATEGORIES
    assert set(pb) == set(CATEGORIES)
    for key, row in pb.items():
        assert row["optimal_price"] > row["cost_midpoint"], key
        assert row["expected_year_profit"] >= 0


def test_stage2_end_to_end_invariants(tmp_path):
    original_days = training.DAYS
    training.DAYS = 45
    try:
        pb = training.stage1_pricing(seed=2)
        out = training.stage2_operations(pb, seed=5,
                                         ledger_path=str(tmp_path / "l.jsonl"))
    finally:
        training.DAYS = original_days

    assert out["total_orders"] > 0
    assert out["sent"] + out["duplicates_absorbed"] + out["failed"] >= out["total_orders"]
    assert out["duplicates_absorbed"] <= out["replays_injected"]
    # every replayed order id was printed at most once (asserted inside stage2)
    assert out["profit"] is not None
    # ledger totals must be numeric — the field-slip regression
    assert isinstance(out["revenue"], float)


def test_playbook_prices_land_within_market_bands():
    from shopbot.knowledge import CATEGORIES
    original_days = training.DAYS
    training.DAYS = 60
    try:
        pb = training.stage1_pricing(seed=3)
    finally:
        training.DAYS = original_days
    for key, row in pb.items():
        lo, hi = CATEGORIES[key].retail_range
        # optimizer may go above the band, but never absurdly so
        assert row["optimal_price"] <= hi * 1.6, key
        assert row["optimal_price"] >= lo * 0.5, key
