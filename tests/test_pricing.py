"""Pricing rule tests."""
import pytest

from shopbot.providers.base import Product, Variant
from shopbot.providers.mock import sample_design
from shopbot.pipeline.pricing import price_variant, price_variants, round_price


def v(cost: float) -> Variant:
    return Variant(variant_id="x", size="m", color="black", cost=cost)


def test_round_price_snaps_to_99():
    assert round_price(17.4) == 17.99
    assert round_price(17.99) == 17.99
    assert round_price(18.01) == 18.99
    assert round_price(0.5) == 0.99


def test_round_price_rejects_nonpositive():
    with pytest.raises(ValueError):
        round_price(0)


def test_multiplier_wins_when_higher_than_floor():
    # cost 10 * 2.5 = 25 -> 25.99 ; floor = 10 + 8 = 18
    assert price_variant(v(10.0), multiplier=2.5, min_margin=8.0) == 25.99


def test_margin_floor_wins_for_cheap_items():
    # cost 1.2 * 2.5 = 3.0 ; floor = 1.2 + 8 = 9.2 -> 9.99
    assert price_variant(v(1.2), multiplier=2.5, min_margin=8.0) == 9.99


def test_price_never_below_floor_after_rounding():
    # cost 10.5 -> floor 18.5, target 26.25 -> 26.99, fine.
    # cost 10.9 -> target 18.53, floor 18.9 -> 18.99 satisfies both
    price = price_variant(v(10.9), multiplier=1.7, min_margin=8.0)
    assert price >= 10.9 + 8.0
    assert price == 18.99


def test_price_variants_covers_all_variants():
    p = Product(design=sample_design(), blueprint_id="bp", print_provider_id=1,
                variants=[Variant(f"v{i}", "m", "black", 5.0 + i) for i in range(3)])
    prices = price_variants(p, 2.5, 8.0)
    assert set(prices) == {"v0", "v1", "v2"}
    assert all(prices[k] >= 5.0 + int(k[1]) + 8.0 for k in prices)
