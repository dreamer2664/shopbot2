"""Persistent mapping between STORE ids and SUPPLIER ids.

Why this module exists (found by the live integration simulator):
a Shopify order references Shopify product/variant ids, but Printify only
accepts its own ids. Without translation, every real order fails with a 400.
The catalog is written at launch time (when the bot knows both id spaces)
and consulted at fulfillment time.

File format (data/catalog.json):
{
  "by_store_product": {
    "<store_product_id>": {
      "slug": "...", "printify_product_id": "...",
      "variants": {"<store_variant_id>": {"supplier_variant_id": "...",
                                          "cost": 8.1, "price": 24.99}}
    }
  }
}
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass

from ..providers.base import Order, OrderLine, Product


class UnmappedOrderError(Exception):
    """Order references a listing this bot never launched (or a deleted one)."""


@dataclass
class Catalog:
    path: str = "data/catalog.json"

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"by_store_product": {}}

    def _save(self, data: dict) -> None:
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d or ".")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp, self.path)      # atomic on POSIX and Windows
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def record(self, product: Product, prices: dict[str, float] | None = None) -> None:
        """Called after a successful launch: persist both id spaces.

        product.store_variant_ids[i] corresponds to product.variants[i].
        """
        if not product.store_product_id or not product.printify_product_id:
            return
        prices = prices or {}
        data = self._load()
        variants = {}
        for i, v in enumerate(product.variants):
            store_vid = (product.store_variant_ids[i]
                         if i < len(product.store_variant_ids) else None)
            if not store_vid:
                continue
            variants[str(store_vid)] = {
                "supplier_variant_id": v.variant_id,
                "cost": v.cost,
                "price": prices.get(v.variant_id),
            }
        data["by_store_product"][str(product.store_product_id)] = {
            "slug": product.design.slug,
            "printify_product_id": product.printify_product_id,
            "variants": variants,
        }
        self._save(data)

    def lookup(self, store_product_id: str) -> dict | None:
        return self._load()["by_store_product"].get(str(store_product_id))

    def translate(self, order: Order) -> tuple[Order, float]:
        """Store ids -> supplier ids. Returns (translated_order, total_cost).

        Raises UnmappedOrderError if any line references an unknown listing
        or variant — those orders must NOT reach the supplier (they'd fail
        with a cryptic 400); they get alerted to the owner instead.
        """
        data = self._load()["by_store_product"]
        new_lines = []
        cost = 0.0
        for ln in order.lines:
            entry = data.get(str(ln.product_id))
            if entry is None:
                raise UnmappedOrderError(
                    f"store product {ln.product_id} not in catalog "
                    f"(launched outside the bot, or catalog lost?)")
            vmap = entry["variants"].get(str(ln.variant_id))
            if vmap is None:
                raise UnmappedOrderError(
                    f"store variant {ln.variant_id} of product {ln.product_id} "
                    f"not in catalog")
            new_lines.append(OrderLine(
                product_id=entry["printify_product_id"],
                variant_id=vmap["supplier_variant_id"],
                quantity=ln.quantity))
            cost += (vmap.get("cost") or 0.0) * ln.quantity
        translated = Order(order_id=order.order_id, lines=new_lines,
                           ship_to=order.ship_to,
                           shipping_method=order.shipping_method,
                           total=order.total, currency=order.currency,
                           received_at=order.received_at)
        return translated, round(cost, 2)
