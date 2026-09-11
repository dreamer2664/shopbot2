# shopbot

Automated e-commerce operations bot — legitimate stack only.

## Concept

A pipeline that automates a real online store end-to-end:

1. **Catalog** — create/manage products via official supplier APIs (e.g. Printify for print-on-demand).
2. **Storefront** — WooCommerce (free, self-hosted) or Shopify.
3. **Orders** — supplier webhooks (`order:created`) trigger automatic fulfillment. No polling, no scraping.
4. **Marketing** — automatic cross-posting of product content to the owner's *own* social accounts via official APIs / open-source schedulers (Postiz, Buffer free tier).
5. **Notifications** — transactional email via free tiers (Resend: 100/day, Brevo: 300/day).

## Hard rules for this project

- Only official APIs. No scraping marketplaces, no fake accounts, no ban evasion.
- No copyrighted material we don't own or have a license to sell.
- Secrets live in `.env` (gitignored). Never commit tokens.

## Status

- [x] Research: platform rules, free API tiers (`docs/RESEARCH.md`)
- [x] Direction: print-on-demand (Printify) + Shopify storefront
- [x] Core pipeline: pricing, product launch, order fulfillment — mock providers
- [x] Live providers: Printify, Shopify, Telegram, Resend, Postiz
- [x] Webhook receiver: Shopify `orders/create` with HMAC verification
- [x] Test suite: 46 tests, all offline (`python -m pytest`)
- [ ] Credentials in `.env` + live smoke test on a real Shopify dev store
- [ ] Design assets (your own artwork) + first product launch

## Try it now

```bash
pip install -r requirements.txt
python -m pytest                             # 46 tests, no network
PYTHONPATH=src python -m shopbot.cli demo    # full business loop, mock providers
PYTHONPATH=src python -m shopbot.cli config  # show live/dry-run mode
PYTHONPATH=src python -m shopbot.webhook     # webhook receiver (dry run without creds)
```

Safety guarantee: `Config.from_env()` stays in **dry run** unless BOTH
`PRINTIFY_API_TOKEN` and `SHOPIFY_ADMIN_TOKEN` are present. Dry run makes zero
network calls. A bad `SHOPIFY_STORE_DOMAIN` (not *.myshopify.com) is rejected
before any request is made.

For local webhook testing expose the receiver with a free tunnel:
`cloudflared tunnel --url http://localhost:8787`, then register the URL in
Shopify → Settings → Notifications → Webhooks (`orders/create`, with signing
secret copied into `SHOPIFY_WEBHOOK_SECRET`).

## Test coverage

| Area | What's asserted |
|---|---|
| Pricing | .99 rounding, multiplier vs margin floor, never prices below cost+margin |
| Product launch | Correct step ordering; socials never fire if listing failed; retries absorb transient outages; nothing half-published when retries exhaust |
| Orders | Address/quantity validation; **duplicate webhook replay never double-prints**; transient retry then success; persistent outage escalates to owner; permanent rejection is not retried |
| Printify provider | Payload shapes; **cents↔dollars and int↔str id normalisation**; HTTP status → Transient/Permanent mapping; network errors retryable |
| Shopify parsing | Order/webhook payload mapping; country normalisation; missing address rejected |
| Webhook server | Real local HTTP server: **HMAC accept/reject**, happy path, replay → duplicate, unfixable payload → 200 + owner alert (stops Shopify retry storms), healthcheck |
| Factory/config | Dry-run default; partial creds stay dry-run; full creds go live; bad domain rejected |

## Structure

```
shopbot/
├── docs/RESEARCH.md              # platform rules, API tiers, architecture
├── src/shopbot/
│   ├── config.py                 # env config + dry-run safety gate
│   ├── cli.py                    # `demo` and `config` commands
│   ├── webhook.py                # Shopify orders/create receiver (HMAC verified)
│   ├── pipeline/
│   │   ├── pricing.py            # cost + margin floor -> .99 retail price
│   │   ├── products.py           # design -> supplier -> priced -> store -> socials
│   │   ├── orders.py             # validate -> fulfill -> alert, idempotent
│   │   └── retry.py              # exponential backoff, transient-only
│   └── providers/
│       ├── base.py               # interfaces + data models + error taxonomy
│       ├── mock.py               # offline providers with injectable failures
│       ├── factory.py            # dry-run vs live wiring
│       ├── http.py               # shared HTTP + status-code error mapping
│       ├── printify.py           # supplier: products, pricing, orders
│       ├── shopify.py            # storefront: listings, order fetch/parse
│       ├── notifications.py      # Telegram (owner) + Resend (customer email)
│       └── postiz.py             # social posting via self-hosted Postiz
├── tests/                        # 46 offline tests
└── .env.example                  # credential template (never commit .env)
```

## Remaining human-only steps

1. Create your own artwork (never branded/celebrity/fan-art — top POD ban vector).
2. Create the accounts: Printify, Shopify trial, Telegram bot, Resend, Postiz.
3. Register the webhook URL in Shopify and copy the signing secret to `.env`.
4. Tax: occasional selling is fine privately; regular commercial activity in
   Italy eventually requires a partita IVA.
