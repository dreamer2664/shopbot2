"""Regression tests for bugs found by adversarial probing (2026-09-11).

  BUG1: mock-style blueprint id crashed live Printify with raw ValueError
  BUG2: non-numeric variant id crashed set_prices with raw ValueError
  BUG3: order line with empty product/variant id passed validation
  BUG4: currency mismatch between pricing (USD) and order (EUR) went unnoticed
"""
import pytest

from shopbot.pipeline.orders import handle_order, validate_order
from shopbot.providers.base import (Address, Order, OrderLine, PermanentError,
                                    Product)
from shopbot.providers.mock import MockFulfiller, MockNotifier, sample_design
from shopbot.providers.printify import PrintifyProvider
from fakes import FakeResponse, FakeSession


def _provider(session):
    return PrintifyProvider(token="t", shop_id="1", session=session)


def _order(**over):
    base = dict(order_id="o1",
                lines=[OrderLine("111", "222", 1)],
                ship_to=Address("a", "b", "e@x.com", "s", "c", "20121", "IT"),
                total=10.0, currency="USD")
    base.update(over)
    return Order(**base)


# ---- BUG 1 & 2: raw ValueError -> PermanentError ---------------------------

def test_bug1_nonnumeric_blueprint_id_is_permanent_error():
    s = FakeSession(responses=[FakeResponse(200, {"id": 1})])
    prod = Product(design=sample_design(), blueprint_id="bp_tshirt",
                   print_provider_id=29)
    with pytest.raises(PermanentError, match="numeric"):
        _provider(s).create_product(prod, "up1")
    assert s.calls == []          # rejected BEFORE any HTTP call


def test_bug2_nonnumeric_variant_id_is_permanent_error():
    s = FakeSession(responses=[FakeResponse(200, {})])
    prod = Product(design=sample_design(), blueprint_id="b", print_provider_id=1)
    prod.printify_product_id = "5"
    with pytest.raises(PermanentError, match="numeric"):
        _provider(s).set_prices(prod, {"v_abc": 9.99})
    assert s.calls == []


def test_numeric_blueprint_still_works():
    s = FakeSession(responses=[FakeResponse(200, {"id": 7, "variants": []})])
    prod = Product(design=sample_design(), blueprint_id="bp_123",
                   print_provider_id=29)
    out = _provider(s).create_product(prod, "up1")
    assert out.printify_product_id == "7"
    assert s.last()["json"]["blueprint_id"] == 123


# ---- BUG 3: empty ids rejected at validation -------------------------------

@pytest.mark.parametrize("pid,vid,expected", [
    ("", "222", "missing product_id"),
    ("111", "", "missing variant_id"),
    ("  ", "222", "missing product_id"),
    ("111", "222", None),
])
def test_bug3_line_item_ids_validated(pid, vid, expected):
    o = _order(lines=[OrderLine(pid, vid, 1)])
    result = validate_order(o)
    if expected is None:
        assert result is None
    else:
        assert result is not None and expected in result


def test_bug3_empty_id_order_alerts_owner_and_not_sent():
    ful, note = MockFulfiller(), MockNotifier()
    r = handle_order(_order(lines=[OrderLine("111", "", 1)]),
                     fulfiller=ful, notifier=note)
    assert r.status == "failed" and "variant_id" in r.error
    assert ful.submitted == {}
    assert any("rejected" in m for m in note.owner_messages)


# ---- BUG 4: currency mismatch alerts ---------------------------------------

def test_bug4_currency_mismatch_alerts_but_still_fulfills():
    ful, note = MockFulfiller(), MockNotifier()
    r = handle_order(_order(currency="EUR"), fulfiller=ful, notifier=note,
                     expected_currency="USD")
    assert r.status == "sent"     # customer already paid — fulfill regardless
    assert any("pricing" in m and "EUR" in m for m in note.owner_messages)


def test_bug4b_currency_alert_dedupes_across_orders():
    """Training-run lesson: 10k EUR orders must not produce 10k alerts."""
    ful, note = MockFulfiller(), MockNotifier()
    seen: set = set()
    for i in range(100):
        handle_order(_order(order_id=f"o{i}", currency="EUR"),
                     fulfiller=ful, notifier=note,
                     expected_currency="USD", alerts_seen=seen)
    assert ful.submitted.keys().__len__() == 100
    currency_alerts = [m for m in note.owner_messages if "pricing" in m]
    assert len(currency_alerts) == 1     # told once, as promised in the message


def test_bug4c_different_currency_pairs_alert_separately():
    ful, note = MockFulfiller(), MockNotifier()
    seen: set = set()
    handle_order(_order(order_id="a", currency="EUR"), fulfiller=ful,
                 notifier=note, expected_currency="USD", alerts_seen=seen)
    handle_order(_order(order_id="b", currency="GBP"), fulfiller=ful,
                 notifier=note, expected_currency="USD", alerts_seen=seen)
    handle_order(_order(order_id="c", currency="EUR"), fulfiller=ful,
                 notifier=note, expected_currency="USD", alerts_seen=seen)
    assert len([m for m in note.owner_messages if "pricing" in m]) == 2


def test_bug4_matching_currency_is_silent():
    ful, note = MockFulfiller(), MockNotifier()
    handle_order(_order(currency="USD"), fulfiller=ful, notifier=note,
                 expected_currency="USD")
    assert not any("currency" in m for m in note.owner_messages)


def test_bug4_no_expected_currency_no_alert():
    ful, note = MockFulfiller(), MockNotifier()
    handle_order(_order(currency="EUR"), fulfiller=ful, notifier=note)
    assert not any("currency" in m for m in note.owner_messages)
