"""Analytics tests: ledger -> business report -> alerts."""
from datetime import datetime, timedelta, timezone

from shopbot.pipeline.analytics import best_sellers, build_report
from shopbot.pipeline.ledger import Ledger
from shopbot.providers.base import Address, FulfillmentResult, Order, OrderLine


def _order(oid, total, currency="EUR"):
    return Order(oid, [OrderLine("p1", "v1", 1)],
                 Address("a", "b", "e", "s", "c", "z", "IT"),
                 total=total, currency=currency)


def _sent(oid):
    return FulfillmentResult(oid, "sent", f"ful_{oid}")


def test_report_aggregates(tmp_path):
    led = Ledger(str(tmp_path / "l.jsonl"))
    led.record_launch("tee-a", True)
    led.record_launch("tee-b", False, error="ip rejected")
    led.record_fulfillment(_sent("o1"), _order("o1", 25.0), cost=9.0, product_id="p1")
    led.record_fulfillment(_sent("o2"), _order("o2", 25.0), cost=9.0, product_id="p1")
    led.record_fulfillment(FulfillmentResult("o3", "duplicate"), _order("o3", 25.0))
    led.record_fulfillment(FulfillmentResult("o4", "failed", error="zip"), _order("o4", 25.0))

    rep = build_report(led)
    assert rep.launches_ok == 1 and rep.launches_failed == 1
    assert rep.orders_sent == 2 and rep.orders_duplicate == 1 and rep.orders_failed == 1
    assert rep.revenue == 50.0 and rep.cost == 18.0 and rep.profit == 32.0
    assert rep.margin is not None and abs(rep.margin - 0.64) < 0.01
    assert rep.failure_rate == 1 / 3
    st = rep.per_product["p1"]
    assert st.units == 2 and st.profit == 32.0


def test_alert_below_survival_floor(tmp_path):
    led = Ledger(str(tmp_path / "l.jsonl"))
    # revenue 20, cost 18.4 -> margin 5% — way below the 20% floor [A][C]
    led.record_fulfillment(_sent("o1"), _order("o1", 20.0), cost=18.4, product_id="p")
    rep = build_report(led)
    assert any("survival floor" in a for a in rep.alerts)


def test_no_alerts_on_healthy_business(tmp_path):
    led = Ledger(str(tmp_path / "l.jsonl"))
    led.record_fulfillment(_sent("o1"), _order("o1", 26.0), cost=9.0, product_id="p")
    rep = build_report(led)
    assert rep.alerts == []


def test_high_failure_rate_alert_needs_volume(tmp_path):
    led = Ledger(str(tmp_path / "l.jsonl"))
    for i in range(12):
        led.record_fulfillment(_sent(f"s{i}"), _order(f"s{i}", 25.0), cost=9.0, product_id="p")
    for i in range(4):
        led.record_fulfillment(FulfillmentResult(f"f{i}", "failed", error="x"),
                               _order(f"f{i}", 25.0))
    rep = build_report(led)
    assert any("failure rate" in a for a in rep.alerts)


def test_per_product_reprice_alert(tmp_path):
    led = Ledger(str(tmp_path / "l.jsonl"))
    for i in range(10):
        # one bad product: sells but barely breaks even
        led.record_fulfillment(_sent(f"o{i}"), _order(f"o{i}", 20.0),
                               cost=18.0, product_id="loser")
        led.record_fulfillment(_sent(f"g{i}"), _order(f"g{i}", 26.0),
                               cost=9.0, product_id="winner")
    rep = build_report(led)
    assert any("loser" in a and "reprice" in a for a in rep.alerts)
    assert not any("winner" in a for a in rep.alerts)
    assert best_sellers(rep, 1)[0][0] in ("loser", "winner")


def test_empty_ledger_report(tmp_path):
    rep = build_report(Ledger(str(tmp_path / "nope.jsonl")))
    assert rep.orders_sent == 0 and rep.margin is None and rep.alerts == []
    assert rep.failure_rate == 0.0
