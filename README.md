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

- [x] Research: platform rules, free API tiers (see `docs/RESEARCH.md`)
- [ ] Direction confirmed (POD store vs eBay reselling vs sandbox)
- [ ] Repo pushed to GitHub (owner creates empty repo; push via `gh` or SSH)
- [ ] Core pipeline implementation

## Structure

```
shopbot/
├── docs/RESEARCH.md   # platform rules, API tiers, architecture options
├── src/               # bot code (added once direction is confirmed)
└── .env.example       # required credentials template
```
