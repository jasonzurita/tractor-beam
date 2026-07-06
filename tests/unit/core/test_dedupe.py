from pathlib import Path

from sw_sourcing.core.dedupe import Dedupe
from sw_sourcing.storage.db import Database
from tests.unit.factories import make_listing


def make_dedupe(tmp_path: Path) -> Dedupe:
    return Dedupe(Database(tmp_path / "test.db"))


def test_unseen_listing_is_new(tmp_path: Path) -> None:
    dedupe = make_dedupe(tmp_path)
    assert dedupe.is_new(make_listing())


def test_marking_processed_makes_it_no_longer_new(tmp_path: Path) -> None:
    dedupe = make_dedupe(tmp_path)
    listing = make_listing()

    dedupe.mark_processed(listing, seen_at="2026-07-06T00:00:00Z")

    assert not dedupe.is_new(listing)


def test_dedupe_is_scoped_per_source_and_listing_id(tmp_path: Path) -> None:
    dedupe = make_dedupe(tmp_path)
    dedupe.mark_processed(
        make_listing(source="ebay", listing_id="1"), seen_at="2026-07-06T00:00:00Z"
    )

    assert dedupe.is_new(make_listing(source="mercari", listing_id="1"))
    assert dedupe.is_new(make_listing(source="ebay", listing_id="2"))
