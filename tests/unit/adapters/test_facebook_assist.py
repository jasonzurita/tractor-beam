import json
from pathlib import Path

from sw_sourcing.adapters.facebook_assist import FacebookAssistAdapter, normalize_item

VALID_ITEM = {
    "listing_id": "fb123",
    "url": "https://www.facebook.com/marketplace/item/123456789/",
    "title": "Vintage Kenner Star Wars carded figure",
    "description": "Never opened, all original",
    "price": 45.0,
    "images": ["https://scontent.fbcdn.net/abc.jpg"],
}


def test_normalize_item_defaults_to_negotiable_since_fb_has_no_formal_offer_flag() -> (
    None
):
    listing = normalize_item(VALID_ITEM, fetched_at="2026-07-06T00:00:00Z")

    assert listing.source == "facebook"
    assert listing.listing_id == "fb123"
    assert listing.buying_option == "best_offer"
    assert listing.offers_accepted is True
    assert listing.returns_accepted is False


def test_normalize_item_respects_explicit_overrides() -> None:
    item = {**VALID_ITEM, "returns_accepted": True, "seller_feedback": 0.9}

    listing = normalize_item(item, fetched_at="2026-07-06T00:00:00Z")

    assert listing.returns_accepted is True
    assert listing.seller_feedback == 0.9


def write_inbox_file(inbox_dir: Path, name: str, content: str) -> None:
    (inbox_dir / name).write_text(content)


def test_fetch_drains_every_json_file_in_the_inbox(tmp_path: Path) -> None:
    write_inbox_file(tmp_path, "1.json", json.dumps(VALID_ITEM))
    write_inbox_file(
        tmp_path, "2.json", json.dumps({**VALID_ITEM, "listing_id": "fb456"})
    )

    listings = FacebookAssistAdapter(tmp_path).fetch()

    assert {listing.listing_id for listing in listings} == {"fb123", "fb456"}


def test_fetch_deletes_files_it_successfully_processed(tmp_path: Path) -> None:
    write_inbox_file(tmp_path, "1.json", json.dumps(VALID_ITEM))

    FacebookAssistAdapter(tmp_path).fetch()

    assert list(tmp_path.glob("*.json")) == []


def test_fetch_skips_malformed_files_but_keeps_processing_the_rest(
    tmp_path: Path,
) -> None:
    write_inbox_file(tmp_path, "1.json", json.dumps(VALID_ITEM))
    write_inbox_file(tmp_path, "2.json", "not valid json")

    listings = FacebookAssistAdapter(tmp_path).fetch()

    assert len(listings) == 1
    assert listings[0].listing_id == "fb123"


def test_fetch_leaves_malformed_files_in_place_for_a_human_to_fix(
    tmp_path: Path,
) -> None:
    write_inbox_file(tmp_path, "2.json", "not valid json")

    FacebookAssistAdapter(tmp_path).fetch()

    assert (tmp_path / "2.json").exists()
