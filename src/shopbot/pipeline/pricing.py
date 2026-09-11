"""Pricing engine v2 — fee-aware, benchmark-checked.

Research lesson (knowledge source [A]): "fees, not base cost, quietly kill
margins." v1 priced on cost+margin only. v2 solves for the retail price that
delivers the TARGET NET margin AFTER the payment/platform fee stack:

    net = price*(1 - fee_rate) - cost
    margin = net / price
    => price = cost / (1 - fee_rate - target_margin)

Results are sanity-checked against published category benchmarks [C] and
flagged when they fall outside the market range (priced out or underpriced).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..knowledge import (CATEGORIES, FEE_RATE_DEFAULT, MARGIN_BENCHMARK_2026,
                         MARGIN_CEILING, MARGIN_FLOOR_BEGINNER)
from ..providers.base import Product, Variant


def round_price(value: float) -> float:
    """Round up to the nearest .99 price point (e.g. 17.4 -> 17.99)."""
    if value <= 0:
        raise ValueError("price must be positive")
    whole = int(value)
    candidate = whole + 0.99
    if candidate < value:
        candidate = whole + 1 + 0.99
    return round(candidate, 2)


def floor_price(value: float) -> float:
    """Largest .99 price point <= value (e.g. 25.0 -> 24.99, 3.99 -> 3.99)."""
    if value <= 0:
        raise ValueError("price must be positive")
    whole = int(value)
    candidate = round(whole + 0.99, 2)
    if candidate > value + 1e-9:
        candidate = round(whole - 0.01, 2)
    if candidate <= 0:
        candidate = 0.99
    return candidate


@dataclass
class PriceDecision:
    variant_id: str
    cost: float
    price: float
    expected_margin: float          # net of fees, fraction of price
    warnings: list[str]


def net_margin(price: float, cost: float, fee_rate: float) -> float:
    if price <= 0:
        return 0.0
    return (price * (1 - fee_rate) - cost) / price


def price_for_margin(cost: float, target_margin: float,
                     fee_rate: float = FEE_RATE_DEFAULT) -> float:
    """Minimum retail price achieving target NET margin after fees."""
    denom = 1 - fee_rate - target_margin
    if denom <= 0:
        raise ValueError(
            f"target margin {target_margin} + fees {fee_rate} >= 100%: "
            "unachievable at any price")
    return cost / denom


def price_variant(variant: Variant, multiplier: float, min_margin: float,
                  fee_rate: float = FEE_RATE_DEFAULT) -> float:
    """v1-compatible behaviour: max(cost*multiplier, cost+min_margin) -> .99.

    Kept for backwards compatibility; prefer price_variant_v2.
    """
    floor = variant.cost + min_margin
    target = max(variant.cost * multiplier, floor)
    price = round_price(target)
    while price < floor:
        price = round(price + 1, 2)
    return price


def price_variant_v2(variant: Variant, category: str = "tshirt",
                     target_margin: float = MARGIN_BENCHMARK_2026,
                     fee_rate: float = FEE_RATE_DEFAULT) -> PriceDecision:
    """Fee-aware pricing, benchmark-checked against market data [B][C]."""
    warnings: list[str] = []
    target_margin = max(MARGIN_FLOOR_BEGINNER,
                        min(target_margin, MARGIN_CEILING))
    raw = price_for_margin(variant.cost, target_margin, fee_rate)
    price = round_price(raw)

    bench = CATEGORIES.get(category)
    if bench:
        lo, hi = bench.retail_range
        if price > hi * 1.15:
            warnings.append(
                f"price {price} well above market range {lo}-{hi} for "
                f"{bench.label} — demand risk")
        elif price < lo:
            # Below the market floor = leaving money on the table (a $1-cost
            # sticker priced at $1.99 when the market bears $4-9). Snap up to
            # the highest .99 point at or below the market floor.
            price = floor_price(float(lo))
    achieved = net_margin(price, variant.cost, fee_rate)
    if achieved < MARGIN_FLOOR_BEGINNER:
        warnings.append(f"net margin {achieved:.0%} below 20% survival floor")
    return PriceDecision(variant.variant_id, variant.cost, price,
                         achieved, warnings)


# ---- v1 API kept for the existing launch pipeline --------------------------

def price_variants(product: Product, multiplier: float,
                   min_margin: float) -> dict[str, float]:
    return {
        v.variant_id: price_variant(v, multiplier, min_margin)
        for v in product.variants
    }
