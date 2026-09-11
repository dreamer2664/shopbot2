"""Regression: the field-slip bug found by the year-long training run.

`Order(id, lines, addr, total, currency)` silently wrote the currency string
into `total` (4th positional is `shipping_method`), producing 279 corrupt
ledger rows whose totals were "EUR". Analytics then crashed on
`revenue += "EUR"`. Order now validates its own field types at construction.
"""
import pytest

from shopbot.providers.base import Address, Order, OrderLine


def _addr():
    return Address("a", "b", "e", "s", "c", "20121", "IT")


def test_positional_fieldslip_now_raises():
    with pytest.raises(TypeError, match="shipping_method must be int"):
        Order("o1", [OrderLine("p", "v", 1)], _addr(), 25.0, "EUR")


def test_total_must_be_numeric():
    with pytest.raises(TypeError, match="total must be numeric"):
        Order("o1", [OrderLine("p", "v", 1)], _addr(), 1, "EUR", "USD")


def test_currency_must_be_string():
    with pytest.raises(TypeError, match="currency must be str"):
        Order("o1", [OrderLine("p", "v", 1)], _addr(), 1, 25.0, 978)


def test_numeric_strings_are_coerced():
    o = Order("o1", [OrderLine("p", "v", 1)], _addr(), total="25.50")
    assert o.total == 25.5 and isinstance(o.total, float)


def test_correct_construction_unaffected():
    o = Order("o1", [OrderLine("p", "v", 1)], _addr(), shipping_method=2,
              total=25.0, currency="EUR")
    assert o.total == 25.0 and o.currency == "EUR" and o.shipping_method == 2
