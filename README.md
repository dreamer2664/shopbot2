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
- [x] Test suite: 20 tests, offline, no network (`python -m pytest`)
- [ ] Real provider implementations (Printify, Shopify, Telegram, Postiz, Resend)
- [ ] Webhook receiver (FastAPI) for `orders/create`
- [ ] Repo pushed to GitHub
- [ ] End-to-end validation against a real Shopify dev store

## Try it now

```bash
pip install -r requirements.txt
python -m pytest                          # 20 tests, all offline
PYTHONPATH=src python -m shopbot.cli demo    # full business loop, mock providers
PYTHONPATH=src python -m shopbot.cli config  # show live/dry-run mode
```

`Config.from_env()` stays in **dry run** unless both `PRINTIFY_API_TOKEN` and
`SHOPIFY_ADMIN_TOKEN` are present. Dry run makes zero network calls, so the
whole pipeline can be exercised as many times as needed before going live.

## Test coverage

| Area | What's asserted |
|---|---|
| Pricing | .99 rounding, multiplier vs margin floor, never prices below cost+margin |
| Product launch | Correct step ordering; socials never fire if listing failed; retries absorb transient outages; nothing half-published when retries exhaust |
| Orders | Address/quantity validation; **duplicate webhook replay never double-prints**; transient retry then success; persistent outage escalates to owner; permanent rejection is not retried |

## Structure

```
shopbot/
├── docs/RESEARCH.md   # platform rules, API tiers, architecture options
├── src/               # bot code (added once direction is confirmed)
└── .env.example       # required credentials template
```
