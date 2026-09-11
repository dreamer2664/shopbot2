"""Copywriting + pricing v2 tests: every rule from the research is enforced."""
import pytest

from shopbot.knowledge import CATEGORIES, SEO
from shopbot.knowledge.copywriting import (alt_text, description_html,
                                           generate_copy, meta_description,
                                           meta_title, seo_title)
from shopbot.pipeline.pricing import (net_margin, price_for_margin,
                                      price_variant_v2, round_price)
from shopbot.providers.base import Variant
from shopbot.providers.mock import sample_design


# ---- copywriting ----

@pytest.mark.parametrize("slug,tags", [
    ("a", ["milan", "skyline"]),
    ("b", ["duomo"]),
    ("c", ["very-long-tag-name-here", "art", "line"]),
    ("d", []),
])
def test_generated_copy_passes_seo_checklist(slug, tags):
    d = sample_design(slug, title="Original Artwork Piece", tags=tags)
    copy = generate_copy(d, "tshirt")
    assert SEO.title_min <= len(copy.title) <= SEO.title_max, copy.title
    assert len(copy.meta_title) <= SEO.meta_title_max
    assert SEO.meta_desc_min <= len(copy.meta_description) <= SEO.meta_desc_max
    assert SEO.alt_text_min <= len(copy.alt_text) <= SEO.alt_text_max
    assert copy.warnings == [] or not tags   # keyword warning only if no tags


def test_title_frontloads_keyword():
    d = sample_design("x", title="Sunset", tags=["milan-skyline", "art"])
    t = seo_title(d, "tshirt")
    assert t.lower().startswith("milan skyline")
    assert "milan skyline" in t[:SEO.keyword_front_chars].lower()


def test_title_never_exceeds_cap_with_long_inputs():
    d = sample_design("y", title="A" * 200, tags=["b" * 60])
    assert len(seo_title(d)) <= SEO.title_max


def test_description_has_structure_and_care_info():
    html = description_html(sample_design("z", tags=["art"]), "mug")
    assert "<h2>" in html and "<ul>" in html
    assert "printed on demand" in html
    assert "wash cold" in html or "care" in html.lower()


def test_copy_tags_capped():
    d = sample_design("t", tags=[f"tag{i}" for i in range(30)])
    assert len(generate_copy(d).tags) <= 13


# ---- pricing v2 (fee-aware) ----

def test_price_for_margin_inverts_the_fee_formula():
    cost, target, fee = 10.0, 0.40, 0.03
    price = price_for_margin(cost, target, fee)
    assert net_margin(price, cost, fee) == pytest.approx(target, abs=1e-9)


def test_price_for_margin_rejects_impossible_target():
    with pytest.raises(ValueError):
        price_for_margin(10.0, target_margin=0.99, fee_rate=0.03)


def test_v2_price_covers_fees_and_target_margin():
    v = Variant("1", "M", "black", cost=9.0)
    dec = price_variant_v2(v, "tshirt", target_margin=0.40)
    assert dec.price > v.cost
    assert net_margin(dec.price, v.cost, 0.03) >= 0.40 - 0.03  # .99 rounding slack
    assert dec.expected_margin >= 0.35


def test_v2_flags_price_far_above_market():
    # a $30-cost item priced at 40% margin lands ~$52 — above tee range 25-35
    v = Variant("1", "M", "black", cost=30.0)
    dec = price_variant_v2(v, "tshirt", target_margin=0.40)
    assert any("above market range" in w for w in dec.warnings)


def test_v2_cheap_item_snapped_up_to_market_floor():
    # Sticker cost $1: naive 20% margin -> $1.99, but market bears $4-9 [C].
    # Engine must snap to the market floor instead of leaving money behind.
    v = Variant("1", "3x3", "white", cost=1.0)
    dec = price_variant_v2(v, "sticker", target_margin=0.20)
    assert dec.price == 3.99
    assert dec.expected_margin > 0.6      # market floor is generous for cheap items
    assert dec.warnings == []


def test_v2_margin_clamped_to_survival_band():
    v = Variant("1", "M", "black", cost=9.0)
    dec_low = price_variant_v2(v, "tshirt", target_margin=0.05)   # clamps to 0.20
    assert dec_low.expected_margin >= 0.18
    dec_high = price_variant_v2(v, "tshirt", target_margin=0.95)  # clamps to 0.65
    assert dec_high.expected_margin <= 0.68


def test_all_categories_present_and_consistent():
    for key, c in CATEGORIES.items():
        assert c.cost_range[0] < c.cost_range[1]
        assert c.retail_range[0] < c.retail_range[1]
        assert 0 < c.margin_range[0] < c.margin_range[1] <= 1
