"""CLI entrypoint.

    python -m shopbot.cli demo              # end-to-end run against mocks
    python -m shopbot.cli config            # show mode + which creds are present
    python -m shopbot.cli launch DESIGNS.csv  # launch products from a CSV
    python -m shopbot.cli poll              # one-shot: fetch + fulfill new orders
    python -m shopbot.cli report [YYYY-MM-DD] # revenue/order summary from ledger
    python -m shopbot.cli serve             # run the webhook receiver

CSV columns: slug,title,description,image_url,tags (space-separated),blueprint
"""
from __future__ import annotations

import csv
import sys

from .config import Config
from .pipeline.ledger import Ledger
from .pipeline.orders import process_orders
from .pipeline.products import launch_product
from .pipeline.state import Checkpoint
from .providers.base import Design
from .providers.factory import build_providers
from .providers.mock import MockProviders, sample_design


def _ledger() -> Ledger:
    return Ledger("data/ledger.jsonl")


def demo() -> int:
    """Simulate the whole business loop with zero network access."""
    p = MockProviders.build()
    cfg = Config.mock()

    print("== shopbot demo (dry run) ==")
    blueprints = p.supplier.list_blueprints()
    print(f"supplier blueprints: {[b['title'] for b in blueprints]}")

    designs = [
        (sample_design("milano-skyline"), "bp_tshirt"),
        (sample_design("duomo-lineart", title="Duomo Line Art Mug",
                       description="Single-line duomo drawing.",
                       tags=["milan", "duomo", "lineart"]), "bp_mug"),
    ]
    launched = []
    for design, bp in designs:
        res = launch_product(design, bp, supplier=p.supplier, store=p.store,
                             social=p.social, cfg=cfg)
        status = "OK " if res.success else "ERR"
        price = res.product.retail_price if res.product else None
        print(f"[{status}] {design.slug}: steps={res.steps_done} "
              f"price=${price} error={res.error}")
        if res.success:
            launched.append(res.product)

    p.supplier.reject_slugs.add("branded-logo-tee")
    bad = launch_product(sample_design("branded-logo-tee"), "bp_tshirt",
                         supplier=p.supplier, store=p.store, social=p.social,
                         cfg=cfg)
    print(f"[{'OK ' if bad.success else 'ERR'}] branded-logo-tee: {bad.error}")

    if launched:
        o1 = p.store.add_order(launched[0], qty=2)
        o2 = p.store.add_order(launched[-1], qty=1)
        o2_dup = p.store.add_order(launched[0], qty=1, order_id=o1.order_id)
        o3 = p.store.add_order(launched[0], qty=1)
        o3.ship_to.zip = ""  # invalid
        p.store.pending_orders.extend([o1, o2, o2_dup, o3])

        p.fulfiller.fail_times = 1  # transient supplier outage
        batch = process_orders(store=p.store, fulfiller=p.fulfiller,
                               notifier=p.notifier, notify_on_sent=True)
        print(f"orders: sent={batch.sent} duplicates={batch.duplicates} "
              f"failed={batch.failed}")

    print("\n-- owner notifications --")
    for m in p.notifier.owner_messages:
        print(" *", m)
    print("\n-- social posts --")
    for post in p.social.posts:
        print(f" * {post['id']}: {post['text'].splitlines()[0]} "
              f"-> {post['networks']}")
    print("\ndemo complete: no network calls were made.")
    return 0


def show_config() -> int:
    cfg = Config.from_env()
    print(f"mode: {'DRY RUN (mock providers)' if cfg.dry_run else 'LIVE'}")
    present = {
        "printify": bool(cfg.printify_token and cfg.printify_shop_id),
        "shopify": bool(cfg.shopify_token and cfg.shopify_domain),
        "telegram": bool(cfg.telegram_token and cfg.telegram_chat_id),
        "postiz": bool(cfg.postiz_url and cfg.postiz_key),
        "resend": bool(cfg.resend_key),
    }
    for name, ok in present.items():
        print(f"  {name:10s} {'configured' if ok else 'missing'}")
    if cfg.dry_run:
        print("\nFill .env (see .env.example) to switch to LIVE mode.")
    return 0


def launch_csv(path: str) -> int:
    """Launch every design in a CSV. Works in dry-run (mocks) and live."""
    cfg = Config.from_env()
    providers = build_providers(cfg)
    ledger = _ledger()
    mode = "DRY RUN" if cfg.dry_run else "LIVE"
    print(f"== launching from {path} ({mode}) ==")
    failures = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            design = Design(
                slug=row["slug"].strip(),
                title=row["title"].strip(),
                description=row.get("description", "").strip(),
                image_path=row["image_url"].strip(),
                tags=(row.get("tags") or "").split(),
            )
            res = launch_product(design, row["blueprint"].strip(),
                                 supplier=providers.supplier,
                                 store=providers.store,
                                 social=providers.social, cfg=cfg,
                                 sleep=providers.sleep)
            ledger.record_launch(design.slug, res.success,
                                 detail={"steps": res.steps_done,
                                         "price": res.product.retail_price
                                         if res.product else None},
                                 error=res.error)
            print(f"[{'OK ' if res.success else 'ERR'}] {design.slug}: {res.error or res.steps_done}")
            if not res.success:
                failures += 1
                if providers.notifier:
                    providers.notifier.notify_owner(
                        f"launch failed for {design.slug}: {res.error}")
    print(f"done: {failures} failure(s)")
    return 1 if failures else 0


def poll() -> int:
    """One-shot order processing with checkpoint + ledger (cron-friendly)."""
    cfg = Config.from_env()
    providers = build_providers(cfg)
    ledger, cp = _ledger(), Checkpoint()
    since = cp.load_last_run()
    batch = process_orders(store=providers.store, fulfiller=providers.fulfiller,
                           notifier=providers.notifier, since=since,
                           sleep=providers.sleep,
                           expected_currency=cfg.pricing_currency)
    for r in batch.results:
        ledger.record_fulfillment(r)
    cp.save_last_run()
    print(f"polled since {since.isoformat()}: sent={batch.sent} "
          f"duplicates={batch.duplicates} failed={batch.failed}")
    return 0


def report(day: str | None = None) -> int:
    s = _ledger().daily_summary(day)
    print(f"== shopbot report ({s['day']}) ==")
    print(f"orders sent to production: {s['sent']}")
    print(f"duplicate replays absorbed: {s['duplicates']}")
    print(f"failed (need attention):    {s['failed']}")
    print(f"revenue (as charged):       {s['revenue']} "
          f"{'/'.join(s['currencies']) if s['currencies'] else ''}")
    return 0


def serve() -> int:
    import logging
    from .webhook import serve as serve_webhook
    logging.basicConfig(level=logging.INFO)
    serve_webhook()
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "demo"
    if cmd == "demo":
        return demo()
    if cmd == "config":
        return show_config()
    if cmd == "launch" and len(argv) > 2:
        return launch_csv(argv[2])
    if cmd == "poll":
        return poll()
    if cmd == "report":
        return report(argv[2] if len(argv) > 2 else None)
    if cmd == "serve":
        return serve()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
