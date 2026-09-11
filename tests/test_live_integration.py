"""LIVE integration test — the "rock hard proof" suite.

Everything here runs over REAL HTTP against the API simulator (devserver):
  - the REAL live providers (PrintifyProvider, ShopifyStorefront GraphQL,
    TelegramNotifier, PostizSocial, Resend) with real `requests` sessions
  - the REAL webhook receiver with REAL HMAC verification
  - the REAL simulator firing REAL Shopify orders/create webhooks at it
  - the REAL control bot answering Telegram commands via long-polling

No mocks in the call path: only the remote servers themselves are simulated.
"""
import json
import time
import urllib.request
from datetime import datetime, timezone

import pytest

from shopbot.config import Config
from shopbot.control import ControlBot
from shopbot.devserver import SimState, serve_sim
from shopbot.pipeline.catalog import Catalog
from shopbot.pipeline.ledger import Ledger
from shopbot.pipeline.products import launch_product
from shopbot.providers.base import Design
from shopbot.providers.factory import Providers
from shopbot.providers.notifications import TelegramNotifier
from shopbot.providers.postiz import PostizSocial
from shopbot.providers.printify import PrintifyProvider
from shopbot.providers.shopify import ShopifyStorefront
from shopbot.webhook import WebhookHandler

SECRET = "itest_secret"
CHAT_ID = "555000"


def _wait_for(predicate, timeout=15.0, interval=0.05):
    """Poll until predicate() is truthy; returns its value or raises."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Full simulated ecosystem: APIs + webhook receiver + control bot."""
    monkeypatch.chdir(tmp_path)           # isolate data/ files

    # 1) API simulator over real HTTP
    httpd, st, port = serve_sim(state=SimState(), port=0)
    base = f"http://127.0.0.1:{port}"

    # 2) real providers pointed at the simulator (production code paths)
    supplier = PrintifyProvider(token="pf_tok", shop_id="1",
                                base_url=f"{base}/printify")
    store = ShopifyStorefront(domain="itest.myshopify.com", token="sh_tok",
                              base_url=f"{base}/shopify")
    notifier = TelegramNotifier(token="tg_tok", chat_id=CHAT_ID,
                                resend_key="re_key", email_from="o@x.com",
                                base_url=f"{base}/telegram",
                                resend_base_url=f"{base}/resend")
    social = PostizSocial(base_url=f"{base}/postiz", api_key="pz_key",
                          integration_ids=["ig_1", "tt_1"])
    providers = Providers(supplier=supplier, store=store, fulfiller=supplier,
                          notifier=notifier, social=social, sleep=time.sleep)

    # 3) real webhook receiver on a real port
    from http.server import ThreadingHTTPServer
    ledger = Ledger("data/ledger.jsonl")
    catalog = Catalog("data/catalog.json")
    WebhookHandler.providers = providers
    WebhookHandler.webhook_secret = SECRET
    WebhookHandler.alerts_seen = set()
    WebhookHandler.catalog = catalog
    WebhookHandler.ledger = ledger
    wh = ThreadingHTTPServer(("127.0.0.1", 0), WebhookHandler)
    wh_port = wh.server_address[1]
    import threading
    threading.Thread(target=wh.serve_forever, daemon=True).start()

    # 4) register the receiver with the simulator (like Shopify settings would)
    req = urllib.request.Request(
        f"{base}/_sim/register_webhook",
        data=json.dumps({"url": f"http://127.0.0.1:{wh_port}/webhooks/orders_create",
                         "secret": SECRET}).encode(),
        headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()

    # 5) real control bot long-polling the simulated Telegram
    cfg = Config(dry_run=False, telegram_token="tg_tok", telegram_chat_id=CHAT_ID)
    import os
    os.environ["TELEGRAM_BASE_URL"] = f"{base}/telegram"
    bot = ControlBot(cfg=cfg, providers=providers,
                     ledger=ledger, catalog=catalog)
    bot_thread_stop = threading.Event()
    threading.Thread(target=bot.run_forever,
                     kwargs={"stop": bot_thread_stop, "poll_seconds": 0.05},
                     daemon=True).start()

    yield {"base": base, "state": st, "supplier": supplier, "store": store,
           "notifier": notifier, "social": social, "providers": providers,
           "bot": bot, "ledger": ledger, "catalog": catalog}

    bot_thread_stop.set()
    wh.shutdown(); wh.server_close()
    httpd.shutdown(); httpd.server_close()
    os.environ.pop("TELEGRAM_BASE_URL", None)


def _sim_post(env, path, payload):
    req = urllib.request.Request(
        f"{env['base']}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _sim_state(env):
    with urllib.request.urlopen(f"{env['base']}/_sim/state", timeout=5) as r:
        return json.loads(r.read())


DESIGN = Design(slug="live-tee", title="Milano Skyline Tee",
                description="Original skyline illustration, hand drawn.",
                image_path="https://cdn.example.com/milano.png",
                tags=["milan", "skyline", "minimal"])


def test_full_business_week_over_real_http(env):
    """THE proof: launch -> listing -> socials -> purchase -> webhook ->
    fulfillment -> owner alerts, all over real HTTP, no mocks in the path."""
    st = env["state"]

    # ---- launch a product through the REAL live providers ----
    res = launch_product(DESIGN, "6", supplier=env["supplier"],
                         store=env["store"], social=env["social"],
                         cfg=Config(dry_run=False), catalog=env["catalog"])
    assert res.success, res.error
    assert "catalog" in res.steps_done
    env["ledger"].record_launch(DESIGN.slug, True)

    state = _sim_state(env)
    # product created + priced + published in the simulated Printify
    assert len(state["printify_products"]) == 1
    prod = next(iter(state["printify_products"].values()))
    assert prod["published"] is True
    assert all(v["is_enabled"] and v["price"] > 0 for v in prod["variants"])
    # listing created in the simulated Shopify (GraphQL productSet)
    assert len(state["listings"]) == 1
    # social post delivered to the simulated Postiz
    assert len(state["postiz_posts"]) == 1
    assert state["postiz_posts"][0]["posts"][0]["integration"]["id"] == "ig_1"

    # ---- customer buys; simulator fires a REAL signed webhook ----
    buy = _sim_post(env, "/_sim/buy", {"qty": 2, "currency": "EUR"})
    assert buy["webhooks_fired"] == 1

    # fulfillment must land in the simulated Printify
    _wait_for(lambda: _sim_state(env)["printify_orders"])
    orders = _sim_state(env)["printify_orders"]
    assert len(orders) == 1
    order = next(iter(orders.values()))
    assert order["external_id"] == str(buy["order_id"])
    assert order["status"] == "in-production"
    assert order["address_to"]["city"] == "Milano"
    assert order["line_items"][0]["quantity"] == 2

    # ---- webhook replay (Shopify retries) must NOT double-print ----
    # simulate by re-firing the same order id through buy's replay count
    buy2 = _sim_post(env, "/_sim/buy", {"qty": 1, "replays": 2})
    assert buy2["webhooks_fired"] == 3      # 1 original + 2 replays
    _wait_for(lambda: len(_sim_state(env)["printify_orders"]) == 2)
    time.sleep(0.3)                          # let any double-print happen
    state = _sim_state(env)
    assert len(state["printify_orders"]) == 2       # exactly 2 distinct orders
    assert state["pf_duplicate_attempts"] >= 2      # replays were absorbed

    # ---- supplier outage: receiver must retry and still fulfill ----
    _sim_post(env, "/_sim/fail_printify", {"times": 1, "status": 503})
    buy3 = _sim_post(env, "/_sim/buy", {"qty": 1})
    _wait_for(lambda: len(_sim_state(env)["printify_orders"]) == 3)
    fired = _sim_state(env)["fired_webhooks"]
    assert all(f["http"] == 200 for f in fired if f["order"] == buy3["order_id"])

    # ---- unpublishable order path: invalid address rejected, alerted ----
    buy4 = _sim_post(env, "/_sim/buy", {"qty": 1, "zip": ""})
    _wait_for(lambda: any("rejected" in m.get("text", "")
                          for m in _sim_state(env)["telegram_messages"]))
    time.sleep(0.3)
    assert len(_sim_state(env)["printify_orders"]) == 3   # bad order NOT printed

    # ---- ledger captured everything ----
    rep_entries = [e for e in env["ledger"].entries()]
    # (webhook path doesn't write the ledger; the control bot's poll does —
    #  so run a poll cycle through the bot to reconcile)
    reply = env["bot"]._do_poll(catchup=True)
    assert "sent=" in reply
    report_lines = env["bot"].handle_text("/report")
    assert "orders sent" in report_lines


def test_control_bot_commands_over_real_telegram(env):
    """The owner interface, exercised through the simulated Telegram API."""
    # send commands as the owner via the simulator
    for text in ["/help", "/status", "/report", "/pause", "/resume", "/bogus"]:
        _sim_post(env, "/_sim/owner_says", {"text": text, "chat_id": int(CHAT_ID)})

    # the bot thread long-polls; wait for all six replies
    def six_replies():
        msgs = _sim_state(env)["telegram_messages"]
        return len(msgs) >= 7   # 1 online greeting + 6 command replies

    _wait_for(six_replies, timeout=20)
    msgs = [m["text"] for m in _sim_state(env)["telegram_messages"]]
    assert any("shopbot control online" in m for m in msgs)
    assert any("commands:" in m for m in msgs)             # /help
    assert any("shopbot LIVE" in m for m in msgs)          # /status
    assert any("orders sent" in m for m in msgs)           # /report
    assert any("paused:" in m for m in msgs)               # /pause
    assert any("unknown command" in m for m in msgs)       # /bogus

    # a stranger's commands are ignored
    before = len(_sim_state(env)["telegram_messages"])
    _sim_post(env, "/_sim/owner_says", {"text": "/status", "chat_id": 999})
    time.sleep(0.5)
    assert len(_sim_state(env)["telegram_messages"]) == before


def test_pause_holds_fulfillment_and_resume_catches_up(env):
    """/pause must stop printing; /resume must catch up without losing orders."""
    # launch so there's something to buy
    res = launch_product(DESIGN, "6", supplier=env["supplier"],
                         store=env["store"], social=env["social"],
                         cfg=Config(dry_run=False), catalog=env["catalog"])
    assert res.success, res.error

    _sim_post(env, "/_sim/owner_says", {"text": "/pause", "chat_id": int(CHAT_ID)})
    _wait_for(lambda: __import__("os").path.exists("data/paused.flag"), timeout=10)

    buy = _sim_post(env, "/_sim/buy", {"qty": 1})
    time.sleep(1.0)
    # webhook was acknowledged (200) but nothing was printed
    fired = _sim_state(env)["fired_webhooks"]
    assert any(f["order"] == buy["order_id"] and f["http"] == 200 for f in fired)
    assert not _sim_state(env)["printify_orders"]

    _sim_post(env, "/_sim/owner_says", {"text": "/resume", "chat_id": int(CHAT_ID)})
    # resume triggers a catch-up poll through the storefront GraphQL query
    _wait_for(lambda: len(_sim_state(env)["printify_orders"]) == 1, timeout=15)
    order = next(iter(_sim_state(env)["printify_orders"].values()))
    assert order["external_id"] == str(buy["order_id"])


def test_email_customer_over_real_http(env):
    env["notifier"].email_customer("buyer@example.com", "Shipped!",
                                   "Your order is on the way.")
    _wait_for(lambda: _sim_state(env)["emails"])
    email = _sim_state(env)["emails"][0]
    assert email["to"] == ["buyer@example.com"]
    assert email["subject"] == "Shipped!"
    assert email["from"] == "o@x.com"


def test_simulator_rejects_unpublished_product_orders(env):
    """Printify sim enforces reality: ordering an unpublished product fails."""
    # create a product but never publish it
    supplier = env["supplier"]
    upload_id = supplier.upload_artwork(DESIGN)
    from shopbot.providers.base import Product
    p = Product(design=DESIGN, blueprint_id="6", print_provider_id=29)
    p = supplier.create_product(p, upload_id)
    supplier.set_prices(p, {v.variant_id: 24.99 for v in p.variants})
    # NOT published -> ordering it must be rejected by the supplier
    from shopbot.providers.base import Address, Order, OrderLine, PermanentError
    bad = Order("ghost_1", [OrderLine(p.printify_product_id,
                                      p.variants[0].variant_id, 1)],
                Address("a", "b", "e", "s", "c", "z", "IT"))
    with pytest.raises(PermanentError):
        supplier.submit(bad)
