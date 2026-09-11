"""Control-bot command tests (pure: no HTTP) + decisions brain tests."""
import pytest

from shopbot.brain.decisions import decide, format_advice
from shopbot.config import Config
from shopbot.control import ControlBot, PAUSE_FLAG
from shopbot.pipeline.analytics import ProductStats, build_report
from shopbot.pipeline.ledger import Ledger
from shopbot.providers.base import FulfillmentResult, Order, OrderLine, Address
from shopbot.providers.factory import Providers
from shopbot.providers.mock import MockProviders


def _order(oid, total=24.99, currency="EUR"):
    return Order(oid, [OrderLine("p", "v", 1)],
                 Address("a", "b", "e", "s", "c", "z", "IT"),
                 total=total, currency=currency)


@pytest.fixture()
def bot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mocks = MockProviders.build()
    providers = Providers(supplier=mocks.supplier, store=mocks.store,
                          fulfiller=mocks.fulfiller, notifier=mocks.notifier,
                          social=mocks.social, sleep=mocks.sleep)
    cfg = Config(dry_run=True, telegram_token="tok", telegram_chat_id="42")
    import requests
    return ControlBot(cfg=cfg, providers=providers, session=requests.Session())


# ---- pure command handling ----

def test_help_lists_commands(bot):
    out = bot.handle_text("/help")
    for cmd in ["/status", "/report", "/orders", "/advice", "/launch",
                "/pause", "/resume"]:
        assert cmd in out


def test_status_shows_mode_and_counters(bot):
    bot.ledger.record_fulfillment(FulfillmentResult("o1", "sent", "f"),
                                  _order("o1"))
    out = bot.handle_text("/status")
    assert "DRY RUN" in out and "running" in out
    assert "orders sent/dup/failed: 1/0/0" in out


def test_report_includes_alerts(bot):
    bot.ledger.record_fulfillment(FulfillmentResult("o1", "sent", "f"),
                                  _order("o1", total=20.0), cost=18.5,
                                  product_id="bad-one")
    out = bot.handle_text("/report")
    assert "profit" in out and "alerts:" in out


def test_orders_shows_recent_events(bot):
    for i in range(7):
        bot.ledger.record_fulfillment(FulfillmentResult(f"o{i}", "sent", "f"),
                                      _order(f"o{i}"))
    out = bot.handle_text("/orders 3")
    assert out.count("\n") == 2                 # exactly 3 lines
    assert "o6" in out and "o4" in out and "o0" not in out


def test_orders_empty(bot):
    assert "no order events" in bot.handle_text("/orders")


def test_pause_resume_cycle(bot, tmp_path):
    import os
    assert "paused:" in bot.handle_text("/pause")
    assert os.path.exists(PAUSE_FLAG)
    assert "PAUSED" in bot.handle_text("/status")
    out = bot.handle_text("/resume")
    assert not os.path.exists(PAUSE_FLAG)
    assert "caught up" in out
    assert "not paused" in bot.handle_text("/resume")


def test_launch_without_csv_reports_clearly(bot, monkeypatch):
    monkeypatch.setenv("DESIGNS_CSV", "nonexistent.csv")
    assert "not found" in bot.handle_text("/launch")


def test_unknown_command(bot):
    assert "unknown command" in bot.handle_text("/frobnicate")


def test_non_command_text_gets_help(bot):
    assert "commands:" in bot.handle_text("hello?")


def test_command_exception_never_propagates(bot, monkeypatch):
    """A crashing command must reply with an error, not kill the loop."""
    monkeypatch.setattr(bot, "_do_launch", lambda: 1 / 0)
    # run_once path catches; handle_text itself raises, so verify the guard
    # in run_once by simulating an update cycle
    monkeypatch.setattr(bot, "fetch_updates", lambda offset: [
        {"update_id": 1, "message": {"chat": {"id": 42}, "text": "/launch"}}])
    said = []
    monkeypatch.setattr(bot, "_say", lambda text: said.append(text))
    assert bot.run_once() == 1
    assert any("command failed" in s for s in said)


def test_messages_from_strangers_ignored(bot, monkeypatch):
    monkeypatch.setattr(bot, "fetch_updates", lambda offset: [
        {"update_id": 1, "message": {"chat": {"id": 999}, "text": "/pause"}}])
    said = []
    monkeypatch.setattr(bot, "_say", lambda text: said.append(text))
    assert bot.run_once() == 0
    assert said == []


# ---- decisions brain ----

def _report(margin_orders=None, failure_rate=False):
    rep = build_report.__wrapped__ if hasattr(build_report, "__wrapped__") else None
    from shopbot.pipeline.analytics import BusinessReport
    r = BusinessReport()
    for pid, (units, rev, cost) in (margin_orders or {}).items():
        st = ProductStats(units=units, revenue=rev, cost=cost)
        r.per_product[pid] = st
        r.orders_sent += units
        r.revenue += rev
        r.cost += cost
    r.revenue, r.cost = round(r.revenue, 2), round(r.cost, 2)
    if failure_rate:
        r.orders_failed = 5
    return r


def test_decide_reprices_below_floor():
    # margin 10% on 8 units -> below 20% floor
    r = _report({"p1": (8, 160.0, 144.0)})
    actions = decide(r)
    kinds = {a.kind for a in actions}
    assert "reprice" in kinds
    assert any(a.target == "p1" for a in actions)


def test_decide_investigates_high_failure_rate():
    r = _report({"p1": (10, 260.0, 90.0)}, failure_rate=True)
    assert any(a.kind == "investigate" for a in decide(r))


def test_decide_expands_winners():
    r = _report({"hot": (12, 12 * 26.0, 12 * 9.0),
                 "cold": (1, 26.0, 9.0)})
    actions = decide(r)
    assert any(a.kind == "expand" and a.target == "hot" for a in actions)
    assert not any(a.kind == "expand" and a.target == "cold" for a in actions)


def test_decide_celebrates_healthy_business():
    r = _report({"p1": (8, 8 * 26.0, 8 * 9.0)})     # ~65% margin
    actions = decide(r)
    assert [a.kind for a in actions] == ["expand"] or \
           any(a.kind == "celebrate" for a in actions)


def test_decide_silent_without_data():
    from shopbot.pipeline.analytics import BusinessReport
    assert decide(BusinessReport()) == []
    assert "no recommendations yet" in format_advice([])


def test_advice_command_wired(bot):
    bot.ledger.record_fulfillment(FulfillmentResult("o1", "sent", "f"),
                                  _order("o1", total=20.0), cost=18.5,
                                  product_id="bad")
    out = bot.handle_text("/advice")
    assert "reprice" in out
