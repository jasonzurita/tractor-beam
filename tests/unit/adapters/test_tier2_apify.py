import json
from pathlib import Path

from sw_sourcing.adapters.tier2_apify import Tier2ApifyAdapter, normalize_item

FIXTURE = json.loads(
    (Path(__file__).parents[2] / "fixtures" / "apify_mercari_dataset.json").read_text()
)


def test_normalize_item_maps_core_fields() -> None:
    listing = normalize_item(
        FIXTURE[0], source="mercari", fetched_at="2026-07-06T00:00:00Z"
    )

    assert listing.source == "mercari"
    assert listing.listing_id == "m123456789"
    assert listing.price == 25.0
    assert listing.shipping == 4.0
    assert listing.buying_option == "fixed_price"
    assert listing.offers_accepted is True
    assert listing.returns_accepted is True
    assert listing.seller_feedback == 0.97
    assert listing.location == "Austin, TX"


def test_normalize_item_applies_conservative_defaults_when_fields_missing() -> None:
    listing = normalize_item(
        FIXTURE[1], source="mercari", fetched_at="2026-07-06T00:00:00Z"
    )

    assert listing.description == ""
    assert listing.shipping == 0.0
    assert listing.offers_accepted is False
    assert listing.returns_accepted is False
    assert listing.location == "unknown"


def test_fetch_normalizes_every_item_from_the_dataset() -> None:
    adapter = Tier2ApifyAdapter(source="mercari", run_dataset=lambda: FIXTURE)

    listings = adapter.fetch()

    assert len(listings) == 2
    assert all(listing.source == "mercari" for listing in listings)
