import base64
import json
from pathlib import Path

import httpx

from sw_sourcing.adapters.ebay import EbayAdapter, get_ebay_access_token, normalize_item

FIXTURE = json.loads(
    (
        Path(__file__).parents[2] / "fixtures" / "ebay_item_summary_search.json"
    ).read_text()
)


def test_normalize_item_maps_core_fields() -> None:
    listing = normalize_item(
        FIXTURE["itemSummaries"][0], fetched_at="2026-07-06T00:00:00Z"
    )

    assert listing.source == "ebay"
    assert listing.listing_id == "v1|110599777099|0"
    assert listing.title == "Vintage Kenner Star Wars Loose Figure Lot of 12"
    assert listing.price == 60.00
    assert listing.shipping == 8.50
    assert listing.buying_option == "best_offer"
    assert listing.offers_accepted is True
    assert listing.returns_accepted is True
    assert listing.seller_feedback == 0.995
    assert listing.location == "OH, US"
    assert len(listing.images) == 2


def test_normalize_item_falls_back_to_empty_description_when_missing() -> None:
    listing = normalize_item(
        FIXTURE["itemSummaries"][1], fetched_at="2026-07-06T00:00:00Z"
    )

    assert listing.description == ""


def test_normalize_item_maps_auction_only_listing() -> None:
    listing = normalize_item(
        FIXTURE["itemSummaries"][1], fetched_at="2026-07-06T00:00:00Z"
    )

    assert listing.buying_option == "auction"
    assert listing.offers_accepted is False
    assert listing.returns_accepted is False


def test_fetch_normalizes_every_item_in_the_search_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=FIXTURE)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://api.ebay.com")
    adapter = EbayAdapter(
        app_token="fake-token", query="vintage kenner star wars", client=client
    )

    listings = adapter.fetch()

    assert len(listings) == 2
    assert {listing.listing_id for listing in listings} == {
        "v1|110599777099|0",
        "v1|110599777100|0",
    }


def test_fetch_sends_the_bearer_token_and_marketplace_header() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=FIXTURE)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://api.ebay.com")
    adapter = EbayAdapter(
        app_token="fake-token", query="vintage kenner star wars", client=client
    )

    adapter.fetch()

    assert captured[0].headers["Authorization"] == "Bearer fake-token"
    assert captured[0].headers["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_US"


def test_fetch_sorts_by_newly_listed_so_repeat_scans_see_fresh_inventory() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=FIXTURE)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://api.ebay.com")
    adapter = EbayAdapter(
        app_token="fake-token", query="vintage kenner star wars", client=client
    )

    adapter.fetch()

    assert captured[0].url.params["sort"] == "newlyListed"


def test_fetch_passes_the_offset_through_to_the_search_request() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=FIXTURE)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://api.ebay.com")
    adapter = EbayAdapter(
        app_token="fake-token", query="vintage kenner star wars", client=client
    )

    adapter.fetch(offset=100)

    assert captured[0].url.params["offset"] == "100"


def test_fetch_defaults_offset_to_zero() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=FIXTURE)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://api.ebay.com")
    adapter = EbayAdapter(
        app_token="fake-token", query="vintage kenner star wars", client=client
    )

    adapter.fetch()

    assert captured[0].url.params["offset"] == "0"


def test_get_ebay_access_token_returns_the_token_from_the_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "fresh-token",
                "expires_in": 7200,
                "token_type": "Application Access Token",
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    token = get_ebay_access_token("app-id", "cert-id", client=client)

    assert token == "fresh-token"


def test_get_ebay_access_token_sends_client_credentials_grant_with_basic_auth() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"access_token": "fresh-token"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    get_ebay_access_token("app-id", "cert-id", client=client)

    request = captured[0]
    expected_credentials = base64.b64encode(b"app-id:cert-id").decode()
    assert request.headers["Authorization"] == f"Basic {expected_credentials}"
    assert b"grant_type=client_credentials" in request.content
