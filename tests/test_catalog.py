"""Catalog tests: store<->supplier id translation (found by live integration).

Shopify orders reference Shopify product/variant ids. Printify only accepts its
own ids. The catalog is written at launch and consulted at fulfillment; an
unmapped order must be rejected with a clear reason, never sent through.
"""
from datetime import datetime, timezone

import pytest

from shopbot.pipeline.catalog import Catalog, UnmappedOrderError
from shopbot.pipeline.orders import process_orders
from shopbot.providers.base import (Address, Order, OrderLine, Product,
                                    Variant)
from shopbot.providers.mock import MockFulfiller, MockNotifier, sample_design


def _product(store_pid="8100", pf_pid="4002",
             store_vids=("9001", "9002"), pf_vids=("400201", "400202"),
             costs=(8.1, 8.35)):
    p = Product(design=sample_design(), blueprint_id="6", print_provider_id=29)
    p.printify_product_id = pf_pid
    p.store_product_id = store_pid
    p.store_variant_ids = list(store_vids)
    p.variants = [Variant(pf_vids[i], "M", "black", costs[i])
                  for i in range(len(pf_vids))]
    p.retail_price = 24.99
    return p


def _order(pid, vid, qty=1, oid="o1"):
    return Order(oid, [OrderLine(pid, vid, qty)],
                 Address("a", "b", "e", "s", "c", "20121", "IT"),
                 total=24.99, currency="EUR")


def test_record_and_translate(tmp_path):
    cat = Catalog(str(tmp_path / "catalog.json"))
    p = _product()
    cat.record(p, {"400201": 24.99, "400202": 25.99})

    translated, cost = cat.translate(_order("8100", "9001", qty=2))
    assert translated.order_id == "o1"                     # id preserved
    assert translated.lines[0].product_id == "4002"        # store -> supplier
    assert translated.lines[0].variant_id == "400201"
    assert translated.lines[0].quantity == 2
    assert cost == pytest.approx(16.2)                     # 2 x 8.1
    assert translated.total == 24.99 and translated.currency == "EUR"


def test_catalog_survives_restart(tmp_path):
    path = str(tmp_path / "catalog.json")
    Catalog(path).record(_product(), {})
    fresh = Catalog(path)                                  # new process
    translated, _ = fresh.translate(_order("8100", "9001"))
    assert translated.lines[0].product_id == "4002"


def test_multiple_products(tmp_path):
    cat = Catalog(str(tmp_path / "c.json"))
    cat.record(_product("A", "PA", ("av1",), ("pv1",), (5.0,)), {})
    cat.record(_product("B", "PB", ("bv1",), ("pv2",), (7.0,)), {})
    assert cat.translate(_order("A", "av1"))[0].lines[0].product_id == "PA"
    assert cat.translate(_order("B", "bv1"))[0].lines[0].product_id == "PB"


def test_unknown_product_raises(tmp_path):
    cat = Catalog(str(tmp_path / "c.json"))
    cat.record(_product(), {})
    with pytest.raises(UnmappedOrderError, match="not in catalog"):
        cat.translate(_order("9999", "9001"))


def test_unknown_variant_raises(tmp_path):
    cat = Catalog(str(tmp_path / "c.json"))
    cat.record(_product(), {})
    with pytest.raises(UnmappedOrderError, match="variant"):
        cat.translate(_order("8100", "7777"))


def test_missing_variants_due_to_misalignment_is_unmapped(tmp_path):
    """If Shopify returned fewer variant ids than we have variants, the
    un-covered variant must be treated as unmapped rather than silently
    fulfilled with a wrong id."""
    cat = Catalog(str(tmp_path / "c.json"))
    p = _product(store_vids=("9001",))          # only one id captured
    cat.record(p, {})
    assert cat.lookup("8100")["variants"].keys() == {"9001"}
    with pytest.raises(UnmappedOrderError):
        cat.translate(_order("8100", "9002"))


def test_record_skips_incomplete_product(tmp_path):
    cat = Catalog(str(tmp_path / "c.json"))
    p = _product()
    p.store_product_id = None                    # never listed
    cat.record(p, {})
    assert cat.lookup("8100") is None


def test_corrupt_catalog_falls_back_to_empty(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{corrupt")
    cat = Catalog(str(path))
    assert cat.lookup("8100") is None
    with pytest.raises(UnmappedOrderError):
        cat.translate(_order("8100", "9001"))


def test_process_orders_translates_and_fulfils(tmp_path):
    cat = Catalog(str(tmp_path / "c.json"))
    cat.record(_product(), {})
    ful, note = MockFulfiller(), MockNotifier()

    class Store:
        def fetch_orders_since(self, since):
            return [_order("8100", "9001", qty=1)]

    batch = process_orders(store=Store(), fulfiller=ful, notifier=note,
                           catalog=cat)
    assert batch.sent == 1
    submitted = ful.submitted["o1"]
    assert submitted.lines[0].product_id == "4002"     # supplier ids used
    assert batch.results[0].cost == pytest.approx(8.1)


def test_process_orders_rejects_unmapped_with_alert(tmp_path):
    cat = Catalog(str(tmp_path / "c.json"))            # empty catalog
    ful, note = MockFulfiller(), MockNotifier()

    class Store:
        def fetch_orders_since(self, since):
            return [_order("8100", "9001", oid="ghost")]

    batch = process_orders(store=Store(), fulfiller=ful, notifier=note,
                           catalog=cat)
    assert batch.failed == 1 and batch.sent == 0
    assert ful.submitted == {}                          # never reached supplier
    assert any("NOT fulfilled" in m for m in note.owner_messages)
