"""Product launch pipeline tests (mock providers, no network)."""
from shopbot.config import Config
from shopbot.pipeline.products import launch_product
from shopbot.providers.mock import MockProviders, sample_design


def _launch(p, slug="tee", bp="bp_tshirt", **kw):
    return launch_product(sample_design(slug), bp, supplier=p.supplier,
                          store=p.store, social=p.social, cfg=Config.mock(), **kw)


def test_happy_path_full_sequence():
    p = MockProviders.build()
    res = _launch(p)
    assert res.success, res.error
    assert res.steps_done == ["upload", "create_draft", "price",
                              "publish_supplier", "upsert_store", "social_post"]
    prod = res.product
    assert prod.printify_product_id in p.supplier.published
    assert prod.store_product_id in p.store.listings
    assert prod.retail_price is not None and prod.retail_price > prod.variants[0].cost + 8.0
    # variants were created and priced
    assert len(prod.variants) == 4  # t-shirt blueprint has 4 sizes
    assert len(p.supplier.prices) == 4


def test_socials_never_fire_if_listing_failed():
    p = MockProviders.build()
    # supplier rejects the design permanently at upload
    p.supplier.reject_slugs.add("tee")
    res = _launch(p)
    assert not res.success
    assert res.error.startswith("permanent")
    assert p.social.posts == []
    assert p.store.listings == {}


def test_transient_supplier_outage_is_retried():
    p = MockProviders.build()
    p.supplier.fail_create_times = 2  # fails twice, succeeds on 3rd attempt
    res = _launch(p)
    assert res.success
    assert res.steps_done[-1] == "social_post"


def test_transient_outage_beyond_retry_budget_fails_cleanly():
    p = MockProviders.build()
    p.supplier.fail_create_times = 99
    res = _launch(p)
    assert not res.success
    assert "retries exhausted" in res.error
    assert p.store.listings == {}   # nothing half-published
    assert p.social.posts == []     # nothing advertised


def test_social_outage_does_not_block_the_listing():
    # The product must still be buyable even if the poster is down.
    p = MockProviders.build()
    p.social.fail_times = 99
    res = _launch(p)
    assert not res.success           # launch reports failure...
    assert res.product.store_product_id is not None  # ...but listing is live
    assert res.steps_done[-1] == "upsert_store"


def test_two_designs_get_distinct_ids():
    p = MockProviders.build()
    r1 = _launch(p, slug="a")
    r2 = _launch(p, slug="b", bp="bp_mug")
    assert r1.success and r2.success
    assert r1.product.printify_product_id != r2.product.printify_product_id
    assert r1.product.store_product_id != r2.product.store_product_id
    assert len(p.supplier.published) == 2
    assert len(p.social.posts) == 2
