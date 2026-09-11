# Research — shopbot (as of 2026-09-11)

## 1. Why the Vinted/Wallapop route is a dead end (data, not opinion)

- Vinted bans for "selling items for commercial purposes" and treats **multiple accounts as a permanent-ban offense** (vinted.com/help/392). Their 2026 detection flags: high-volume identical new items, multiple sizes of one product, "can order more" language, catalogue-style listings, duplicate relisting.
- Even **approved Vinted Pro sellers get perma-banned** for selling new-with-tags items at volume (r/vinted, Dec 2025). Pro accounts in some markets also forbid bulk new-with-tags items outright.
- Wallapop has a legal Pro path, but dropshipping new Shein/Temu goods still violates supplier ToS, image copyright, and EU consumer law (2-year warranty liability sits with *us* as the seller). Wallapop dispute resolution is heavily buyer-favored — sellers routinely lose "empty box" disputes.
- Automating Shein/Temu checkouts + scraping their product photos = ToS violation + copyright infringement on the images.

**Conclusion:** the ban the owner already received is the expected outcome of this model, and evasion (fresh emails/accounts) escalates to permanent bans via fingerprinting. Not buildable.

## 2. Social media auto-posting — what's actually free (official APIs only)

Auto-posting **your own products to your own accounts** is legitimate and common. But the API landscape in 2026:

| Platform | Free? | Approval friction | Limits / gotchas |
|---|---|---|---|
| **Instagram / Facebook** | Yes | **Heavy** — Meta App Review (weeks), Business/Creator account + linked FB Page | ~200 req/hr per token; 100 posts/24h/account; Pages-only on FB |
| **TikTok** | Yes | **Heavy** — audit required; unaudited apps can ONLY post private (self-only) | ~15 posts/day/creator; audit needs demo video of full flow |
| **X (Twitter)** | **No** — pay-per-use since Feb 2026 ($0.01/post) | Light (dev account) | Legacy "free tier" ≈17 posts/day; new devs pay from request one |
| **Pinterest** | Trial tier = instant | Light → Medium (video demo for Standard) | **Trial pins are hidden from the public web** — useless for marketing until approved |
| **YouTube** | Yes | Light | 10,000 quota units/day; uploads are expensive in quota |
| **Bluesky** | Yes | **None** (open AT Protocol) | 5,000 points/hr; simplest real API |
| **Telegram** | Yes | **None** (BotFather) | Great for order notifications to the owner |
| **Discord** | Yes | **None** (webhook) | Same — notifications |

### Practical takeaways
- Building direct API integrations for IG/FB/TikTok ourselves = weeks of app review before a single public post. Terrible for a beginner MVP.
- **Shortcut that stays legit:** use an already-approved tool that holds the Meta/TikTok approvals:
  - **Postiz** (open-source, self-hostable, free) — schedules/publishes to IG, FB, TikTok, X, Pinterest, etc. Has an API we can drive programmatically.
  - **Buffer free tier** — 3 channels, ~10 scheduled posts per channel.
  - Shopify's own free channel apps (Instagram/Facebook/TikTok sales channels) sync product catalogs natively.
- Telegram/Discord bots: zero friction, use for the *owner-facing* alerting (new order, out of stock, daily revenue summary).

## 3. Print-on-demand (recommended core) — Printify API

- **API is completely free**, even on the free Printify plan. Premium plans only unlock unit-price discounts, not endpoints. You pay only product+shipping cost when an order goes to production.
- Auth: Personal Access Token (Bearer), scopes: `shops.read`, `catalog.read`, `products.read/write`, `orders.read/write`, `uploads.read/write`, `webhooks.read/write`.
- Full lifecycle available: list blueprints → upload artwork → create product → set variants/prices → publish to connected store.
- **Webhooks** (`order:created`, etc.) — push-based order handling; polling GET /orders is the common mistake that breaks at scale.
- Order flow: `POST /v1/shops/{shop_id}/orders.json` with `line_items`, `shipping_method`, `address_to`; order sits on-hold until `send_to_production`/publish → provider prints & ships in 24–72h. `send_shipping_notification` emails the customer automatically.
- No sandbox — test with a cheap sticker product, keep auto-send disabled, cancel before production.
- Native integrations exist for Shopify/Etsy/WooCommerce/Ebay that handle OAuth + order routing for free; the API wins for bulk/custom logic.

## 4. Storefront options (honest costs)

| Option | Cost | Automation friendliness |
|---|---|---|
| Square Online | Free plan, 2.9% + 30¢ per transaction | Limited API; no self-hosting |
| Big Cartel | Free up to 5 products | Very narrow; not automation-friendly |
| Medusa / Saleor | Open-source, free | Self-host + you build everything; overkill for a beginner |
| WooCommerce (self-hosted) | Software free; needs a host. Genuinely-free options are thin (most "free" hosts are trials); Oracle Cloud Always-Free VM works but is fiddly. Realistic: ~€3-5/mo shared hosting | Full REST API, free, no review |
| Shopify | 3-day free trial (no credit card), then $1/mo for 3 months (~$3 total), then Basic at $39/mo ($29/mo billed annually) | Excellent Admin API, native Printify app, zero server maintenance |
| Etsy | €0.20/listing | Native Printify integration; API access is restricted |
| eBay | Free-ish listings | Real official APIs (Sell/Browse/Trading), free dev keys |

## 5. Email without getting banned

The owner's email got banned likely due to signups/automation patterns on adversarial platforms. For legit transactional email use free API tiers, never a personal Gmail:
- **Resend** — 100 emails/day free, simple REST API.
- **Brevo** — 300 emails/day free.
- Both require domain verification (SPF/DKIM) for deliverability.

## 5b. eBay as a marketplace alternative

- eBay has **official free developer APIs** (Sell / Inventory / Browse / Trading) via the eBay Developers Program — free keys, generous call limits for a new seller. This is the only major marketplace here that legally supports the automation model.
- Caveat: new accounts can face temporary API application restrictions; manual listing via **Seller Hub** (free forever) is the fallback while access is granted.
- Selling fees apply per sale (~10-13% + payment processing). Not "free", but no fixed monthly cost at low volume.
- Printify has a native eBay integration, so POD → eBay needs no custom fulfillment code.

## 6. Proposed architecture (POD route)

```
[design sources] → products.py (Printify API: upload art, create products)
       ↓
[storefront: WooCommerce/Shopify ← native Printify sync or API publish]
       ↓
order webhook (order:created) → fulfiller.py (auto send_to_production)
       ↓                       ↘ notifier.py (Telegram/Discord alert to owner)
social.py → Postiz API (auto-post new products to IG/TikTok/Pinterest...)
       ↘
email.py → Resend (shipping confirmations, if not handled by Printify/store)
```

All components: free tiers, official APIs, Python + FastAPI (webhook receiver).

## 7. Legal notes (Italy/EU)

- Occasional selling: fine as private individual. Regular commercial activity → partita IVA eventually (regime forfettario is the cheap entry path).
- POD products we design ourselves: no IP issues. Never print branded/celebrity/fan-art designs — that's the #1 POD ban + lawsuit vector.
- Distance-selling consumer rules: 14-day withdrawal right, must be stated in the shop.
