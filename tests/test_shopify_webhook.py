"""Shopify order parsing + webhook receiver tests (HMAC, idempotency, health)."""
import base64
import hashlib
import hmac as hmac_mod
import io
import json
import threading
import time

import pytest

from shopbot.config import Config
from shopbot.providers.base import PermanentError
from shopbot.providers.mock import MockProviders
from shopbot.providers.shopify import parse_shopify_order
from shopbot.webhook import WebhookHandler, verify_shopify_hmac, serve

SECRET = "whsec_test"

SAMPLE_ORDER = {
    "id": 5512345678,
    "email": "buyer@example.com",
    "currency": "EUR",
    "total_price": "39.98",
    "line_items": [
        {"product_id": 111, "variant_id": 222, "quantity": 2},
    ],
    "shipping_address": {
        "first_name": "Giulia", "last_name": "Bianchi",
        "address1": "Via Torino 5", "city": "Milano", "zip": "20123",
        "country_code": "it", "province_code": "MI", "phone": "+39 333 123",
    },
}


def test_parse_shopify_order_maps_fields():
    o = parse_shopify_order(SAMPLE_ORDER)
    assert o.order_id == "5512345678"
    assert o.lines[0].variant_id == "222" and o.lines[0].quantity == 2
    assert o.ship_to.country == "IT"          # normalised to upper alpha-2
    assert o.ship_to.zip == "20123"
    assert o.total == 39.98 and o.currency == "EUR"
    assert o.ship_to.email == "buyer@example.com"


def test_parse_rejects_missing_address():
    bad = {"id": 1, "line_items": [], "shipping_address": None}
    with pytest.raises(PermanentError):
        parse_shopify_order(bad)


def test_hmac_verification():
    body = json.dumps(SAMPLE_ORDER).encode()
    sig = base64.b64encode(
        hmac_mod.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()
    assert verify_shopify_hmac(body, sig, SECRET) is True
    assert verify_shopify_hmac(body, sig, "wrong-secret") is False
    assert verify_shopify_hmac(body, "tampered", SECRET) is False
    assert verify_shopify_hmac(body, sig, "") is False   # no secret = reject


# ---- live server tests (localhost only, ephemeral port) ----

@pytest.fixture()
def server():
    """Boot the receiver in dry-run mode with mock providers."""
    from http.server import ThreadingHTTPServer
    mocks = MockProviders.build()
    from shopbot.providers.factory import Providers
    providers = Providers(supplier=mocks.supplier, store=mocks.store,
                          fulfiller=mocks.fulfiller, notifier=mocks.notifier,
                          social=mocks.social, sleep=mocks.sleep)
    WebhookHandler.providers = providers
    WebhookHandler.webhook_secret = SECRET
    WebhookHandler.catalog = None   # reset class state from other test modules
    WebhookHandler.ledger = None
    WebhookHandler.alerts_seen = set()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), WebhookHandler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield port, providers
    finally:
        httpd.shutdown()
        httpd.server_close()


def _post(port, payload, secret=SECRET, tamper=False):
    import urllib.request
    body = json.dumps(payload).encode()
    sig = base64.b64encode(
        hmac_mod.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    if tamper:
        sig = "AAAA" + sig[4:]
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/webhooks/orders_create", data=body,
        headers={"Content-Type": "application/json",
                 "X-Shopify-Hmac-Sha256": sig})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_webhook_happy_path_fulfills(server):
    port, providers = server
    code, body = _post(port, SAMPLE_ORDER)
    assert code == 200 and body["status"] == "sent"
    assert "5512345678" in providers.fulfiller.submitted
    # successful sales are silent by default (alert-fatigue policy);
    # the daily `report` command is the owner's sales summary
    assert providers.notifier.owner_messages == []


def test_webhook_replay_is_duplicate_not_double_print(server):
    port, providers = server
    _post(port, SAMPLE_ORDER)
    code, body = _post(port, SAMPLE_ORDER)   # Shopify retry/replay
    assert code == 200 and body["status"] == "duplicate"
    assert len(providers.fulfiller.submitted) == 1


def test_webhook_rejects_bad_hmac(server):
    port, providers = server
    code, body = _post(port, SAMPLE_ORDER, tamper=True)
    assert code == 401
    assert providers.fulfiller.submitted == {}


def test_webhook_bad_payload_returns_200_and_alerts(server):
    port, providers = server
    code, body = _post(port, {"id": 9, "line_items": [], "shipping_address": None})
    # 200 on purpose so Shopify stops retrying an unfixable payload
    assert code == 200 and body["status"] == "rejected"
    assert any("Bad webhook payload" in m for m in providers.notifier.owner_messages)


def test_health_endpoint(server):
    port, _ = server
    import urllib.request
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
        assert r.status == 200 and json.loads(r.read())["ok"] is True
