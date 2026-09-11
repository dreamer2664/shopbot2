"""Printify live-provider tests against a fake HTTP session."""
import pytest

from shopbot.providers.base import Address, Order, OrderLine, PermanentError, Product, TransientError
from shopbot.providers.printify import PrintifyProvider, _to_variant
from shopbot.providers.mock import sample_design
from fakes import FakeResponse, FakeSession


def provider(session) -> PrintifyProvider:
    return PrintifyProvider(token="tok", shop_id="123", session=session)


def test_variant_mapping_normalises_id_and_cents():
    v = _to_variant({"id": 51508, "title": "M / Black",
                     "options": {"color": "Black", "size": "M"}, "cost": 1234})
    assert v.variant_id == "51508"        # int -> str
    assert v.cost == 12.34                # cents -> dollars
    assert v.size == "M / Black"
    assert v.color == "Black"


def test_variant_mapping_survives_missing_options():
    v = _to_variant({"id": 1, "cost": 500})
    assert v.size == "" and v.color == "" and v.cost == 5.0


def test_upload_artwork_posts_to_shop_images_and_returns_id():
    s = FakeSession(responses=[FakeResponse(200, {"id": 998877})])
    up = provider(s).upload_artwork(sample_design())
    assert up == "998877"
    assert s.last()["url"].endswith("/v1/shops/123/images.json")
    assert s.last()["headers"]["Authorization"] == "Bearer tok"


def test_create_product_parses_variants_from_response():
    s = FakeSession(responses=[FakeResponse(200, {
        "id": 42, "variants": [
            {"id": 1001, "title": "S", "cost": 800, "options": {"color": "black"}},
            {"id": 1002, "title": "M", "cost": 825, "options": {"color": "black"}},
        ]})])
    p = Product(design=sample_design(), blueprint_id="bp_123", print_provider_id=29)
    out = provider(s).create_product(p, "998877")
    assert out.printify_product_id == "42"
    assert [v.variant_id for v in out.variants] == ["1001", "1002"]
    assert out.variants[1].cost == 8.25
    body = s.last()["json"]
    assert body["blueprint_id"] == 123          # parsed out of "bp_123"
    assert body["images"] == [{"id": "998877", "position": "front"}]


def test_set_prices_sends_integer_cents():
    s = FakeSession(responses=[FakeResponse(200, {})])
    p = Product(design=sample_design(), blueprint_id="b", print_provider_id=1)
    p.printify_product_id = "42"
    provider(s).set_prices(p, {"1001": 19.99, "1002": 20.99})
    sent = {v["id"]: v["price"] for v in s.last()["json"]["variants"]}
    assert sent == {1001: 1999, 1002: 2099}     # dollars -> cents, no floats
    assert s.last()["method"] == "PUT"


def test_submit_order_payload_shape():
    s = FakeSession(responses=[FakeResponse(200, {"id": 777})])
    order = Order("ord_1",
                  [OrderLine("42", "1001", 2)],
                  Address("Mario", "Rossi", "m@e.com", "Via Roma 1", "Milan",
                          "20121", "IT", phone="333"))
    r = provider(s).submit(order)
    assert r.status == "sent" and r.provider_order_id == "777"
    body = s.last()["json"]
    assert body["external_id"] == "ord_1"                  # dedupe key
    assert body["line_items"] == [{"product_id": "42", "variant_id": 1001,
                                   "quantity": 2}]
    assert body["send_shipping_notification"] is True
    assert body["address_to"]["country"] == "IT"
    assert body["address_to"]["zip"] == "20121"


@pytest.mark.parametrize("status,expected", [
    (429, TransientError), (500, TransientError), (503, TransientError),
    (401, PermanentError), (400, PermanentError), (404, PermanentError),
])
def test_http_status_maps_to_error_class(status, expected):
    s = FakeSession(responses=[FakeResponse(status, {"error": "x"})])
    with pytest.raises(expected):
        provider(s).list_blueprints()


def test_network_exception_is_transient():
    s = FakeSession(responses=[ConnectionError("dns fail")])
    with pytest.raises(TransientError):
        provider(s).list_blueprints()


def test_missing_id_in_response_is_permanent():
    s = FakeSession(responses=[FakeResponse(200, {"nope": True})])
    order = Order("o", [OrderLine("1", "1", 1)],
                  Address("a", "b", "e", "s", "c", "z", "IT"))
    with pytest.raises(PermanentError):
        provider(s).submit(order)
