from pathlib import Path

from sw_sourcing.core.dedupe import Dedupe
from sw_sourcing.core.schema import Listing
from sw_sourcing.storage.db import Database
from tests.unit.factories import make_listing


def make_db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


def record_alert_for(db: Database, listing: Listing, *, outcome: str = "buy") -> None:
    db.record_alert(
        source=listing.source,
        listing_id=listing.listing_id,
        title=listing.title,
        url=str(listing.url),
        image_url=None,
        outcome=outcome,
        cost_per_figure=None,
        target_grade_count=None,
        max_repro_risk=None,
        returns_accepted=False,
        suggested_offer=None,
        vision_notes=None,
        cost_per_weapon=None,
        price=listing.price,
        alerted_at="2026-07-06T00:00:00Z",
    )


def test_never_alerted_listing_is_not_already_alerted(tmp_path: Path) -> None:
    dedupe = Dedupe(make_db(tmp_path))
    assert not dedupe.already_alerted(make_listing(), outcome="buy")


def test_already_alerted_after_recording_the_same_outcome_and_price(
    tmp_path: Path,
) -> None:
    db = make_db(tmp_path)
    dedupe = Dedupe(db)
    listing = make_listing(price=10.0)
    record_alert_for(db, listing, outcome="buy")

    assert dedupe.already_alerted(listing, outcome="buy")


def test_a_price_drop_is_not_already_alerted(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    dedupe = Dedupe(db)
    listing = make_listing(listing_id="1", price=10.0)
    record_alert_for(db, listing, outcome="buy")

    cheaper = make_listing(listing_id="1", price=5.0)
    assert not dedupe.already_alerted(cheaper, outcome="buy")


def test_a_different_outcome_is_not_already_alerted(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    dedupe = Dedupe(db)
    listing = make_listing(price=10.0)
    record_alert_for(db, listing, outcome="review")

    assert not dedupe.already_alerted(listing, outcome="buy")


def test_dedupe_is_scoped_per_source(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    dedupe = Dedupe(db)
    listing = make_listing(source="ebay", listing_id="1", price=10.0)
    record_alert_for(db, listing, outcome="buy")

    other_source = make_listing(source="mercari", listing_id="1", price=10.0)
    assert not dedupe.already_alerted(other_source, outcome="buy")
