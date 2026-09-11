"""Chaos training: hundreds of randomized "business days" with injected
failures, asserting GLOBAL invariants that must hold no matter what breaks.

This is the "train it more" suite. Invariants:
  I1. An order is never sent to production twice (unique order ids in submitted).
  I2. Only valid orders (passing validate_order) ever reach the fulfiller.
  I3. Social posts never outnumber successful store listings.
  I4. A product is never listed on the store without a supplier draft+publish.
  I5. The ledger on disk agrees with what the fulfiller actually accepted.
  I6. Nothing in the pipeline raises an unexpected exception type — failures
      surface as results/errors, never crashes.
"""
import random

import pytest

from shopbot.config import Config
from shopbot.pipeline.ledger import Ledger
from shopbot.pipeline.orders import handle_order, validate_order
from shopbot.pipeline.products import launch_product
from shopbot.providers.base import TransientError, PermanentError, OrderLine
from shopbot.providers.mock import MockProviders, sample_design

BLUEPRINTS = ["bp_tshirt", "bp_mug", "bp_sticker"]


def one_business_day(seed: int, ledger_path: str) -> dict:
    rng = random.Random(seed)
    p = MockProviders.build()
    cfg = Config.mock()
    ledger = Ledger(ledger_path)

    # Random outage injection
    p.supplier.fail_create_times = rng.choice([0, 0, 1, 2, 3, 99])
    p.social.fail_times = rng.choice([0, 0, 1, 99])
    p.fulfiller.fail_times = rng.choice([0, 0, 1, 2, 99])
    if rng.random() < 0.2:
        p.supplier.reject_slugs.add(f"d{seed}_2")  # one "IP rejected" design

    # Launch a random catalog
    products = []
    n_designs = rng.randint(1, 5)
    for i in range(n_designs):
        res = launch_product(sample_design(f"d{seed}_{i}"),
                             rng.choice(BLUEPRINTS),
                             supplier=p.supplier, store=p.store,
                             social=p.social, cfg=cfg)
        if res.success:
            products.append(res.product)

    # Fabricate orders: valid, duplicates, invalid, empty lines, weird qty
    orders = []
    for _ in range(rng.randint(0, 8)):
        kind = rng.random()
        if products and kind < 0.55:
            orders.append(p.store.add_order(rng.choice(products),
                                            qty=rng.randint(1, 3)))
        elif products and kind < 0.75 and orders:
            src = rng.choice([o for o in orders] or orders)
            orders.append(p.store.add_order(products[0], qty=1,
                                            order_id=src.order_id))  # replay
        elif kind < 0.85:
            bad = p.store.add_order(products[0] if products else None) \
                if products else None
            if bad is not None:
                bad.ship_to.zip = ""            # invalid address
                orders.append(bad)
        elif kind < 0.95 and products:
            ghost = p.store.add_order(products[0])
            ghost.lines = [OrderLine("999", "", 1)]  # missing variant id
            orders.append(ghost)
        # else: skip

    results = []
    for o in orders:
        r = handle_order(o, fulfiller=p.fulfiller, notifier=p.notifier,
                         expected_currency="USD")
        ledger.record_fulfillment(r, o)
        results.append((o, r))

    return {"p": p, "products": products, "orders": orders,
            "results": results, "ledger": ledger}


@pytest.fixture()
def ledger_file(tmp_path):
    return str(tmp_path / "ledger.jsonl")


@pytest.mark.parametrize("seed", range(120))
def test_invariants_under_chaos(seed, ledger_file):
    day = one_business_day(seed, ledger_file)
    p = day["p"]

    # I1: no double printing
    assert len(p.fulfiller.submitted) == len(set(p.fulfiller.submitted))
    sent_ids = [r.order_id for _, r in day["results"] if r.status == "sent"]
    assert len(sent_ids) == len(set(sent_ids))

    # I2: only valid orders were submitted
    for oid, order in p.fulfiller.submitted.items():
        assert validate_order(order) is None, f"invalid order {oid} was fulfilled"

    # I3: social posts <= successful launches
    assert len(p.social.posts) <= len(day["products"])

    # I4: every store listing has a published supplier draft
    for sid, prod in p.store.listings.items():
        assert prod.printify_product_id in p.supplier.published

    # I5: ledger matches fulfiller ground truth
    ledger_sent = day["ledger"].fulfilled_order_ids()
    assert ledger_sent == set(p.fulfiller.submitted)
    summary = day["ledger"].daily_summary()
    assert summary["sent"] == len(sent_ids)
    assert summary["sent"] + summary["failed"] == sum(
        1 for _, r in day["results"] if r.status != "duplicate")

    # I6: no unexpected exceptions escaped (we got here => none raised);
    # also every failed result carries a reason
    for _, r in day["results"]:
        if r.status == "failed":
            assert r.error


def test_chaos_ledger_survives_torn_last_line(ledger_file):
    """Power-cut simulation: a half-written JSON line must not kill reporting."""
    day = one_business_day(0, ledger_file)
    with open(ledger_file, "a") as f:
        f.write('{"v": 1, "kind": "fulfillment", "order_i')  # torn line
    summary = day["ledger"].daily_summary()   # must not raise
    assert summary["sent"] == len(day["ledger"].fulfilled_order_ids() &
                                    set(p_id for p_id in
                                        day["p"].fulfiller.submitted))
