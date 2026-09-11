"""In-memory mock providers.

Used for dry-run mode and for the test suite. Deliberately records every call
so tests can assert on behaviour rather than on network traffic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from .base import (
    Address, Design, Fulfiller, FulfillmentResult, Notifier, Order, OrderLine,
    PermanentError, Product, ProductProvider, SocialPoster, Storefront,
    TransientError, Variant,
)

BLUEPRINTS = [
    {"id": "bp_tshirt", "title": "Unisex T-Shirt", "print_provider_id": 29,
     "variants": [("s", "black"), ("m", "black"), ("l", "black"), ("xl", "black")],
     "base_cost": 8.0},
    {"id": "bp_mug", "title": "Ceramic Mug 11oz", "print_provider_id": 41,
     "variants": [("11oz", "white")], "base_cost": 4.5},
    {"id": "bp_sticker", "title": "Die-Cut Sticker", "print_provider_id": 72,
     "variants": [("3x3", "white")], "base_cost": 1.2},
]


@dataclass
class MockProductProvider(ProductProvider):
    fail_create_times: int = 0      # inject transient failures
    reject_slugs: set[str] = field(default_factory=set)  # inject permanent failure
    uploads: dict[str, str] = field(default_factory=dict)
    drafts: dict[str, Product] = field(default_factory=dict)
    published: list[str] = field(default_factory=list)
    prices: dict[str, float] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    _n: int = 0

    def list_blueprints(self) -> list[dict]:
        self.calls.append("list_blueprints")
        return list(BLUEPRINTS)

    def upload_artwork(self, design: Design) -> str:
        self.calls.append(f"upload_artwork:{design.slug}")
        if design.slug in self.reject_slugs:
            raise PermanentError(f"design rejected: {design.slug}")
        self._n += 1
        upload_id = f"up_{self._n}"
        self.uploads[upload_id] = design.image_path
        return upload_id

    def create_product(self, product: Product, upload_id: str) -> Product:
        self.calls.append(f"create_product:{product.design.slug}")
        if upload_id not in self.uploads:
            raise PermanentError(f"unknown upload id {upload_id}")
        if self.fail_create_times > 0:
            self.fail_create_times -= 1
            raise TransientError("supplier 503, retry")
        blueprint = next(b for b in BLUEPRINTS if b["id"] == product.blueprint_id)
        self._n += 1
        product.printify_product_id = f"pp_{self._n}"
        product.variants = [
            Variant(variant_id=f"{product.printify_product_id}_v{i}", size=size,
                    color=color, cost=round(blueprint["base_cost"] + i * 0.25, 2))
            for i, (size, color) in enumerate(blueprint["variants"])
        ]
        self.drafts[product.printify_product_id] = product
        return product

    def set_prices(self, product: Product, price_by_variant: dict[str, float]) -> None:
        if not product.printify_product_id:
            raise PermanentError("product has no supplier id — create it first")
        self.calls.append(f"set_prices:{product.printify_product_id}")
        self.prices.update(price_by_variant)

    def publish(self, product: Product) -> Product:
        if not product.printify_product_id:
            raise PermanentError("cannot publish a product that was never created")
        self.calls.append(f"publish:{product.printify_product_id}")
        product.published = True
        self.published.append(product.printify_product_id)
        return product


@dataclass
class MockStorefront(Storefront):
    listings: dict[str, Product] = field(default_factory=dict)
    pending_orders: list[Order] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    _n: int = 0

    def upsert_product(self, product: Product) -> str:
        self.calls.append(f"upsert_product:{product.design.slug}")
        self._n += 1
        product.store_product_id = f"shop_{self._n}"
        self.listings[product.store_product_id] = product
        return product.store_product_id

    def fetch_orders_since(self, since: datetime) -> list[Order]:
        self.calls.append("fetch_orders_since")
        return [o for o in self.pending_orders if o.received_at >= since]

    def add_order(self, product: Product, qty: int = 1,
                  order_id: str | None = None) -> Order:
        """Test helper: fabricate a customer order for a live listing."""
        self._n += 1
        variant = product.variants[0]
        return Order(
            order_id=order_id or f"ord_{self._n}",
            lines=[OrderLine(product_id=product.store_product_id,
                             variant_id=variant.variant_id, quantity=qty)],
            ship_to=Address("Mario", "Rossi", "mario@example.com", "Via Roma 1",
                            "Milan", "20121", "IT"),
            total=(product.retail_price or 0) * qty,
        )


@dataclass
class MockFulfiller(Fulfiller):
    fail_times: int = 0
    fail_with: type = TransientError
    submitted: dict[str, Order] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    _n: int = 0

    def submit(self, order: Order) -> FulfillmentResult:
        # Duplicate check comes first: a replayed webhook must not even count
        # as a submit attempt (this is the never-double-print guarantee).
        if order.order_id in self.submitted:
            return FulfillmentResult(order.order_id, "duplicate",
                                     attempts=0)
        self.calls.append(f"submit:{order.order_id}")
        if not order.lines:
            return FulfillmentResult(order.order_id, "failed",
                                     error="order has no line items")
        if self.fail_times > 0:
            self.fail_times -= 1
            raise self.fail_with("injected failure")
        self._n += 1
        provider_order_id = f"ful_{self._n}"
        self.submitted[order.order_id] = order
        return FulfillmentResult(order.order_id, "sent", provider_order_id)


@dataclass
class MockNotifier(Notifier):
    owner_messages: list[str] = field(default_factory=list)
    emails: list[tuple[str, str, str]] = field(default_factory=list)
    fail_email_times: int = 0

    def notify_owner(self, message: str) -> None:
        self.owner_messages.append(message)

    def email_customer(self, to: str, subject: str, body: str) -> None:
        if self.fail_email_times > 0:
            self.fail_email_times -= 1
            raise TransientError("email provider 429")
        self.emails.append((to, subject, body))


@dataclass
class MockSocialPoster(SocialPoster):
    posts: list[dict] = field(default_factory=list)
    fail_times: int = 0
    _n: int = 0

    def post(self, text: str, image_urls: list[str], networks: list[str]) -> str:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise TransientError("social api 503")
        self._n += 1
        post_id = f"post_{self._n}"
        self.posts.append({"id": post_id, "text": text,
                           "images": image_urls, "networks": networks})
        return post_id


@dataclass
class MockProviders:
    """The full dry-run stack."""
    supplier: MockProductProvider = field(default_factory=MockProductProvider)
    store: MockStorefront = field(default_factory=MockStorefront)
    fulfiller: MockFulfiller = field(default_factory=MockFulfiller)
    notifier: MockNotifier = field(default_factory=MockNotifier)
    social: MockSocialPoster = field(default_factory=MockSocialPoster)
    sleep: Callable[[float], None] = lambda _s: None  # no real waiting in tests

    @classmethod
    def build(cls) -> "MockProviders":
        return cls()


def sample_design(slug: str = "milano-skyline", **kw) -> Design:
    return Design(
        slug=slug,
        title=kw.pop("title", "Milano Skyline Tee"),
        description=kw.pop("description", "Original minimal skyline illustration."),
        image_path=kw.pop("image_path", f"designs/{slug}.png"),
        tags=kw.pop("tags", ["milan", "skyline", "minimal"]),
        **kw,
    )
