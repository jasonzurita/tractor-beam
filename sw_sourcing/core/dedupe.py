"""Seen-listing check: a listing is processed and alerted at most once."""

from __future__ import annotations

from sw_sourcing.core.schema import Listing
from sw_sourcing.storage.db import Database


class Dedupe:
    def __init__(self, db: Database) -> None:
        self._db = db

    def is_new(self, listing: Listing) -> bool:
        return not self._db.has_seen(listing.source, listing.listing_id)

    def mark_processed(self, listing: Listing, *, seen_at: str) -> None:
        self._db.mark_seen(listing.source, listing.listing_id, seen_at=seen_at)
