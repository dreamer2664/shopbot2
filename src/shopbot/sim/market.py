"""Market simulator: months of synthetic business data to train decisions.

The bot can't learn from your real store until the store exists — but it CAN
learn from simulated ones. This module models demand as a function of price
(elasticity), seasonality, and noise, then lets the pricing engine search for
the strategy that maximizes simulated profit.

Demand model (deliberately simple + documented, not pretend-precise):
    units/day ~ Poisson( base_demand * exp(-elasticity * (price/ref - 1))
                         * seasonal_factor(month) * noise )

Elasticity values are set per category from the benchmark ranges in
knowledge/ (retail ranges tell us how much price spread the market tolerates:
wide range = low elasticity, narrow = high). They are assumptions to be
REPLACED by real store data — the analytics module exists exactly for that.

Everything is seeded: the same seed always produces the same market, so
strategy comparisons are apples-to-apples and tests are deterministic.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..knowledge import CATEGORIES, FEE_RATE_DEFAULT

# Assumed price elasticity per category (higher = more price-sensitive).
# Derived qualitatively from retail spread in CATEGORIES [C]: stickers have a
# tiny spread (buyers won't pay 3x) -> high elasticity; posters/canvas have
# wide spread (art value dominates) -> lower elasticity.
ELASTICITY = {
    "tshirt": 2.2, "hoodie": 1.8, "mug": 2.5, "tote": 2.0,
    "phone_case": 1.6, "sticker": 3.0, "poster": 1.4,
}

# Northern-hemisphere seasonality multipliers by month (1=Jan) [D]:
# apparel peaks fall-winter + gift season; mugs peak winter; stickers flat.
SEASONALITY = {
    "tshirt":      [0.9, 0.9, 1.0, 1.0, 1.1, 1.2, 1.2, 1.1, 1.0, 1.0, 1.3, 1.4],
    "hoodie":      [1.2, 1.1, 1.0, 0.8, 0.7, 0.6, 0.6, 0.7, 0.9, 1.2, 1.4, 1.5],
    "mug":         [1.1, 1.0, 0.9, 0.8, 0.8, 0.8, 0.8, 0.9, 1.0, 1.1, 1.3, 1.5],
    "tote":        [0.9, 0.9, 1.0, 1.1, 1.2, 1.3, 1.3, 1.2, 1.1, 1.0, 0.9, 1.0],
    "phone_case":  [1.0] * 12,
    "sticker":     [1.0] * 12,
    "poster":      [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.1, 1.1, 1.1, 1.2, 1.2],
}


@dataclass
class SimProduct:
    sku: str
    category: str
    cost: float
    base_demand: float          # units/day at the reference price
    ref_price: float            # market-typical retail price

    @property
    def elasticity(self) -> float:
        return ELASTICITY.get(self.category, 2.0)

    def seasonal(self, day: date) -> float:
        return SEASONALITY.get(self.category, [1.0] * 12)[day.month - 1]


@dataclass
class SimDay:
    day: date
    units: int
    revenue: float
    cost: float
    fees: float

    @property
    def profit(self) -> float:
        return self.revenue - self.cost - self.fees


def expected_units(product: SimProduct, price: float, day: date) -> float:
    """Deterministic demand (no noise) — the strategy search uses this."""
    if price <= 0:
        return 0.0
    ratio = price / product.ref_price
    lam = (product.base_demand
           * math.exp(-product.elasticity * (ratio - 1))
           * product.seasonal(day))
    return max(0.0, lam)


def simulate_day(rng: random.Random, product: SimProduct, price: float,
                 day: date, fee_rate: float = FEE_RATE_DEFAULT) -> SimDay:
    lam = expected_units(product, price, day)
    # Poisson sampling via inverse-CDF for small lambdas, normal approx above
    if lam <= 0:
        units = 0
    elif lam < 30:
        units = _poisson(rng, lam)
    else:
        units = max(0, int(rng.gauss(lam, math.sqrt(lam))))
    revenue = units * price
    return SimDay(day=day, units=units, revenue=revenue,
                  cost=units * product.cost, fees=revenue * fee_rate)


def _poisson(rng: random.Random, lam: float) -> int:
    """Knuth sampling — fine for the small lambdas of a new store."""
    L = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1
        if k > 10_000:            # pathological guard
            return k


def simulate_period(product: SimProduct, price: float, start: date,
                    days: int, seed: int,
                    fee_rate: float = FEE_RATE_DEFAULT) -> list[SimDay]:
    rng = random.Random(seed)
    return [simulate_day(rng, product, price, start + timedelta(d), fee_rate)
            for d in range(days)]


def period_profit(product: SimProduct, price: float, start: date, days: int,
                  seed: int, fee_rate: float = FEE_RATE_DEFAULT) -> float:
    return sum(d.profit for d in
               simulate_period(product, price, start, days, seed, fee_rate))


def find_optimal_price(product: SimProduct, start: date, days: int = 90,
                       seed: int = 0,
                       candidates: list[float] | None = None,
                       fee_rate: float = FEE_RATE_DEFAULT) -> tuple[float, float]:
    """Grid-search the price maximizing simulated profit. Returns (price, profit).

    Uses EXPECTED units (noise-free) for ranking so the search finds the true
    optimum of the model instead of chasing lucky noise draws.
    """
    if candidates is None:
        lo = max(1.0, product.cost * 1.2)
        hi = product.ref_price * 2.0
        candidates = [round(lo + (hi - lo) * i / 60, 2) for i in range(61)]
    best_price, best_profit = None, -math.inf
    for price in candidates:
        profit = 0.0
        for d in range(days):
            day = start + timedelta(d)
            units = expected_units(product, price, day)
            revenue = units * price
            profit += revenue - units * product.cost - revenue * fee_rate
        if profit > best_profit:
            best_price, best_profit = price, profit
    return round(best_price, 2), round(best_profit, 2)
