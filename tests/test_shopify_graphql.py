"""Shopify GraphQL Admin API provider tests (fake session, no network).

Covers the REST->GraphQL migration: productSet payload shape, gid parsing,
userErrors handling, order query mapping.
"""
import pytest

from shopbot.providers.base import PermanentError, Product
from shopbot.providers.mock import sample_design
from shopbot.providers.shopify import (ShopifyStorefront, _gid_id,
                                       _graphql_order_to_model)
from fakes import FakeResponse, FakeSession


def store(session, domain="mystore.myshopify.com"):
    return ShopifyStorefront(domain=domain, token="shpat_x", session=session)


def gql(data):
    return FakeResponse(200, {"data": data})


# ---- gid handling ----

def test_gid_id_parsing():
    assert _gid_id("gid://shopify/Order/5512345678") == "5512345678"
    assert _gid_id("gid://shopify/Product/1") == "1"
    assert _gid_id(None) == ""
    assert _gid_id("") == ""


# ---- productSet ----

def _product(price=24.99, published=True, image="https://cdn.example.com/a.png"):
    d = sample_design(image_path=image)
    p = Product(design=d, blueprint_id="b", print_provider_id=1)
    from shopbot.providers.base import Variant
    p.variants = [Variant("1001", "S", "black", 9.0),
                  Variant("1002", "M", "black", 9.25)]
    p.retail_price = price
    p.published = published
    p.printify_product_id = "pp1"
    return p


def test_product_set_payload_shape():
    s = FakeSession(responses=[gql({"productSet": {
        "product": {"id": "gid://shopify/Product/777"}, "userErrors": []}})])
    pid = store(s).upsert_product(_product())
    assert pid == "777"
    body = s.last()["json"]
    assert body["query"].strip().startswith("mutation productSet")
    inp = body["variables"]["input"]
    assert inp["status"] == "ACTIVE"
    assert inp["productType"] == "print-on-demand"
    assert [v["name"] for v in inp["productOptions"][0]["values"]] == ["S", "M"]
    assert inp["variants"][0]["price"] == "24.99"
    assert inp["files"][0]["originalSource"] == "https://cdn.example.com/a.png"


def test_unpublished_product_goes_as_draft():
    s = FakeSession(responses=[gql({"productSet": {
        "product": {"id": "gid://shopify/Product/1"}, "userErrors": []}})])
    store(s).upsert_product(_product(published=False))
    assert s.last()["json"]["variables"]["input"]["status"] == "DRAFT"


def test_duplicate_option_values_are_deduplicated():
    s = FakeSession(responses=[gql({"productSet": {
        "product": {"id": "gid://shopify/Product/2"}, "userErrors": []}})])
    p = _product()
    p.variants[1].size = "S"          # duplicate option value -> productSet error
    store(s).upsert_product(p)
    names = [v["name"] for v in
             s.last()["json"]["variables"]["input"]["productOptions"][0]["values"]]
    assert len(set(names)) == len(names)   # all unique
    assert names[0] == "S"


def test_local_image_path_not_sent_as_file():
    s = FakeSession(responses=[gql({"productSet": {
        "product": {"id": "gid://shopify/Product/3"}, "userErrors": []}})])
    store(s).upsert_product(_product(image="designs/local.png"))
    assert "files" not in s.last()["json"]["variables"]["input"]


def test_user_errors_raise_permanent():
    s = FakeSession(responses=[gql({"productSet": {
        "product": None,
        "userErrors": [{"field": ["title"], "message": "too long"}]}})])
    with pytest.raises(PermanentError, match="userErrors"):
        store(s).upsert_product(_product())


def test_graphql_errors_raise_permanent():
    s = FakeSession(responses=[FakeResponse(200, {
        "errors": [{"message": "Field 'productSet' doesn't exist"}]})])
    with pytest.raises(PermanentError, match="graphql errors"):
        store(s).upsert_product(_product())


def test_existing_store_id_short_circuits():
    s = FakeSession()
    p = _product()
    p.store_product_id = "999"
    assert store(s).upsert_product(p) == "999"
    assert s.calls == []


# ---- orders query ----

GRAPHQL_ORDER = {
    "id": "gid://shopify/Order/5512345678",
    "name": "#1001",
    "totalPrice": "49.98",
    "currencyCode": "EUR",
    "email": "buyer@example.com",
    "phone": None,
    "shippingAddress": {
        "firstName": "Giulia", "lastName": "Bianchi",
        "address1": "Via Torino 5", "city": "Milano", "zip": "20123",
        "countryCode": "it", "provinceCode": "MI", "phone": "+39 333 123",
    },
    "lineItems": {"nodes": [
        {"quantity": 2,
         "product": {"id": "gid://shopify/Product/111"},
         "variant": {"id": "gid://shopify/ProductVariant/222"}},
    ]},
}


def test_graphql_order_maps_to_model():
    o = _graphql_order_to_model(GRAPHQL_ORDER)
    assert o.order_id == "5512345678"
    assert o.lines[0].product_id == "111" and o.lines[0].variant_id == "222"
    assert o.lines[0].quantity == 2
    assert o.ship_to.country == "IT"          # normalised
    assert o.total == 49.98 and o.currency == "EUR"


def test_graphql_order_without_address_rejected():
    bad = dict(GRAPHQL_ORDER, shippingAddress=None)
    with pytest.raises(PermanentError):
        _graphql_order_to_model(bad)


def test_fetch_orders_sends_search_query():
    s = FakeSession(responses=[gql({"orders": {"nodes": [GRAPHQL_ORDER]}})])
    from datetime import datetime, timezone
    orders = store(s).fetch_orders_since(datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert len(orders) == 1 and orders[0].order_id == "5512345678"
    q = s.last()["json"]["variables"]["q"]
    assert "fulfillment_status:unfulfilled" in q
    assert "2026-09-01" in q


def test_bad_domain_rejected():
    with pytest.raises(PermanentError):
        ShopifyStorefront(domain="not-shopify.com", token="t",
                          session=FakeSession())
