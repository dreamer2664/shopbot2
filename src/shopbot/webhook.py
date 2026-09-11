"""Webhook receiver for Shopify `orders/create`.

Standard library only (no FastAPI/uvicorn dependency): ThreadingHTTPServer on
0.0.0.0. Shopify retries webhooks on non-2xx, so:
  - return 200 fast, fulfill synchronously (our submit is one API call), and
  - idempotency on order id means a retry after a 500 never double-prints.

HMAC: Shopify signs each request (X-Shopify-Hmac-Sha256, base64 HMAC-SHA256 of
the raw body with the app's client secret). We verify with compare_digest.

Local dev: expose via `cloudflared tunnel --url http://localhost:8787` (free)
and register the resulting URL in the Shopify webhook settings.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import Config
from .pipeline.orders import handle_order
from .providers.base import PermanentError
from .providers.factory import Providers, build_providers
from .providers.shopify import parse_shopify_order

log = logging.getLogger("shopbot.webhook")


def verify_shopify_hmac(body: bytes, header_value: str, secret: str) -> bool:
    if not secret:
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, header_value or "")


class WebhookHandler(BaseHTTPRequestHandler):
    providers: Providers        # injected by serve()
    webhook_secret: str = ""    # empty = skip HMAC (dev only, warn loudly)

    def do_POST(self):  # noqa: N802
        if self.path.rstrip("/") not in ("/webhooks/orders_create", ""):
            self._respond(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            self._respond(413, {"error": "payload too large"})
            return
        body = self.rfile.read(length)

        if self.webhook_secret:
            if not verify_shopify_hmac(body,
                                       self.headers.get("X-Shopify-Hmac-Sha256", ""),
                                       self.webhook_secret):
                log.warning("HMAC verification failed; rejecting")
                self._respond(401, {"error": "invalid hmac"})
                return
        else:
            log.warning("WEBHOOK_SECRET not set — accepting unsigned payloads "
                        "(dev mode only!)")

        try:
            raw = json.loads(body)
        except json.JSONDecodeError:
            self._respond(400, {"error": "invalid json"})
            return

        try:
            order = parse_shopify_order(raw)
        except PermanentError as exc:
            # 200 on purpose: a payload Shopify will never fix shouldn't be
            # retried forever. We alert the owner instead.
            log.error("unparseable order: %s", exc)
            if self.providers.notifier:
                self.providers.notifier.notify_owner(f"Bad webhook payload: {exc}")
            self._respond(200, {"status": "rejected", "reason": str(exc)})
            return

        result = handle_order(order, fulfiller=self.providers.fulfiller,
                              notifier=self.providers.notifier,
                              sleep=self.providers.sleep)
        status = 200 if result.status in ("sent", "duplicate") else 500
        self._respond(status, {"status": result.status,
                               "provider_order_id": result.provider_order_id,
                               "error": result.error})

    def do_GET(self):  # noqa: N802 — healthcheck
        if self.path.rstrip("/") in ("/health", ""):
            self._respond(200, {"ok": True,
                                "dry_run": not hasattr(self.providers.supplier, "token")})
        else:
            self._respond(404, {"error": "not found"})

    def _respond(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quieter default logging
        log.info(fmt, *args)


def serve(host: str = "0.0.0.0", port: int = 8787,
          cfg: Config | None = None) -> None:
    cfg = cfg or Config.from_env()
    providers = build_providers(cfg)
    import os
    WebhookHandler.providers = providers
    WebhookHandler.webhook_secret = os.environ.get("SHOPIFY_WEBHOOK_SECRET", "")
    mode = "DRY RUN (mock fulfillment)" if cfg.dry_run else "LIVE"
    log.info("shopbot webhook receiver starting on %s:%s — %s", host, port, mode)
    ThreadingHTTPServer((host, port), WebhookHandler).serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    serve()
