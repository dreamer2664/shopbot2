"""Pricing rules: cover cost, respect margin floor, end in .99."""
from __future__ import annotations

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


def price_variant(variant: Variant, multiplier: float, min_margin: float) -> float:
    floor = variant.cost + min_margin
    target = max(variant.cost * multiplier, floor)
    price = round_price(target)
    # Safety net: rounding must never push us below the margin floor.
    while price < floor:
        price = round(price + 1, 2)
    return price


def price_variants(product: Product, multiplier: float,
                   min_margin: float) -> dict[str, float]:
    """Returns {variant_id: retail_price} for every variant of a product."""
    return {
        v.variant_id: price_variant(v, multiplier, min_margin)
        for v in product.variants
    }
