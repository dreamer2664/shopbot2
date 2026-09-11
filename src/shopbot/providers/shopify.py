"""Live Shopify storefront — GraphQL Admin API.

WHY GraphQL, not REST: Shopify deprecated the entire REST Admin API
(Oct 2024) and organizations created after April 1 2025 can only create
custom apps with GraphQL. A brand-new store = brand-new org = GraphQL only.
Source: shopify.dev changelog + community.shopify.com/c/technical-q-a/deprecating-rest-api

Auth: custom-app Admin API access token (X-Shopify-Access-Token header).
Scopes: read_products, write_products, read_orders, read_fulfillments.
Endpoint: POST https://{domain}/admin/api/{version}/graphql.json

Note: `orders/create` WEBHOOK payloads keep the classic REST-style JSON
shape, so parse_shopify_order() below handles webhook bodies, while
fetch_orders_since() maps GraphQL order nodes to the same Order model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .base import Address, Order, OrderLine, PermanentError, Product, Storefront
from .http import api_request

API_VERSION = "2025-01"

PRODUCT_SET_MUTATION = """
mutation productSet($input: ProductSetInput!) {
  productSet(synchronous: true, input: $input) {
    product { id title status variants(first: 100) { nodes { id } } }
    userErrors { field message }
  }
}
"""

ORDERS_QUERY = """
query unfulfilledOrders($q: String!) {
  orders(query: $q, first: 100, sortKey: UPDATED_AT) {
    nodes {
      id name totalPrice currencyCode email phone
      shippingAddress {
        firstName lastName address1 city zip countryCode provinceCode phone
      }
      lineItems(first: 50) {
        nodes { quantity product { id } variant { id } }
      }
    }
  }
}
"""


def _gid_id(gid: str | None) -> str:
    """'gid://shopify/Order/123' -> '123' (None-safe)."""
    if not gid:
        return ""
    return str(gid).rsplit("/", 1)[-1]


@dataclass
class ShopifyStorefront(Storefront):
    domain: str          # mystore.myshopify.com
    token: str
    session: object = field(default=None)
    base_url: str = ""   # override for the integration simulator; "" = derive from domain

    def __post_init__(self):
        if self.session is None:
            import requests
            self.session = requests.Session()
        if not self.base_url and not self.domain.endswith("myshopify.com"):
            raise PermanentError(f"bad Shopify domain: {self.domain!r}")

    @property
    def _endpoint(self) -> str:
        base = self.base_url or f"https://{self.domain}"
        return f"{base.rstrip('/')}/admin/api/{API_VERSION}/graphql.json"

    @property
    def _headers(self) -> dict:
        return {"X-Shopify-Access-Token": self.token,
                "Content-Type": "application/json"}

    def _gql(self, query: str, variables: dict, label: str) -> dict:
        data = api_request(self.session, "POST", self._endpoint,
                           headers=self._headers,
                           json={"query": query, "variables": variables},
                           label=label)
        if not isinstance(data, dict):
            raise PermanentError(f"{label}: unexpected response {str(data)[:200]}")
        if data.get("errors"):
            raise PermanentError(
                f"{label}: graphql errors {str(data['errors'])[:300]}")
        return data.get("data") or {}

    # ---- Storefront -------------------------------------------------------

    def upsert_product(self, product: Product) -> str:
        """Create/publish the listing via productSet (new product model).

        If Printify's native Shopify sync already created the listing, this
        acts as a safety net keyed on the store product id when known.
        """
        if product.store_product_id:
            return product.store_product_id

        # One option ("Title") listing every variant; images come from the
        # design URL so the listing shows something even before Printify sync.
        option_values = []
        variants = []
        seen = set()
        for v in product.variants:
            name = (v.size or v.color or "Default").strip() or "Default"
            # productSet rejects duplicate option value names
            base, i = name, 2
            while name in seen:
                name = f"{base} ({i})"
                i += 1
            seen.add(name)
            option_values.append({"name": name})
            variants.append({
                "optionValues": [{"optionName": "Title", "name": name}],
                "price": f"{(product.retail_price or 0):.2f}",
            })
        if not option_values:
            option_values = [{"name": "Default"}]
            variants = [{"optionValues": [{"optionName": "Title",
                                           "name": "Default"}],
                         "price": f"{(product.retail_price or 0):.2f}"}]

        input_payload = {
            "title": product.design.title[:255],
            "descriptionHtml": f"<p>{product.design.description}</p>",
            "vendor": "shopbot",
            "productType": "print-on-demand",
            "tags": product.design.tags[:20],
            "status": "ACTIVE" if product.published else "DRAFT",
            "productOptions": [{"name": "Title", "values": option_values}],
            "variants": variants,
        }
        if product.design.image_path.startswith("http"):
            input_payload["files"] = [{
                "originalSource": product.design.image_path,
                "contentType": "MEDIA_IMAGE",
            }]

        data = self._gql(PRODUCT_SET_MUTATION, {"input": input_payload},
                         "shopify.productSet")
        result = data.get("productSet") or {}
        user_errors = result.get("userErrors") or []
        if user_errors:
            raise PermanentError(
                f"shopify.productSet userErrors: {str(user_errors)[:300]}")
        node = result.get("product") or {}
        if not node.get("id"):
            raise PermanentError(
                f"shopify.productSet: no product id in {str(result)[:200]}")
        product.store_product_id = _gid_id(node["id"])
        # Capture store variant ids (same order as our variants list) so the
        # catalog can translate incoming store orders to supplier ids.
        nodes = ((node.get("variants") or {}).get("nodes") or [])
        product.store_variant_ids = [_gid_id(n.get("id")) for n in nodes]
        return product.store_product_id

    def fetch_orders_since(self, since: datetime) -> list[Order]:
        since = since.astimezone(timezone.utc)
        q = (f"updated_at:>='{since.strftime('%Y-%m-%dT%H:%M:%SZ')}' "
             f"fulfillment_status:unfulfilled status:open")
        data = self._gql(ORDERS_QUERY, {"q": q}, "shopify.orders")
        nodes = (data.get("orders") or {}).get("nodes") or []
        return [_graphql_order_to_model(n) for n in nodes]


def _graphql_order_to_model(node: dict) -> Order:
    sa = node.get("shippingAddress")
    if not sa:
        raise PermanentError(f"order {node.get('id')}: no shipping address")
    lines = [
        OrderLine(product_id=_gid_id((li.get("product") or {}).get("id")),
                  variant_id=_gid_id((li.get("variant") or {}).get("id")),
                  quantity=int(li.get("quantity") or 0))
        for li in (node.get("lineItems") or {}).get("nodes", [])
    ]
    address = Address(
        first_name=sa.get("firstName") or "",
        last_name=sa.get("lastName") or "",
        email=node.get("email") or "",
        phone=sa.get("phone") or node.get("phone") or "",
        address1=sa.get("address1") or "",
        region=sa.get("provinceCode") or "",
        city=sa.get("city") or "",
        zip=sa.get("zip") or "",
        country=(sa.get("countryCode") or "").upper(),
    )
    return Order(
        order_id=_gid_id(node.get("id")),
        lines=lines,
        ship_to=address,
        total=float(node.get("totalPrice") or 0),
        currency=node.get("currencyCode") or "USD",
    )


def parse_shopify_order(raw: dict) -> Order:
    """Map a Shopify orders/create WEBHOOK payload (classic JSON shape).

    Webhook replays are normal, so order id is the idempotency key downstream.
    """
    sa = raw.get("shipping_address") or (raw.get("customer") or {}).get("default_address") or {}
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
