"""Product launch pipeline: design -> supplier draft -> priced -> store -> socials."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..config import Config
from ..knowledge.copywriting import ListingCopy, generate_copy
from ..providers.base import (
    Design, PermanentError, Product, ProductProvider, SocialPoster,
    Storefront, TransientError,
)
from .pricing import price_variant_v2, price_variants
from .retry import retry


@dataclass
class ProductLaunchResult:
    design_slug: str
    success: bool
    product: Product | None = None
    post_id: str | None = None
    error: str | None = None
    steps_done: list[str] = field(default_factory=list)
    copy: ListingCopy | None = None
    pricing_warnings: list[str] = field(default_factory=list)


def build_social_copy(product: Product) -> tuple[str, list[str]]:
    """Text + image refs for the launch post. Our own designs, our own words."""
    d = product.design
    tags = " ".join(f"#{t}" for t in d.tags[:5])
    text = (f"New in: {d.title}\n{d.description}\n{tags}")
    return text, [d.image_path]


def launch_product(
    design: Design,
    blueprint_id: str,
    *,
    supplier: ProductProvider,
    store: Storefront,
    social: SocialPoster | None = None,
    cfg: Config | None = None,
    sleep: Callable[[float], None] = lambda _s: None,
    social_networks: list[str] | None = None,
    category: str = "tshirt",
    use_v2_pricing: bool = True,
    catalog=None,
) -> ProductLaunchResult:
    """Run one design through the full launch flow.

    Order matters: nothing is published to the store before the supplier draft
    exists and is priced, and socials only fire after the listing is live —
    so we never advertise a product that can't be bought.

    category: knowledge-base key (tshirt/mug/sticker/...) — drives benchmark
    checks in v2 pricing and the SEO copy generator.
    use_v2_pricing: fee-aware pricing with market-band checks (default).
    """
    cfg = cfg or Config.mock()
    result = ProductLaunchResult(design_slug=design.slug, success=False)
    product = Product(design=design, blueprint_id=blueprint_id,
                      print_provider_id=0)

    try:
        result.copy = generate_copy(design, category)
        result.steps_done.append("copy")

        upload_id = retry(lambda: supplier.upload_artwork(design), sleep=sleep)
        result.steps_done.append("upload")

        def _create() -> Product:
            return supplier.create_product(product, upload_id)

        product = retry(_create, sleep=sleep)
        result.steps_done.append("create_draft")

        if use_v2_pricing:
            decisions = [price_variant_v2(v, category) for v in product.variants]
            prices = {d.variant_id: d.price for d in decisions}
            for d in decisions:
                result.pricing_warnings.extend(
                    f"{d.variant_id}: {w}" for w in d.warnings)
        else:
            prices = price_variants(product, cfg.price_multiplier, cfg.min_margin)
        supplier.set_prices(product, prices)
        result.steps_done.append("price")
        # One retail price per product for display/socials: the cheapest variant.
        product.retail_price = min(prices.values())

        supplier.publish(product)
        result.steps_done.append("publish_supplier")

        store.upsert_product(product)
        result.steps_done.append("upsert_store")

        if catalog is not None:
            # persist BOTH id spaces now — the only moment we know them
            catalog.record(product, prices)
            result.steps_done.append("catalog")

        if social is not None:
            text, images = build_social_copy(product)
            post_id = retry(
                lambda: social.post(text, images,
                                    social_networks or ["bluesky", "telegram"]),
                sleep=sleep,
            )
            result.post_id = post_id
            result.steps_done.append("social_post")

        result.success = True
        result.product = product
        return result

    except PermanentError as exc:
        result.error = f"permanent: {exc}"
        result.product = product
        return result
    except TransientError as exc:
        result.error = f"transient (retries exhausted): {exc}"
        result.product = product
        return result
