from datetime import UTC, datetime
from pathlib import Path

import httpx

from sw_sourcing.adapters.craigslist import CraigslistAdapter, parse_search_rss

FIXTURE = (
    Path(__file__).parents[2] / "fixtures" / "craigslist_search_rss.xml"
).read_text()

FETCHED_AT = datetime(2026, 7, 22, tzinfo=UTC)


def parse() -> list:
    return parse_search_rss(FIXTURE, fetched_at=FETCHED_AT, site="longisland")


def test_parse_maps_core_fields_of_a_priced_listing() -> None:
    listing = next(x for x in parse() if x.listing_id == "7891234567")

    assert listing.source == "craigslist"
    assert str(listing.url).endswith("7891234567.html")
    assert "Kenner Star Wars Figure Lot" in listing.title
    assert listing.price == 50.00
    assert listing.shipping == 0.0  # local pickup, no shipping
    assert listing.buying_option == "best_offer"
    assert listing.offers_accepted is True  # haggle-friendly -> negotiate-eligible
    assert listing.returns_accepted is False
    assert listing.location == "Merrick"
    assert [str(i) for i in listing.images] == [
        "https://images.craigslist.org/00X0X_merrick_600x450.jpg"
    ]


def test_parse_reads_a_price_with_a_thousands_separator() -> None:
    listing = next(x for x in parse() if x.listing_id == "7891234568")

    assert listing.price == 1200.00
    assert listing.location == "Huntington"


def test_parse_skips_a_listing_with_no_parseable_price() -> None:
    # A missing price can't be defaulted to $0 without manufacturing a fake
    # "great deal" in the cost math -- such listings are dropped, not guessed.
    ids = {listing.listing_id for listing in parse()}

    assert "7891234569" not in ids


def test_parse_returns_only_the_priced_listings() -> None:
    assert {listing.listing_id for listing in parse()} == {
        "7891234567",
        "7891234568",
    }


def test_fetch_runs_every_query_and_category_and_unions_results() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, text=FIXTURE)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://longisland.craigslist.org",
    )
    adapter = CraigslistAdapter(
        site="longisland",
        queries=["vintage star wars", "kenner star wars"],
        categories=["sss", "clt"],
        client=client,
    )

    listings = adapter.fetch()

    # 2 queries x 2 categories = 4 feed requests.
    assert len(captured) == 4
    assert {req.url.params["query"] for req in captured} == {
        "vintage star wars",
        "kenner star wars",
    }
    assert {req.url.params["format"] for req in captured} == {"rss"}
    # All four feeds return the same fixture -> deduped down to its two
    # priced listings, not 4x that.
    assert {listing.listing_id for listing in listings} == {
        "7891234567",
        "7891234568",
    }


def test_fetch_sorts_by_date_so_repeat_scans_see_fresh_inventory() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, text=FIXTURE)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://longisland.craigslist.org",
    )
    adapter = CraigslistAdapter(
        site="longisland",
        queries=["vintage star wars"],
        categories=["sss"],
        client=client,
    )

    adapter.fetch()

    assert captured[0].url.params["sort"] == "date"


def test_fetch_returns_nothing_for_a_paged_request() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text=FIXTURE)

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://longisland.craigslist.org",
    )
    adapter = CraigslistAdapter(
        site="longisland",
        queries=["vintage star wars"],
        categories=["sss"],
        client=client,
    )

    assert adapter.fetch(offset=25) == []
    assert calls == []  # no network call for a paged request
