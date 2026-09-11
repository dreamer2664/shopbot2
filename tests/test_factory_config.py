"""Factory + config tests: dry-run safety guarantees."""
import pytest

from shopbot.config import Config
from shopbot.providers.factory import build_providers
from shopbot.providers.mock import (MockFulfiller, MockNotifier,
                                    MockProductProvider, MockStorefront)


def test_defaults_to_dry_run():
    cfg = Config.from_env()
    assert cfg.dry_run is True
    p = build_providers(cfg)
    assert isinstance(p.supplier, MockProductProvider)
    assert isinstance(p.store, MockStorefront)
    assert isinstance(p.fulfiller, MockFulfiller)


def test_partial_credentials_stay_dry_run(monkeypatch):
    monkeypatch.setenv("PRINTIFY_API_TOKEN", "x")   # only supplier configured
    cfg = Config.from_env()
    assert cfg.dry_run is True, "half-configured must never go live"


def test_full_credentials_switch_to_live(monkeypatch):
    monkeypatch.setenv("PRINTIFY_API_TOKEN", "x")
    monkeypatch.setenv("PRINTIFY_SHOP_ID", "1")
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "mystore.myshopify.com")
    monkeypatch.setenv("SHOPIFY_ADMIN_TOKEN", "shpat_x")
    cfg = Config.from_env()
    assert cfg.dry_run is False
    p = build_providers(cfg)
    from shopbot.providers.printify import PrintifyProvider
    from shopbot.providers.shopify import ShopifyStorefront
    assert isinstance(p.supplier, PrintifyProvider)
    assert isinstance(p.store, ShopifyStorefront)
    assert p.fulfiller is p.supplier          # Printify fulfills its own orders
    # notifier/social stay None until their creds exist
    assert p.notifier is None and p.social is None


def test_bad_shopify_domain_rejected(monkeypatch):
    monkeypatch.setenv("PRINTIFY_API_TOKEN", "x")
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "evil.example.com")
    monkeypatch.setenv("SHOPIFY_ADMIN_TOKEN", "t")
    from shopbot.providers.base import PermanentError
    with pytest.raises(PermanentError):
        build_providers(Config.from_env())
