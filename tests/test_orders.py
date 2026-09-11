"""Order fulfillment tests: idempotency, validation, retries, alerts."""
from shopbot.config import Config
from shopbot.pipeline.orders import handle_order, process_orders, validate_order
from shopbot.pipeline.products import launch_product
from shopbot.providers.base import Address, Order, OrderLine, PermanentError
from shopbot.providers.mock import MockProviders, sample_design


def _live_order(p, **kw):
    res = launch_product(sample_design(), "bp_tshirt", supplier=p.supplier,
                         store=p.store, cfg=Config.mock())
    assert res.success
    order = p.store.add_order(res.product, **kw)
    p.store.pending_orders.append(order)
    return order


def _addr(**over):
    base = dict(first_name="M", last_name="R", email="m@e.com",
                address1="Via 1", city="Milan", zip="20121", country="IT")
    base.update(over)
    return Address(**base)


def test_validate_order_rules():
    ok = Order("o1", [OrderLine("p", "v", 1)], _addr())
    assert validate_order(ok) is None
    assert validate_order(Order("", [OrderLine("p", "v", 1)], _addr())) == "missing order_id"
    assert validate_order(Order("o1", [], _addr())) == "no line items"
    assert validate_order(Order("o1", [OrderLine("p", "v", 0)], _addr())) == "non-positive quantity"
    assert validate_order(Order("o1", [OrderLine("p", "v", 1)], _addr(zip=""))) == "ship_to.zip missing"
    assert validate_order(Order("o1", [OrderLine("p", "v", 1)], _addr(country="ITA"))) == \
        "ship_to.country must be ISO-3166 alpha-2"


def test_order_sent_to_production_and_owner_alerted():
    p = MockProviders.build()
    order = _live_order(p, qty=2)
    r = handle_order(order, fulfiller=p.fulfiller, notifier=p.notifier,
                     notify_on_sent=True)
    assert r.status == "sent"
    assert r.provider_order_id
    assert order.order_id in p.fulfiller.submitted
    assert any("sent to production" in m for m in p.notifier.owner_messages)


def test_successful_sales_do_not_page_owner_by_default():
    """Alert-fatigue lesson: 10k sales must not mean 10k Telegram pings.

    Successful fulfillment is silent by default — the daily `report` covers it.
    Only problems (rejections, exhausted retries, currency drift) notify.
    """
    p = MockProviders.build()
    for i in range(25):
        order = _live_order(p, order_id=f"sale_{i}")
        handle_order(order, fulfiller=p.fulfiller, notifier=p.notifier,
                     expected_currency="USD")
    assert len(p.fulfiller.submitted) == 25
    assert p.notifier.owner_messages == []


def test_duplicate_webhook_replay_never_double_prints():
    p = MockProviders.build()
    order = _live_order(p)
    r1 = handle_order(order, fulfiller=p.fulfiller, notifier=p.notifier)
    r2 = handle_order(order, fulfiller=p.fulfiller, notifier=p.notifier)  # replay
    assert r1.status == "sent"
    assert r2.status == "duplicate"
    assert len(p.fulfiller.submitted) == 1
    assert sum(1 for c in p.fulfiller.calls if c == f"submit:{order.order_id}") == 1


def test_invalid_address_rejected_with_alert_not_sent():
    p = MockProviders.build()
    order = _live_order(p)
    order.ship_to.zip = ""
    r = handle_order(order, fulfiller=p.fulfiller, notifier=p.notifier)
    assert r.status == "failed"
    assert "zip" in r.error
    assert p.fulfiller.submitted == {}
    assert any("rejected" in m for m in p.notifier.owner_messages)


def test_transient_fulfiller_outage_retries_then_succeeds():
    p = MockProviders.build()
    order = _live_order(p)
    p.fulfiller.fail_times = 2
    r = handle_order(order, fulfiller=p.fulfiller, notifier=p.notifier)
    assert r.status == "sent"
    assert r.attempts == 1  # retry() absorbed the failures internally


def test_persistent_outage_escalates_to_owner():
    p = MockProviders.build()
    order = _live_order(p)
    p.fulfiller.fail_times = 99
    r = handle_order(order, fulfiller=p.fulfiller, notifier=p.notifier, attempts=3)
    assert r.status == "failed"
    assert r.attempts == 3
    assert any("Manual action needed" in m for m in p.notifier.owner_messages)
    assert p.fulfiller.submitted == {}


def test_permanent_supplier_rejection_not_retried():
    p = MockProviders.build()
    order = _live_order(p)
    p.fulfiller.fail_times = 1
    p.fulfiller.fail_with = PermanentError
    r = handle_order(order, fulfiller=p.fulfiller, notifier=p.notifier)
    assert r.status == "failed"
    # PermanentError must escape immediately: exactly one submit call
    assert p.fulfiller.calls.count(f"submit:{order.order_id}") == 1
    assert any("rejected by supplier" in m for m in p.notifier.owner_messages)


def test_process_orders_batch_mixed_outcomes():
    p = MockProviders.build()
    good1 = _live_order(p)                      # already in pending_orders
    good2 = _live_order(p)                      # already in pending_orders
    dup = Order(good1.order_id, list(good1.lines), _addr())   # replayed webhook
    bad = Order("bad_1", [], _addr())           # invalid: no line items
    p.store.pending_orders = [good1, good2, dup, bad]

    batch = process_orders(store=p.store, fulfiller=p.fulfiller,
                           notifier=p.notifier)
    assert batch.sent == 2          # good1, good2
    assert batch.duplicates == 1    # dup
    assert batch.failed == 1        # bad
    assert len(p.fulfiller.submitted) == 2
