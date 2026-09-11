"""Provider interfaces + shared data models.

Everything downstream depends on these ABCs, not on Printify/Shopify directly.
That is what makes the whole pipeline testable offline in dry-run mode.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal


class ProviderError(Exception):
    """Base error for provider failures."""


class TransientError(ProviderError):
    """Retryable: rate limit, timeout, 5xx."""


class PermanentError(ProviderError):
    """Not retryable: bad credentials, invalid payload, rejected design."""


@dataclass
class Design:
    """A piece of artwork we own the rights to."""
    slug: str
    title: str
    description: str
    image_path: str
    tags: list[str] = field(default_factory=list)


@dataclass
class Variant:
    variant_id: str
    size: str
    color: str
    cost: float  # Printify production + shipping cost, USD


@dataclass
class Product:
    """A catalog product created from a Design on one blueprint."""
    design: Design
    blueprint_id: str
    print_provider_id: int
    variants: list[Variant] = field(default_factory=list)
    printify_product_id: str | None = None
    store_product_id: str | None = None
    retail_price: float | None = None
    published: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class OrderLine:
    product_id: str
    variant_id: str
    quantity: int


@dataclass
class Address:
    first_name: str
    last_name: str
    email: str
    address1: str
    city: str
    zip: str
    country: str  # ISO-3166 alpha-2
    phone: str = ""
    region: str = ""


@dataclass
class Order:
    """A customer order from the storefront."""
    order_id: str          # storefront order id — also our idempotency key
    lines: list[OrderLine]
    ship_to: Address
    shipping_method: int = 1
    total: float = 0.0
    currency: str = "USD"
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        # Field-slip guard: a positional mistake (e.g. passing currency where
        # total belongs) must fail loudly HERE, not three modules downstream
        # when analytics tries `revenue += "EUR"`.
        if not isinstance(self.shipping_method, int):
            raise TypeError(
                f"Order.shipping_method must be int, got "
                f"{type(self.shipping_method).__name__} ({self.shipping_method!r}) "
                "— check positional argument order")
        try:
            self.total = float(self.total)
        except (TypeError, ValueError):
            raise TypeError(
                f"Order.total must be numeric, got {self.total!r} "
                "— check positional argument order")
        if not isinstance(self.currency, str):
            raise TypeError(f"Order.currency must be str, got {self.currency!r}")


FulfillmentStatus = Literal["sent", "duplicate", "failed"]


@dataclass
class FulfillmentResult:
    order_id: str
    status: FulfillmentStatus
    provider_order_id: str | None = None
    error: str | None = None
    attempts: int = 1


class ProductProvider(ABC):
    """Supplier side: catalog lookup, product creation, publishing."""

    @abstractmethod
    def list_blueprints(self) -> list[dict]:
        """Available product templates (t-shirt, mug, sticker...)."""

    @abstractmethod
    def upload_artwork(self, design: Design) -> str:
        """Returns an upload/image id."""

    @abstractmethod
    def create_product(self, product: Product, upload_id: str) -> Product:
        """Creates the product draft with variants; fills product.variants."""

    @abstractmethod
    def set_prices(self, product: Product, price_by_variant: dict[str, float]) -> None:
        ...

    @abstractmethod
    def publish(self, product: Product) -> Product:
        """Pushes the draft live to the connected store."""


class Storefront(ABC):
    """Our own store (Shopify / WooCommerce)."""

    @abstractmethod
    def upsert_product(self, product: Product) -> str:
        """Creates/updates the listing; returns store product id."""

    @abstractmethod
    def fetch_orders_since(self, since: datetime) -> list[Order]:
        """Polling fallback for when webhooks are unavailable."""


class Fulfiller(ABC):
    """Sends an order to production."""

    @abstractmethod
    def submit(self, order: Order) -> FulfillmentResult:
        ...


class Notifier(ABC):
    """Owner-facing alerts (Telegram/Discord) and customer email."""

    @abstractmethod
    def notify_owner(self, message: str) -> None:
        ...

    @abstractmethod
    def email_customer(self, to: str, subject: str, body: str) -> None:
        ...


class SocialPoster(ABC):
    """Posts our own product content to our own accounts (via Postiz)."""

    @abstractmethod
    def post(self, text: str, image_urls: list[str], networks: list[str]) -> str:
        """Returns a post id."""
