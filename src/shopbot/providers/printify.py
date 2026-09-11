"""Live Printify provider (ProductProvider + Fulfiller).

API docs: developers.printify.com. Free on all plans, Bearer-token auth.
No sandbox exists — for live testing use a cheap sticker product with
auto-send-to-production disabled, then cancel before it prints.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .base import (
    Address, FulfillmentResult, Order, PermanentError, Product,
    ProductProvider, Variant,
)
from .http import api_request

PROD_BASE = "https://api.printify.com"


@dataclass
class PrintifyProvider(ProductProvider):
    token: str
    shop_id: str
    session: object = field(default=None)  # injectable for tests
    base_url: str = ""  # override for the integration simulator; "" = production

    def __post_init__(self):
        if self.session is None:
            import requests
            self.session = requests.Session()

    @property
    def _root(self) -> str:
        return f"{(self.base_url or PROD_BASE).rstrip('/')}/v1"

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"}

    def _shop(self, path: str) -> str:
        return f"{self._root}/shops/{self.shop_id}{path}"

    # ---- ProductProvider -------------------------------------------------

    def list_blueprints(self) -> list[dict]:
        data = api_request(self.session, "GET", f"{self._root}/catalog/blueprints.json",
                           headers=self._headers, label="printify.blueprints")
        return data if isinstance(data, list) else []

    def upload_artwork(self, design) -> str:
        """Upload by URL. For local files, pass a data: URL or host the image
        (Printify accepts any public URL; base64 payloads are also supported)."""
        payload = {"file_name": f"{design.slug}.png", "url": design.image_path}
        data = api_request(self.session, "POST", self._shop("/images.json"),
                           headers=self._headers, json=payload,
                           label="printify.upload")
        upload_id = str(data["id"]) if isinstance(data, dict) else str(data)
        if not upload_id:
            raise PermanentError("printify.upload: no id in response")
        return upload_id

    def create_product(self, product: Product, upload_id: str) -> Product:
        try:
            # Real Printify blueprint ids are numeric; tolerate "bp_123" style.
            blueprint_id = int(str(product.blueprint_id).split("_")[-1])
        except ValueError:
            raise PermanentError(
                f"blueprint_id must be numeric (use an id from list_blueprints()), "
                f"got {product.blueprint_id!r}")
        payload = {
            "blueprint_id": blueprint_id,
            "print_provider_id": product.print_provider_id,
            "title": product.design.title,
            "description": product.design.description,
            "tags": product.design.tags,
            "images": [{"id": upload_id, "position": "front"}],
            # start with all variants disabled+unpriced; set_prices enables them
        }
        data = api_request(self.session, "POST", self._shop("/products.json"),
                           headers=self._headers, json=payload,
                           label="printify.create_product")
        if not isinstance(data, dict) or "id" not in data:
            raise PermanentError(
                f"printify.create_product: bad response {str(data)[:200]}")
        product.printify_product_id = str(data["id"])
        if data.get("variants"):
            product.variants = [_to_variant(v) for v in data["variants"]]
        return product

    def set_prices(self, product: Product, price_by_variant: dict[str, float]) -> None:
        if not product.printify_product_id:
            raise PermanentError("set_prices: product not created yet")
        try:
            variants = [
                {"id": int(vid), "price": int(round(price * 100)),
                 "is_enabled": True}
                for vid, price in price_by_variant.items()
            ]
        except ValueError:
            raise PermanentError(
                f"variant ids must be the numeric ids Printify returned in "
                f"create_product, got {list(price_by_variant)!r}")
        api_request(self.session, "PUT",
                    self._shop(f"/products/{product.printify_product_id}.json"),
                    headers=self._headers, json={"variants": variants},
                    label="printify.set_prices")

    def publish(self, product: Product) -> Product:
        if not product.printify_product_id:
            raise PermanentError("publish: product not created yet")
        api_request(self.session, "POST",
                    self._shop(f"/products/{product.printify_product_id}/publish.json"),
                    headers=self._headers, json={}, label="printify.publish")
        product.published = True
        return product

    # ---- Fulfiller --------------------------------------------------------

    def submit(self, order: Order) -> FulfillmentResult:
        """Create a Printify order and send it straight to production."""
        payload = {
            "external_id": order.order_id,   # Printify dedupes on this too
            "line_items": [
                {"product_id": ln.product_id, "variant_id": int(ln.variant_id),
                 "quantity": ln.quantity}
                for ln in order.lines
            ],
            "shipping_method": order.shipping_method,
            "send_shipping_notification": True,
            "address_to": _address_payload(order.ship_to),
        }
        data = api_request(self.session, "POST", self._shop("/orders.json"),
                           headers=self._headers, json=payload,
                           label="printify.submit_order")
        if not isinstance(data, dict) or "id" not in data:
            raise PermanentError(
                f"printify.submit_order: bad response {str(data)[:200]}")
        return FulfillmentResult(order.order_id, "sent", str(data["id"]))


def _to_variant(raw: dict) -> Variant:
    """Map a Printify variant onto our model.

    Printify returns variant ids as integers and costs in CENTS. Both must be
    normalised here or pricing will be off by 100x.
    """
    options = raw.get("options")
    options = options if isinstance(options, dict) else {}
    size = raw.get("title") or options.get("size") or ""
    return Variant(
        variant_id=str(raw["id"]),
        size=str(size),
        color=str(options.get("color") or ""),
        cost=round(float(raw.get("cost", 0)) / 100.0, 2),
    )


def _address_payload(a: Address) -> dict:
    return {
        "first_name": a.first_name, "last_name": a.last_name,
        "email": a.email, "phone": a.phone or "",
        "country": a.country, "region": a.region or "",
        "address1": a.address1, "address2": "",
        "city": a.city, "zip": a.zip,
    }
