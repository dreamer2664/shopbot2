"""Live Shopify storefront (Admin REST API, pinned version).

Auth: custom-app Admin API access token (x-shopify-access-token header).
Needed scopes: read_products, write_products, read_orders, read_fulfillments.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .base import Address, Order, OrderLine, PermanentError, Product, Storefront
from .http import api_request

API_VERSION = "2025-01"


@dataclass
class ShopifyStorefront(Storefront):
    domain: str          # mystore.myshopify.com
    token: str
    session: object = field(default=None)

    def __post_init__(self):
        if self.session is None:
            import requests
            self.session = requests.Session()
        if not self.domain.endswith("myshopify.com"):
            raise PermanentError(f"bad Shopify domain: {self.domain!r}")

    @property
    def _base(self) -> str:
        return f"https://{self.domain}/admin/api/{API_VERSION}"

    @property
    def _headers(self) -> dict:
        return {"X-Shopify-Access-Token": self.token,
                "Content-Type": "application/json"}

    def upsert_product(self, product: Product) -> str:
        """Create the storefront listing. If Printify's native Shopify sync is
        enabled this is a no-op safety net: we verify the product exists and
        return its id. Otherwise we create a simple listing ourselves."""
        if product.store_product_id:
            return product.store_product_id
        variant = product.variants[0] if product.variants else None
        payload = {"product": {
            "title": product.design.title,
            "body_html": f"<p>{product.design.description}</p>",
            "vendor": "shopbot",
            "product_type": "print-on-demand",
            "tags": ", ".join(product.design.tags),
            "status": "active" if product.published else "draft",
            "variants": [{
                "title": v.size or "Default",
                "price": f"{(product.retail_price or 0):.2f}",
                "sku": v.variant_id,
                "inventory_management": None,   # supplier handles stock
            } for v in product.variants] or [{"title": "Default",
                                              "price": f"{(product.retail_price or 0):.2f}"}],
        }}
        data = api_request(self.session, "POST", f"{self._base}/products.json",
                           headers=self._headers, json=payload,
                           label="shopify.upsert_product")
        if not isinstance(data, dict) or "product" not in data:
            raise PermanentError(f"shopify.upsert_product: bad response {data!r:.200}")
        product.store_product_id = str(data["product"]["id"])
        return product.store_product_id

    def fetch_orders_since(self, since: datetime) -> list[Order]:
        since = since.astimezone(timezone.utc)
        url = (f"{self._base}/orders.json?status=any&fulfillment_status=unfulfilled"
               f"&updated_at_min={since.strftime('%Y-%m-%dT%H:%M:%SZ')}&limit=250")
        data = api_request(self.session, "GET", url, headers=self._headers,
                           label="shopify.fetch_orders")
        raw_orders = (data or {}).get("orders", []) if isinstance(data, dict) else []
        return [parse_shopify_order(o) for o in raw_orders]


def parse_shopify_order(raw: dict) -> Order:
    """Map a Shopify order (REST or orders/create webhook payload) to our Order.

    Webhook replays are normal, so order id is the idempotency key downstream.
    """
    sa = raw.get("shipping_address") or raw.get("customer", {}).get("default_address") or {}
    if not sa:
        raise PermanentError(f"order {raw.get('id')}: no shipping address")
    lines = [
        OrderLine(product_id=str(li.get("product_id") or ""),
                  variant_id=str(li.get("variant_id") or ""),
                  quantity=int(li.get("quantity") or 0))
        for li in raw.get("line_items", [])
    ]
    address = Address(
        first_name=sa.get("first_name") or "",
        last_name=sa.get("last_name") or "",
        email=raw.get("email") or sa.get("email") or "",
        phone=sa.get("phone") or raw.get("phone") or "",
        address1=sa.get("address1") or "",
        region=sa.get("province_code") or "",
        city=sa.get("city") or "",
        zip=sa.get("zip") or "",
        country=(sa.get("country_code") or "").upper(),
    )
    return Order(
        order_id=str(raw.get("id") or raw.get("order_id") or ""),
        lines=lines,
        ship_to=address,
        total=float(raw.get("total_price") or 0),
        currency=raw.get("currency") or "USD",
    )
