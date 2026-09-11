"""CLI entrypoint.

    python -m shopbot.cli demo     # full end-to-end run against mock providers
    python -m shopbot.cli config   # show what mode we'd run in (never prints secrets)
"""
from __future__ import annotations

import sys

from .config import Config
from .pipeline.products import launch_product
from .pipeline.orders import process_orders
from .providers.mock import MockProviders, sample_design


def demo() -> int:
    """Simulate the whole business loop with zero network access."""
    p = MockProviders.build()
    cfg = Config.mock()

    print("== shopbot demo (dry run) ==")
    blueprints = p.supplier.list_blueprints()
    print(f"supplier blueprints: {[b['title'] for b in blueprints]}")

    # 1) Launch two designs
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

    # 2) Inject a bad design to show graceful failure
    p.supplier.reject_slugs.add("branded-logo-tee")
    bad = launch_product(sample_design("branded-logo-tee"), "bp_tshirt",
                         supplier=p.supplier, store=p.store, social=p.social,
                         cfg=cfg)
    print(f"[{'OK ' if bad.success else 'ERR'}] branded-logo-tee: {bad.error}")

    # 3) Simulate customer orders (including a duplicate webhook replay
    #    and one invalid address)
    if launched:
        o1 = p.store.add_order(launched[0], qty=2)
        o2 = p.store.add_order(launched[-1], qty=1)
        o2_dup = p.store.add_order(launched[0], qty=1, order_id=o1.order_id)
        o3 = p.store.add_order(launched[0], qty=1)
        o3.ship_to.zip = ""  # invalid
        p.store.pending_orders.extend([o1, o2, o2_dup, o3])

        # transient supplier outage on first attempt of the batch
        p.fulfiller.fail_times = 1
        batch = process_orders(store=p.store, fulfiller=p.fulfiller,
                               notifier=p.notifier)
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
        "printify": bool(cfg.printify_token),
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


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "demo"
    if cmd == "demo":
        return demo()
    if cmd == "config":
        return show_config()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
