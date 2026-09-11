"""High-fidelity simulator of every external API shopbot talks to.

Purpose: PROVE the live code path works before real accounts exist. The live
providers (real `requests` sessions, real JSON, real HTTP status codes) point
at this server instead of production. It simulates:

  /printify/v1/...            supplier: blueprints, images, products, prices,
                              publish, orders (idempotent on external_id)
  /shopify/admin/api/.../graphql.json   storefront: productSet, orders query
  /telegram/bot<tok>/sendMessage|getUpdates   owner alerts + control commands
  /postiz/public/v1/posts     social posting
  /resend/emails              customer email
  /_sim/...                   test-driver controls (buy, replay, failures,
                              webhook registration, state dump)

When a simulated customer buys, the simulator POSTs a real Shopify
orders/create webhook (HMAC-signed) to the registered receiver URL — the
exact path production uses.
"""
from __future__ import annotations

import base64
import hashlib
import hmac as hmac_mod
import json
import threading
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class SimState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    n: int = 0

    # printify
    uploads: dict = field(default_factory=dict)
    products: dict = field(default_factory=dict)      # id -> product dict
    pf_orders: dict = field(default_factory=dict)     # id -> order dict
    pf_by_external: dict = field(default_factory=dict)  # external_id -> id
    pf_duplicate_attempts: int = 0

    # shopify
    listings: dict = field(default_factory=dict)      # id -> productSet input
    sh_orders: dict = field(default_factory=dict)     # id -> REST-style order
    sh_order_seq: list = field(default_factory=list)

    # telegram
    messages: list = field(default_factory=list)      # sent messages
    updates: list = field(default_factory=list)       # queued owner commands

    # postiz / resend
    posts: list = field(default_factory=list)
    emails: list = field(default_factory=list)

    # sim controls
    webhook_url: str = ""
    webhook_secret: str = ""
    fail_printify_times: int = 0
    fail_printify_status: int = 503
    fail_telegram_times: int = 0
    fired_webhooks: list = field(default_factory=list)

    def next_id(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}_{self.n}"


BLUEPRINTS = [
    {"id": 6, "title": "Unisex T-Shirt", "print_provider_id": 29,
     "variants": [("S", "black", 810), ("M", "black", 835),
                  ("L", "black", 860), ("XL", "black", 885)]},
    {"id": 11, "title": "Ceramic Mug", "print_provider_id": 41,
     "variants": [("11oz", "white", 455)]},
    {"id": 27, "title": "Die-Cut Sticker", "print_provider_id": 72,
     "variants": [("3x3", "white", 125)]},
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SimHandler(BaseHTTPRequestHandler):
    state: SimState = None  # injected by serve_sim()

    # ---------- plumbing ----------

    def _json(self, code: int, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None  # signal malformed

    def _auth_printify(self) -> bool:
        return self.headers.get("Authorization", "").startswith("Bearer ")

    def _auth_shopify(self) -> bool:
        return bool(self.headers.get("X-Shopify-Access-Token"))

    def log_message(self, fmt, *args):
        pass  # silence

    # ---------- routing ----------

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        st = self.state
        if path == "/_sim/state":
            with st.lock:
                return self._json(200, {
                    "listings": st.listings,
                    "printify_products": st.products,
                    "printify_orders": st.pf_orders,
                    "pf_duplicate_attempts": st.pf_duplicate_attempts,
                    "shopify_orders": st.sh_orders,
                    "telegram_messages": st.messages,
                    "telegram_updates_pending": len(st.updates),
                    "postiz_posts": st.posts,
                    "emails": st.emails,
                    "fired_webhooks": st.fired_webhooks,
                })
        if path == "/_sim/health":
            return self._json(200, {"ok": True})
        if path.startswith("/printify/v1/catalog/blueprints"):
            if not self._auth_printify():
                return self._json(401, {"error": "unauthorized"})
            return self._json(200, BLUEPRINTS)
        if path.startswith("/telegram/") and path.endswith("/getUpdates"):
            return self._get_updates()
        return self._json(404, {"error": f"no route GET {path}"})

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0]
        body = self._body()
        if body is None:
            return self._json(400, {"error": "malformed json"})
        st = self.state

        # ---- sim controls ----
        if path == "/_sim/register_webhook":
            with st.lock:
                st.webhook_url = body.get("url", "")
                st.webhook_secret = body.get("secret", "")
            return self._json(200, {"ok": True})
        if path == "/_sim/buy":
            return self._sim_buy(body)
        if path == "/_sim/fail_printify":
            with st.lock:
                st.fail_printify_times = int(body.get("times", 1))
                st.fail_printify_status = int(body.get("status", 503))
            return self._json(200, {"ok": True})
        if path == "/_sim/fail_telegram":
            with st.lock:
                st.fail_telegram_times = int(body.get("times", 1))
            return self._json(200, {"ok": True})
        if path == "/_sim/owner_says":
            with st.lock:
                st.n += 1
                st.updates.append({
                    "update_id": 1000 + st.n,
                    "message": {"message_id": st.n,
                                "chat": {"id": body.get("chat_id", 1)},
                                "text": body.get("text", "")}})
            return self._json(200, {"ok": True})

        # ---- printify ----
        if path.startswith("/printify/v1/shops/"):
            return self._printify_shop(path, body)

        # ---- shopify graphql ----
        if path.endswith("/graphql.json"):
            if not self._auth_shopify():
                return self._json(401, {"errors": [{"message": "unauthorized"}]})
            return self._shopify_gql(body)

        # ---- telegram sendMessage ----
        if path.startswith("/telegram/") and path.endswith("/sendMessage"):
            with st.lock:
                if st.fail_telegram_times > 0:
                    st.fail_telegram_times -= 1
                    return self._json(429, {"ok": False,
                                            "description": "Too Many Requests"})
                st.messages.append(body)
            return self._json(200, {"ok": True, "result": {"message_id": 1}})

        # ---- postiz ----
        if path.endswith("/postiz/public/v1/posts") or path.endswith("/public/v1/posts"):
            if not self.headers.get("Authorization"):
                return self._json(401, {"error": "missing api key"})
            with st.lock:
                st.n += 1
                pid = f"postiz_{st.n}"
                body_copy = dict(body, id=pid, received_at=_now())
                st.posts.append(body_copy)
            return self._json(201, [{"id": pid, "status": "queued"}])

        # ---- resend ----
        if path.endswith("/emails"):
            if not self.headers.get("Authorization", "").startswith("Bearer "):
                return self._json(401, {"error": "unauthorized"})
            with st.lock:
                st.emails.append(body)
            return self._json(200, {"id": "email_1"})

        return self._json(404, {"error": f"no route POST {path}"})

    def do_PUT(self):  # noqa: N802
        path = self.path.split("?")[0]
        body = self._body()
        if body is None:
            return self._json(400, {"error": "malformed json"})
        if path.startswith("/printify/v1/shops/"):
            return self._printify_shop(path, body)
        return self._json(404, {"error": f"no route PUT {path}"})

    # ---------- printify ----------

    def _printify_shop(self, path: str, body: dict):
        if not self._auth_printify():
            return self._json(401, {"error": "unauthorized"})
        st = self.state
        with st.lock:
            if st.fail_printify_times > 0 and "/orders.json" in path:
                st.fail_printify_times -= 1
                return self._json(st.fail_printify_status,
                                  {"error": "injected outage"})

            if path.endswith("/images.json"):
                if not body.get("url"):
                    return self._json(400, {"message": "url required"})
                st.n += 1
                uid = 900000 + st.n
                st.uploads[str(uid)] = body
                return self._json(200, {"id": uid, "status": "OK"})

            if path.endswith("/products.json"):
                bp_id = body.get("blueprint_id")
                bp = next((b for b in BLUEPRINTS if b["id"] == bp_id), None)
                if bp is None:
                    return self._json(400, {"message": "unknown blueprint_id"})
                if not body.get("images"):
                    return self._json(400, {"message": "images required"})
                st.n += 1
                pid = 400000 + st.n
                variants = [
                    {"id": pid * 100 + i, "title": size,
                     "options": {"color": color, "size": size},
                     "cost": cost, "is_enabled": False, "price": 0}
                    for i, (size, color, cost) in enumerate(bp["variants"])
                ]
                st.products[str(pid)] = dict(body, id=pid, variants=variants,
                                             published=False)
                return self._json(200, {"id": pid, "variants": variants})

            if "/products/" in path and path.endswith("/publish.json"):
                pid = path.split("/products/")[1].split("/")[0]
                prod = st.products.get(pid)
                if not prod:
                    return self._json(404, {"message": "not found"})
                if not all(v.get("is_enabled") and v.get("price", 0) > 0
                           for v in prod["variants"]):
                    # real Printify refuses to publish unpriced variants
                    return self._json(400, {
                        "message": "at least one variant must be enabled and priced"})
                prod["published"] = True
                return self._json(200, {"id": pid, "status": "published",
                                        "published_at": _now()})

            if "/products/" in path and path.endswith(".json"):
                pid = path.split("/products/")[1].split(".json")[0]
                prod = st.products.get(pid)
                if not prod:
                    return self._json(404, {"message": "not found"})
                by_id = {str(v["id"]): v for v in prod["variants"]}
                for incoming in body.get("variants", []):
                    v = by_id.get(str(incoming.get("id")))
                    if v is None:
                        return self._json(400, {"message": "unknown variant id"})
                    v.update({k: incoming[k] for k in ("price", "is_enabled")
                              if k in incoming})
                return self._json(200, {"id": prod["id"],
                                        "variants": prod["variants"]})

            if path.endswith("/orders.json"):
                ext = body.get("external_id")
                if not ext:
                    return self._json(400, {"message": "external_id required"})
                if ext in st.pf_by_external:
                    st.pf_duplicate_attempts += 1
                    existing = st.pf_orders[st.pf_by_external[ext]]
                    return self._json(200, existing)   # idempotent, like real API
                for li in body.get("line_items", []):
                    prod = st.products.get(str(li.get("product_id")))
                    if prod is None or not prod.get("published"):
                        return self._json(400, {
                            "message": f"product {li.get('product_id')} not published"})
                st.n += 1
                oid = 700000 + st.n
                order = {"id": oid, "external_id": ext, "status": "in-production",
                         "line_items": body.get("line_items"),
                         "address_to": body.get("address_to"),
                         "created_at": _now()}
                st.pf_orders[str(oid)] = order
                st.pf_by_external[ext] = str(oid)
                # mark the shopify order fulfilled so polling doesn't refetch it
                for sh in st.sh_orders.values():
                    if str(sh["id"]) == str(ext):
                        sh["fulfillment_status"] = "fulfilled"
                return self._json(202, order)

        return self._json(404, {"error": f"no printify route {path}"})

    # ---------- shopify graphql ----------

    def _shopify_gql(self, body: dict):
        query = body.get("query", "")
        variables = body.get("variables", {})
        st = self.state
        with st.lock:
            if "productSet" in query:
                inp = variables.get("input") or {}
                if not inp.get("title"):
                    return self._json(200, {"data": {"productSet": {
                        "product": None,
                        "userErrors": [{"field": ["title"],
                                        "message": "can't be blank"}]}}})
                st.n += 1
                pid = 8100000 + st.n
                # create real variant ids, like Shopify would
                variant_nodes = []
                for i, v in enumerate(inp.get("variants") or [{}]):
                    st.n += 1
                    vid = 8200000 + st.n
                    variant_nodes.append({"id": f"gid://shopify/ProductVariant/{vid}",
                                          "price": v.get("price")})
                st.listings[str(pid)] = dict(
                    inp, id=str(pid), created_at=_now(),
                    variant_ids=[n["id"].rsplit("/", 1)[-1]
                                 for n in variant_nodes])
                gid = f"gid://shopify/Product/{pid}"
                return self._json(200, {"data": {"productSet": {
                    "product": {"id": gid, "title": inp["title"],
                                "status": inp.get("status", "DRAFT"),
                                "variants": {"nodes": variant_nodes}},
                    "userErrors": []}}})

            if "unfulfilledOrders" in query or "orders(" in query:
                nodes = []
                for sh in st.sh_orders.values():
                    if sh.get("fulfillment_status") == "unfulfilled":
                        nodes.append(_rest_to_graphql_node(sh))
                return self._json(200, {"data": {"orders": {"nodes": nodes}}})

            return self._json(200, {"errors": [
                {"message": "unsupported query in simulator"}]})

    # ---------- telegram getUpdates ----------

    def _get_updates(self):
        st = self.state
        offset = 0
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        for part in qs.split("&"):
            if part.startswith("offset="):
                offset = int(part.split("=", 1)[1])
        with st.lock:
            result = [u for u in st.updates if u["update_id"] >= offset]
        return self._json(200, {"ok": True, "result": result})

    # ---------- sim: customer buys ----------

    def _sim_buy(self, body: dict):
        """Create a Shopify order and fire the real (HMAC-signed) webhook."""
        st = self.state
        listing_ids = list(st.listings)
        if not listing_ids:
            return self._json(400, {"error": "no listings to buy from"})
        listing_id = str(body.get("listing_id") or listing_ids[0])
        if listing_id not in st.listings:
            return self._json(404, {"error": "unknown listing"})
        qty = int(body.get("qty", 1))
        replays = int(body.get("replays", 0))
        with st.lock:
            listing = st.listings[listing_id]
            variant_id = int(listing.get("variant_ids", ["1"])[0])
            st.n += 1
            oid = 5500000 + st.n
            order = {
                "id": oid, "email": body.get("email", "buyer@example.com"),
                "currency": body.get("currency", "EUR"),
                "total_price": "24.99", "fulfillment_status": "unfulfilled",
                # Realistic: order line items carry STORE ids — the bot must
                # translate them to supplier ids via its catalog before
                # submitting to Printify (this is the exact thing that breaks
                # naive integrations).
                "line_items": [{"product_id": int(listing_id),
                                "variant_id": variant_id, "quantity": qty}],
                "shipping_address": {
                    "first_name": "Mario", "last_name": "Rossi",
                    "address1": "Via Roma 1", "city": "Milano",
                    "zip": body.get("zip", "20121"),
                    "country_code": "IT", "province_code": "MI",
                    "phone": "+39 333 000"},
            }
            st.sh_orders[str(oid)] = order
            st.sh_order_seq.append(str(oid))
            url, secret = st.webhook_url, st.webhook_secret

        fired = 0
        if url:
            payload = json.dumps(order).encode()
            for _ in range(1 + replays):
                sig = base64.b64encode(hmac_mod.new(
                    secret.encode(), payload, hashlib.sha256).digest()).decode()
                req = urllib.request.Request(
                    url, data=payload,
                    headers={"Content-Type": "application/json",
                             "X-Shopify-Hmac-Sha256": sig,
                             "X-Shopify-Topic": "orders/create"})
                try:
                    with urllib.request.urlopen(req, timeout=10) as r:
                        code = r.status
                except urllib.error.HTTPError as e:
                    code = e.code
                with st.lock:
                    st.fired_webhooks.append({"order": oid, "http": code})
                fired += 1
        return self._json(200, {"order_id": oid, "webhooks_fired": fired})


def _rest_to_graphql_node(sh: dict) -> dict:
    sa = sh["shipping_address"]
    return {
        "id": f"gid://shopify/Order/{sh['id']}",
        "name": f"#{sh['id']}",
        "totalPrice": sh["total_price"],
        "currencyCode": sh["currency"],
        "email": sh["email"], "phone": None,
        "shippingAddress": {
            "firstName": sa["first_name"], "lastName": sa["last_name"],
            "address1": sa["address1"], "city": sa["city"], "zip": sa["zip"],
            "countryCode": sa["country_code"],
            "provinceCode": sa.get("province_code", ""),
            "phone": sa.get("phone")},
        "lineItems": {"nodes": [
            {"quantity": li["quantity"],
             "product": {"id": f"gid://shopify/Product/{li['product_id']}"},
             "variant": {"id": f"gid://shopify/ProductVariant/{li['variant_id']}"}}
            for li in sh["line_items"]]},
    }


def serve_sim(state: SimState | None = None, host: str = "127.0.0.1",
              port: int = 0):
    """Returns (httpd, state, port). Runs in a background thread."""
    st = state or SimState()
    SimHandler.state = st
    httpd = ThreadingHTTPServer((host, port), SimHandler)
    real_port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, st, real_port


if __name__ == "__main__":
    # manual mode: python -m shopbot.devserver  -> serves on :8788 forever
    import time
    httpd, st, port = serve_sim(host="0.0.0.0", port=8788)
    print(f"API simulator listening on http://localhost:{port}")
    print("endpoints: /printify/v1 /shopify/admin/api/2025-01/graphql.json "
          "/telegram /postiz /resend /_sim/*")
    while True:
        time.sleep(3600)
