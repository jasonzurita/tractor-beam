import base64
import copy
import json
from pathlib import Path
from typing import Any

import httpx

from sw_sourcing.adapters.ebay import (
    EbayAdapter,
    get_ebay_access_token,
    is_still_listed,
    normalize_item,
)

FIXTURE = json.loads(
    (
        Path(__file__).parents[2] / "fixtures" / "ebay_item_summary_search.json"
    ).read_text()
)


def make_item(**overrides: Any) -> dict[str, Any]:
    item = copy.deepcopy(FIXTURE["itemSummaries"][0])
    item.update(overrides)
    return item


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


def test_normalize_item_falls_back_to_5_dollars_when_shipping_cost_is_absent() -> None:
    """Calculated-shipping listings often omit shippingCost entirely in the
    summary API (the real cost depends on buyer location) -- defaulting
    that to $0 would systematically underprice them, so an undetermined
    cost assumes a conservative flat $5 instead."""
    item = make_item(shippingOptions=[{}])
    listing = normalize_item(item, fetched_at="2026-07-06T00:00:00Z")

    assert listing.shipping == 5.00


def test_normalize_item_falls_back_to_5_dollars_when_no_shipping_options() -> None:
    item = make_item(shippingOptions=[])
    listing = normalize_item(item, fetched_at="2026-07-06T00:00:00Z")

    assert listing.shipping == 5.00


def test_normalize_item_uses_zero_only_when_shipping_cost_is_explicitly_present() -> (
    None
):
    item = make_item(
        shippingOptions=[{"shippingCost": {"value": "0.0", "currency": "USD"}}]
    )
    listing = normalize_item(item, fetched_at="2026-07-06T00:00:00Z")

    assert listing.shipping == 0.0


def test_fetch_normalizes_every_item_in_the_search_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=FIXTURE)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://api.ebay.com")
    adapter = EbayAdapter(
        app_token="fake-token", queries=["vintage kenner star wars"], client=client
    )

    listings = adapter.fetch()

    assert len(listings) == 2
    assert {listing.listing_id for listing in listings} == {
        "v1|110599777099|0",
        "v1|110599777100|0",
    }


def test_fetch_runs_every_query_and_unions_the_results() -> None:
    """Each configured query is a separate Browse API search; the adapter
    unions their results so widening the query list widens coverage."""
    captured_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["q"]
        captured_queries.append(query)
        # Each query returns a distinct listing id so the union is visible.
        item = make_item(itemId=f"id-for-{query}")
        return httpx.Response(200, json={"itemSummaries": [item]})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://api.ebay.com")
    adapter = EbayAdapter(
        app_token="fake-token",
        queries=["star wars figure lot", "kenner star wars weapon"],
        client=client,
    )

    listings = adapter.fetch()

    assert captured_queries == ["star wars figure lot", "kenner star wars weapon"]
    assert {listing.listing_id for listing in listings} == {
        "id-for-star wars figure lot",
        "id-for-kenner star wars weapon",
    }


def test_fetch_dedupes_a_listing_matched_by_more_than_one_query() -> None:
    """A lot whose title matches several queries must be handed downstream
    once, not once per query it happened to match."""

    def handler(request: httpx.Request) -> httpx.Response:
        # Every query returns the same single item.
        return httpx.Response(200, json={"itemSummaries": [make_item(itemId="dup")]})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://api.ebay.com")
    adapter = EbayAdapter(
        app_token="fake-token",
        queries=["query one", "query two", "query three"],
        client=client,
    )

    listings = adapter.fetch()

    assert [listing.listing_id for listing in listings] == ["dup"]


def test_fetch_returns_nothing_for_a_paged_request() -> None:
    """Several queries at 50 results each already far exceed a run's fresh
    analysis budget, so the adapter serves the newest page in one shot and
    treats any offset>0 request as exhausted -- the same one-shot contract
    the Facebook inbox adapter uses (see adapters/base.py)."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=FIXTURE)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://api.ebay.com")
    adapter = EbayAdapter(
        app_token="fake-token", queries=["vintage kenner star wars"], client=client
    )

    assert adapter.fetch(offset=50) == []
    assert calls == []  # no network call at all for a paged request


def test_fetch_sends_the_bearer_token_and_marketplace_header() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=FIXTURE)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://api.ebay.com")
    adapter = EbayAdapter(
        app_token="fake-token", queries=["vintage kenner star wars"], client=client
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
        app_token="fake-token", queries=["vintage kenner star wars"], client=client
    )

    adapter.fetch()

    assert captured[0].url.params["sort"] == "newlyListed"


def test_fetch_defaults_to_the_first_page_of_each_query() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=FIXTURE)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://api.ebay.com")
    adapter = EbayAdapter(
        app_token="fake-token", queries=["vintage kenner star wars"], client=client
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


def test_is_still_listed_true_when_in_stock() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "estimatedAvailabilities": [{"estimatedAvailabilityStatus": "IN_STOCK"}]
            },
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.ebay.com"
    )

    assert is_still_listed("v1|1|0", app_token="fake-token", client=client) is True


def test_is_still_listed_false_when_out_of_stock() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "estimatedAvailabilities": [
                    {"estimatedAvailabilityStatus": "OUT_OF_STOCK"}
                ]
            },
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.ebay.com"
    )

    assert is_still_listed("v1|1|0", app_token="fake-token", client=client) is False


def test_is_still_listed_false_when_the_listing_no_longer_exists() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.ebay.com"
    )

    assert is_still_listed("v1|1|0", app_token="fake-token", client=client) is False


def test_is_still_listed_fails_open_on_an_unexpected_error_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.ebay.com"
    )

    assert is_still_listed("v1|1|0", app_token="fake-token", client=client) is True


def test_is_still_listed_fails_open_on_a_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.ebay.com"
    )

    assert is_still_listed("v1|1|0", app_token="fake-token", client=client) is True


def test_is_still_listed_percent_encodes_the_item_id_in_the_path() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"estimatedAvailabilities": []})

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.ebay.com"
    )

    is_still_listed("v1|110599777099|0", app_token="fake-token", client=client)

    assert captured[0].url.raw_path == b"/buy/browse/v1/item/v1%7C110599777099%7C0"
    assert captured[0].headers["Authorization"] == "Bearer fake-token"


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
