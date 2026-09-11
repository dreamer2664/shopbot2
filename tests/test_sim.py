"""Simulator determinism + economic sanity checks."""
from datetime import date

import pytest

from shopbot.sim.market import (SimProduct, expected_units, find_optimal_price,
                                simulate_day, simulate_period)
import random


@pytest.fixture()
def tee():
    return SimProduct(sku="t1", category="tshirt", cost=9.0,
                      base_demand=3.0, ref_price=25.0)


def test_deterministic_under_same_seed(tee):
    a = simulate_period(tee, 25.0, date(2026, 1, 1), 60, seed=7)
    b = simulate_period(tee, 25.0, date(2026, 1, 1), 60, seed=7)
    assert [(d.units, round(d.revenue, 2)) for d in a] == \
           [(d.units, round(d.revenue, 2)) for d in b]


def test_different_seeds_differ(tee):
    a = simulate_period(tee, 25.0, date(2026, 1, 1), 60, seed=1)
    b = simulate_period(tee, 25.0, date(2026, 1, 1), 60, seed=2)
    assert [d.units for d in a] != [d.units for d in b]


def test_demand_falls_as_price_rises(tee):
    low = expected_units(tee, 20.0, date(2026, 6, 15))
    mid = expected_units(tee, 25.0, date(2026, 6, 15))
    high = expected_units(tee, 35.0, date(2026, 6, 15))
    assert low > mid > high > 0


def test_seasonality_hoodie_beats_summer(tee):
    hoodie = SimProduct("h", "hoodie", 20.0, 1.5, 50.0)
    winter = expected_units(hoodie, 50.0, date(2026, 12, 1))
    summer = expected_units(hoodie, 50.0, date(2026, 7, 1))
    assert winter > summer * 1.5


def test_zero_and_negative_price_yield_no_demand(tee):
    assert expected_units(tee, 0, date(2026, 6, 1)) == 0.0
    assert expected_units(tee, -5, date(2026, 6, 1)) == 0.0


def test_units_are_nonnegative_ints(tee):
    for seed in range(20):
        for d in simulate_period(tee, 25.0, date(2026, 3, 1), 30, seed=seed):
            assert isinstance(d.units, int) and d.units >= 0


def test_accounting_identity(tee):
    for d in simulate_period(tee, 25.0, date(2026, 3, 1), 30, seed=3):
        assert d.revenue == pytest.approx(d.units * 25.0)
        assert d.cost == pytest.approx(d.units * 9.0)
        assert d.fees == pytest.approx(d.revenue * 0.03)
        assert d.profit == pytest.approx(d.revenue - d.cost - d.fees)


def test_optimal_price_lands_in_sane_band(tee):
    price, profit = find_optimal_price(tee, date(2026, 1, 1), days=90)
    assert profit > 0
    # Optimum must clear cost and sit within a believable retail band for tees
    assert price > tee.cost
    assert 15.0 <= price <= 45.0


def test_optimal_price_beats_naive_pricing(tee):
    opt_price, opt_profit = find_optimal_price(tee, date(2026, 1, 1), days=90)
    naive = 9.0 * 2.5           # the v1 multiplier heuristic
    naive_profit = sum(
        (lambda u: u * naive - u * tee.cost - u * naive * 0.03)(
            expected_units(tee, naive, date(2026, 1, 1)))
        for _ in range(90))
    assert opt_profit >= naive_profit * 0.99


def test_higher_cost_shifts_optimum_up():
    cheap = SimProduct("c", "tshirt", 6.0, 3.0, 25.0)
    pricey = SimProduct("p", "tshirt", 14.0, 3.0, 25.0)
    pc, _ = find_optimal_price(cheap, date(2026, 1, 1))
    pp, _ = find_optimal_price(pricey, date(2026, 1, 1))
    assert pp > pc


def test_poisson_mean_matches_lambda(tee):
    rng = random.Random(42)
    day = date(2026, 6, 15)
    lam = expected_units(tee, 25.0, day)
    draws = [simulate_day(rng, tee, 25.0, day).units for _ in range(4000)]
    mean = sum(draws) / len(draws)
    assert abs(mean - lam) < 0.25      # statistical, generous tolerance
