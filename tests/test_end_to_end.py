"""End-to-end integration: design CSV -> launch -> orders -> ledger -> report.

This is the full business in one test, using the real CLI code paths against
mock providers. If this passes, the wiring between every module is correct.
"""
import csv
import os
from datetime import datetime, timezone

import pytest

from shopbot import cli
from shopbot.pipeline.analytics import best_sellers, build_report
from shopbot.pipeline.ledger import Ledger
from shopbot.pipeline.orders import process_orders
from shopbot.pipeline.products import launch_product
from shopbot.providers.base import Design
from shopbot.providers.mock import MockProviders


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """Run inside a temp cwd so data/ files never touch the real repo."""
    monkeypatch.chdir(tmp_path)
    csv_path = tmp_path / "designs.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["slug", "title", "description", "image_url", "tags",
                    "blueprint"])
        w.writerow(["milan-tee", "Milan Skyline Tee", "Original skyline art.",
                    "https://cdn.example.com/milan.png", "milan skyline art",
                    "bp_tshirt"])
        w.writerow(["duomo-mug", "Duomo Mug", "Single-line duomo.",
                    "https://cdn.example.com/duomo.png", "milan duomo",
                    "bp_mug"])
        w.writerow(["sticker-1", "Tram Sticker", "Vintage tram sticker.",
                    "designs/local.png", "milan tram", "bp_sticker"])
    return tmp_path, str(csv_path)


def test_full_business_lifecycle(workspace, capsys):
    tmp_path, csv_path = workspace

    # ---- 1. Launch catalog via the real CLI path ----
    assert cli.launch_csv(csv_path) == 0
    ledger = Ledger("data/ledger.jsonl")
    launches = [e for e in ledger.entries() if e.kind == "launch"]
    assert len(launches) == 3 and all(e.status == "ok" for e in launches)

    # ---- 2. Rebuild the same catalog in-process so we can fabricate orders ----
    p = MockProviders.build()
    from shopbot.config import Config
    products = []
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            design = Design(slug=row["slug"], title=row["title"],
                            description=row["description"],
                            image_path=row["image_url"],
                            tags=row["tags"].split())
            res = launch_product(design, row["blueprint"], supplier=p.supplier,
                                 store=p.store, social=p.social,
                                 cfg=Config.mock(),
                                 category={"bp_tshirt": "tshirt",
                                           "bp_mug": "mug",
                                           "bp_sticker": "sticker"}[row["blueprint"]])
            assert res.success, res.error
            products.append(res.product)

    # v2 pricing: every listing must clear the 20% survival floor net of fees
    from shopbot.knowledge import MARGIN_FLOOR_BEGINNER, FEE_RATE_DEFAULT
    from shopbot.pipeline.pricing import net_margin
    for prod in products:
        for v in prod.variants:
            prices = p.supplier.prices
            price = prices[v.variant_id]
            assert net_margin(price, v.cost, FEE_RATE_DEFAULT) >= \
                MARGIN_FLOOR_BEGINNER - 0.05, (prod.design.slug, price, v.cost)

    # ---- 3. A month of orders: bestseller + slow item + replays + failures ----
    orders = []
    for i in range(12):
        orders.append(p.store.add_order(products[0], qty=1))    # tee sells well
    for i in range(3):
        orders.append(p.store.add_order(products[1], qty=1))    # mug slower
    orders.append(p.store.add_order(products[0], qty=1,
                                    order_id=orders[0].order_id))  # replay
    bad = p.store.add_order(products[2], qty=1)
    bad.ship_to.city = ""                                       # invalid
    orders.append(bad)
    p.store.pending_orders = orders
    p.fulfiller.fail_times = 1                                  # one transient blip

    batch = process_orders(store=p.store, fulfiller=p.fulfiller,
                           notifier=p.notifier, expected_currency="USD")
    assert batch.sent == 15
    assert batch.duplicates == 1
    assert batch.failed == 1
    assert len(p.fulfiller.submitted) == 15          # never double-printed

    for r in batch.results:
        order = next((o for o in orders if o.order_id == r.order_id), None)
        ledger.record_fulfillment(r, order,
                                  cost=order.total * 0.4 if order else None,
                                  product_id=order.lines[0].product_id
                                  if order and order.lines else None)

    # ---- 4. Analytics reflects reality ----
    report = build_report(ledger)
    assert report.orders_sent == 15
    assert report.orders_failed == 1
    assert report.orders_duplicate == 1
    assert report.revenue > 0 and report.profit > 0
    top_id, top_stats = best_sellers(report, 1)[0]
    assert top_stats.units == 12                     # the tee is the bestseller

    # ---- 5. Owner was told about problems only ----
    alerts = p.notifier.owner_messages
    assert any("rejected" in m for m in alerts)      # the invalid address
    assert len(alerts) <= 3                          # ...and no sales spam

    # ---- 6. CLI report runs against the real ledger ----
    assert cli.report(None) == 0
    out = capsys.readouterr().out
    assert "orders sent to production: 15" in out


def test_launch_csv_reports_failures_and_stays_consistent(workspace, monkeypatch):
    tmp_path, _ = workspace
    bad_csv = tmp_path / "bad.csv"
    with open(bad_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["slug", "title", "description", "image_url", "tags",
                    "blueprint"])
        w.writerow(["ok-one", "Good Design", "Fine.", "https://x/y.png",
                    "art", "bp_tshirt"])
        w.writerow(["bad-one", "Rejected Design", "Nope.", "https://x/z.png",
                    "art", "bp_tshirt"])

    # make the mock supplier reject one design: patch the provider used by
    # the factory (dry-run path)
    from shopbot.providers import mock as mock_mod
    original = mock_mod.MockProductProvider.upload_artwork

    def patched(self, design):
        if design.slug == "bad-one":
            self.reject_slugs.add("bad-one")
        return original(self, design)

    monkeypatch.setattr(mock_mod.MockProductProvider, "upload_artwork", patched)
    rc = cli.launch_csv(str(bad_csv))
    assert rc == 1                       # non-zero: something failed

    ledger = Ledger("data/ledger.jsonl")
    launches = {e.detail["design"]: e.status for e in ledger.entries()
                if e.kind == "launch"}
    assert launches["ok-one"] == "ok"
    assert launches["bad-one"] == "failed"
