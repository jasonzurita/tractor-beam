"""Tier-2 managed-scraper adapter (Apify or similar actor output).

One generic adapter shared across tier-2 sources (Mercari, OfferUp,
shopGoodwill, estate-auction aggregators) -- normalizes whatever an actor
run returns into `Listing`. Exact field names are actor-specific in
practice; whichever actor backs a given source must be configured to emit
the shape this module expects. Public listing data only, no auto-messaging
(see CLAUDE.md ToS guardrails).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sw_sourcing.core.schema import Listing


def normalize_item(
    item: dict[str, Any], *, source: str, fetched_at: datetime
) -> Listing:
    """Map one actor result item to a `Listing`.

    Missing optional fields default conservatively (no offers/returns
    assumed, unknown location) rather than guessing in the adapter's favor.
    """
    return Listing(
        source=source,
        listing_id=str(item["listingId"]),
        url=item["url"],
        title=item["title"],
        description=item.get("description", ""),
        price=float(item["price"]),
        shipping=float(item.get("shipping", 0.0)),
        buying_option="auction" if item.get("isAuction", False) else "fixed_price",
        offers_accepted=bool(item.get("offersAccepted", False)),
        returns_accepted=bool(item.get("returnsAccepted", False)),
        seller_feedback=float(item.get("sellerFeedback", 0.0)),
        location=item.get("location") or "unknown",
        images=item.get("images", []),
        fetched_at=fetched_at,
    )


class Tier2ApifyAdapter:
    """Fetches one actor run's dataset items and normalizes them.

    `run_dataset` is injected -- typically a thin call to the Apify client's
    `dataset().list_items()` -- so this class never touches the network
    itself and tests never touch it either.
    """

    def __init__(
        self, *, source: str, run_dataset: Callable[[], list[dict[str, Any]]]
    ) -> None:
        self._source = source
        self._run_dataset = run_dataset

    def fetch(self, *, offset: int = 0) -> list[Listing]:
        # No pagination exposed through the injected callable -- one run
        # already returns the whole dataset.
        if offset > 0:
            return []
        items = self._run_dataset()
        fetched_at = datetime.now(UTC)
        return [
            normalize_item(item, source=self._source, fetched_at=fetched_at)
            for item in items
        ]
