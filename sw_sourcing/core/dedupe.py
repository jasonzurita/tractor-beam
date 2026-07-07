"""Alert-level dedupe: a listing is re-alerted only if its outcome or price
has changed since the last alert for it -- a re-fetched, already-graded
listing at an unchanged price and outcome is not re-alerted."""

from __future__ import annotations

from sw_sourcing.core.schema import Listing
from sw_sourcing.storage.db import Database


class Dedupe:
    def __init__(self, db: Database) -> None:
        self._db = db

    def already_alerted(self, listing: Listing, *, outcome: str) -> bool:
        return self._db.has_alerted(
            listing.source, listing.listing_id, outcome=outcome, price=listing.price
        )
