"""Encoded business knowledge, with sources.

Everything here came from published market research (see SOURCES). When a
number changes in the real world, it changes here in one place and every
module that consumes it (pricing, copywriting, simulator) picks it up.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# SOURCES (retrieved 2026-09-11)
#   [A] chayaani.com/blog/is-print-on-demand-profitable-2026
#       -> POD net margins typically 20-40%; $25 Etsy tee nets ~$6.64;
#          marketplace fees ~11-13% are the silent margin killer.
#   [B] printify.com/blog/t-shirt-pricing-calculator/
#       -> 40% margin is the 2026 benchmark; most sellers 30-50%;
#          tees retail $20-40; review pricing quarterly.
#   [C] raccoontransfers.com/blogs/guides/print-on-demand-profit-margins
#       -> category cost/retail/margin table (below); average seller ~20%,
#          top performers 40-45%.
#   [D] printify.com/blog/most-profitable-print-on-demand-products/
#       -> category seasonality (sweatshirts fall-winter, tees year-round).
#   [E] rewarx.com/blogs/ecommerce-character-limits-15-90
#       -> title 50-80 chars, keyword in first 30; meta title <=60;
#          meta description 120-155 (hard 160); alt text 15-90.
#   [F] charle.co.uk/articles/optimise-product-descriptions-seo/
#       -> descriptions 300-500 words; first 100 words weighted; human-first.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CategoryBenchmark:
    """Market data for one POD category. Costs/retail in USD. [C][D]"""
    key: str
    label: str
    cost_range: tuple[float, float]
    retail_range: tuple[float, float]
    margin_range: tuple[float, float]   # fraction of retail price
    season: str = "year-round"


CATEGORIES: dict[str, CategoryBenchmark] = {
    c.key: c for c in [
        CategoryBenchmark("tshirt", "T-Shirts", (8, 15), (25, 35), (0.30, 0.50)),
        CategoryBenchmark("hoodie", "Hoodies/Sweatshirts", (15, 30), (35, 60),
                          (0.25, 0.55), season="fall-winter"),
        CategoryBenchmark("mug", "Mugs", (4, 8), (15, 22), (0.40, 0.60)),
        CategoryBenchmark("tote", "Tote Bags", (7, 12), (20, 30), (0.35, 0.55)),
        CategoryBenchmark("phone_case", "Phone Cases", (5, 10), (18, 28),
                          (0.45, 0.65)),
        CategoryBenchmark("sticker", "Stickers", (1, 3), (4, 9), (0.50, 0.70)),
        CategoryBenchmark("poster", "Posters/Canvas", (8, 30), (20, 60),
                          (0.30, 0.60)),
    ]
}

# Fee stack we must price ABOVE (fraction of retail), beyond product cost:
#   Shopify Payments processing ~1.5-2% + 0.25 EUR fixed (EU cards),
#   Shopify plan transaction fee 0% when using Shopify Payments,
#   apps/misc buffer. Conservative combined estimate:
FEE_RATE_DEFAULT = 0.03          # [A] fees are the margin killer — overestimate
MARKETPLACE_FEE_RATE = 0.13      # if we ever sell via Etsy-like marketplace [A]

MARGIN_BENCHMARK_2026 = 0.40     # [B]
MARGIN_FLOOR_BEGINNER = 0.20     # [A][C] below this, returns/ads eat you alive
MARGIN_CEILING = 0.65            # above this you're usually priced out [C]

AVERAGE_SELLER_MARGIN = 0.20     # [C]
TOP_PERFORMER_MARGIN = 0.45      # [C]


# --- SEO constraints [E][F] -------------------------------------------------

@dataclass(frozen=True)
class SeoLimits:
    title_min: int = 50
    title_max: int = 80
    keyword_front_chars: int = 30   # primary keyword must appear this early [E]
    meta_title_max: int = 60
    meta_desc_min: int = 120
    meta_desc_max: int = 155        # 160 hard cap, 155 safe [E]
    alt_text_min: int = 15
    alt_text_max: int = 90
    description_words_min: int = 150   # simple products [F]
    description_words_max: int = 500


SEO = SeoLimits()

SOURCES = {
    "A": "https://chayaani.com/blog/is-print-on-demand-profitable-2026",
    "B": "https://printify.com/blog/t-shirt-pricing-calculator/",
    "C": "https://raccoontransfers.com/blogs/guides/print-on-demand-profit-margins",
    "D": "https://printify.com/blog/most-profitable-print-on-demand-products/",
    "E": "https://www.rewarx.com/blogs/ecommerce-character-limits-15-90",
    "F": "https://www.charle.co.uk/articles/optimise-product-descriptions-seo/",
}
