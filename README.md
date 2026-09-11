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
- [x] Durability: append-only ledger, poll checkpoint (crash-safe)
- [x] Chaos training: 1000 randomized business days, 6 global invariants, 0 violations
- [x] Business knowledge base: cited market benchmarks + rule-based SEO copywriting
- [x] Market simulator + year-long training run (pricing playbook + ops rehearsal)
- [x] Analytics: ledger → business report → benchmark-based alerts
- [x] Test suite: 244 tests, all offline (`python -m pytest`)
- [ ] Credentials in `.env` + live smoke test on a real Shopify dev store
- [ ] Design assets (your own artwork) + first product launch

## Commands

Works on Windows (PowerShell/cmd), macOS, Linux — no env vars, no install step:

```bash
pip install -r requirements.txt
python -m pytest                          # 244 tests, no network
python shopbot.py demo                    # simulated business day (mocks)
python shopbot.py config                  # live/dry-run + which creds present
python shopbot.py launch designs.example.csv
python shopbot.py poll                    # one-shot order processing (cron-friendly)
python shopbot.py report                  # revenue/order summary from ledger
python shopbot.py serve                   # webhook receiver
```

(PowerShell note: `PYTHONPATH=src python ...` is bash syntax; the
`shopbot.py` launcher above makes it unnecessary.)

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
| Orders | Address/quantity/**line-item id** validation; **duplicate webhook replay never double-prints**; transient retry then success; persistent outage escalates to owner; permanent rejection not retried; **currency mismatch alerts owner** |
| Printify provider | Payload shapes; cents↔dollars and int↔str id normalisation; **bad ids → PermanentError, never raw crashes**; HTTP status → error-class mapping |
| Shopify parsing | Order/webhook payload mapping; country normalisation; missing address rejected |
| Webhook server | Real local HTTP server: HMAC accept/reject, replay → duplicate, unfixable payload → 200 + alert, healthcheck |
| Factory/config | Dry-run default; partial creds stay dry-run; full creds go live; bad domain rejected |
| Ledger | Crash-safe JSONL: torn last line skipped; dedupe ids; daily revenue summary; unicode-safe |
| Checkpoint | Roundtrip; corrupt file → safe fallback; atomic overwrite |
| **Chaos (x120 seeds)** | Randomized business days with injected outages/rejections/replays. Invariants: no double-print, invalid orders never fulfilled, no ghost social posts, no unpublished listings, ledger == fulfiller truth, failures never crash |
| Shopify GraphQL | productSet payload shape, gid→id parsing, userErrors/graphql errors → PermanentError, DRAFT vs ACTIVE, duplicate option values, order query mapping, bad-domain rejection |
| Simulator | Determinism per seed, demand falls as price rises, seasonality, Poisson mean ≈ λ, accounting identity, optimal price beats naive heuristic, cost shift moves optimum |
| Knowledge/copy | Every generated title/meta/alt passes the cited SEO checklist; keyword front-loaded; caps respected |
| Pricing v2 | Fee-aware formula inverts exactly, impossible targets rejected, market-floor snap, margin clamped to survival band |
| Analytics | Aggregation, margin-floor / failure-rate / per-product reprice alerts, empty ledger |
| Training run | Stage 1 covers all categories profitably; stage 2 end-to-end invariants; playbook prices within sane market bands |
| **End-to-end** | CSV → launch → v2 pricing clears margin floor → 15 orders + replay + invalid → analytics bestseller correct → CLI report |
| Regressions | Every bug found by adversarial probing or training gets a permanent named test (`test_regressions.py`, `test_regression_order_fieldslip.py`) |

## Structure

```
shopbot/
├── docs/RESEARCH.md              # platform rules, API tiers, market data + sources
├── designs.example.csv           # input format for `cli launch`
├── src/shopbot/
│   ├── config.py                 # env config + dry-run safety gate
│   ├── cli.py                    # demo / config / launch / poll / report / serve
│   ├── webhook.py                # Shopify orders/create receiver (HMAC verified)
│   ├── knowledge/                # encoded business research (cited in-file)
│   │   ├── __init__.py           #   category benchmarks, fee data, SEO limits
│   │   └── copywriting.py        #   rule-based SEO listing copy + self-check
│   ├── pipeline/
│   │   ├── pricing.py            # v1 (multiplier) + v2 (fee-aware, market-checked)
│   │   ├── products.py           # design -> copy -> supplier -> priced -> store -> socials
│   │   ├── orders.py             # validate -> fulfill -> alert policy, idempotent
│   │   ├── ledger.py             # crash-safe JSONL business memory
│   │   ├── analytics.py          # ledger -> report -> benchmark-based alerts
│   │   ├── state.py              # poll checkpoint (atomic writes)
│   │   └── retry.py              # exponential backoff, transient-only
│   ├── sim/                      # market simulator + year-long training run
│   │   ├── market.py             #   elasticity + seasonality demand model
│   │   └── training.py           #   stage1 pricing search, stage2 ops rehearsal
│   └── providers/
│       ├── base.py               # interfaces + data models + error taxonomy
│       ├── mock.py               # offline providers with injectable failures
│       ├── factory.py            # dry-run vs live wiring
│       ├── http.py               # shared HTTP + status-code error mapping
│       ├── printify.py           # supplier: products, pricing, orders
│       ├── shopify.py            # storefront: GraphQL Admin API (REST is deprecated)
│       ├── notifications.py      # Telegram (owner) + Resend (customer email)
│       └── postiz.py             # social posting via self-hosted Postiz
├── tests/                        # 230 offline tests incl. chaos + regressions
└── .env.example                  # credential template (never commit .env)
```

## Training (simulated experience before going live)

`PYTHONPATH=src python -m shopbot.sim.training` runs:

1. **Pricing search** — grid-searches the profit-maximizing price per category
   over a simulated year (elasticity + seasonality demand model grounded in
   published benchmarks). Writes `data/pricing_playbook.json`.
2. **Operations rehearsal** — pushes ~10k simulated orders through the REAL
   pipeline with injected webhook replays, invalid addresses and supplier
   outages; asserts the never-double-print / never-fulfill-invalid invariants;
   finishes with an analytics report.

Latest run: 10,183 orders, 10,180 sent, 279/279 replays absorbed, 5 outage
days survived, 4 owner alerts (was 20,645 before the alert-fatigue fix),
0 invariant violations.

Lessons the training run taught the codebase (each now enforced by tests):
- Alert fatigue is real: successful sales are silent, only problems page the
  owner; recurring advisories dedupe per key.
- Positional-argument field slips corrupt ledgers silently → Order validates
  its own field types at construction.
- Cheap items priced by margin alone land below what the market bears →
  pricing snaps to the researched market floor.

## Remaining human-only steps

1. Create your own artwork (never branded/celebrity/fan-art — top POD ban vector).
2. Create the accounts: Printify, Shopify trial, Telegram bot, Resend, Postiz.
3. Register the webhook URL in Shopify and copy the signing secret to `.env`.
4. Tax: occasional selling is fine privately; regular commercial activity in
   Italy eventually requires a partita IVA.
