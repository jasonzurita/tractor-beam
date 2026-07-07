"""Facebook Marketplace browser-assist adapter (human-in-the-loop, v1).

No headless scraping and no auto-messaging sellers -- a browser extension
(out of scope for this codebase) forwards listings you're already viewing
by dropping one JSON file per listing into a local inbox directory. This
adapter only drains that inbox into `Listing` objects.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sw_sourcing.core.schema import Listing

logger = logging.getLogger(__name__)

# Facebook Marketplace has no formal buying-option or return-policy signal
# -- a human always negotiates over chat, so this is the pragmatic default.
_DEFAULT_BUYING_OPTION = "best_offer"


def normalize_item(item: dict[str, Any], *, fetched_at: datetime) -> Listing:
    """Map one browser-extension-forwarded listing to a `Listing`."""
    return Listing(
        source="facebook",
        listing_id=str(item["listing_id"]),
        url=item["url"],
        title=item["title"],
        description=item.get("description", ""),
        price=float(item["price"]),
        shipping=float(item.get("shipping", 0.0)),
        buying_option=item.get("buying_option", _DEFAULT_BUYING_OPTION),
        offers_accepted=bool(item.get("offers_accepted", True)),
        returns_accepted=bool(item.get("returns_accepted", False)),
        seller_feedback=float(item.get("seller_feedback", 0.0)),
        location=item.get("location") or "unknown",
        images=item.get("images", []),
        fetched_at=fetched_at,
    )


class FacebookAssistAdapter:
    """Drains an inbox directory of extension-forwarded listing JSON files.

    Each successfully processed file is deleted so a listing is only ever
    handed to the pipeline once, even across runs. A malformed file is
    logged and left in place for a human to fix, rather than losing the
    rest of the batch.
    """

    def __init__(self, inbox_dir: str | Path) -> None:
        self._inbox_dir = Path(inbox_dir)

    def fetch(self, *, offset: int = 0) -> list[Listing]:
        # No real pagination concept for a one-shot inbox drain -- the
        # first call already returns everything there was to see.
        if offset > 0:
            return []
        fetched_at = datetime.now(UTC)
        listings: list[Listing] = []
        for path in sorted(self._inbox_dir.glob("*.json")):
            try:
                item = json.loads(path.read_text())
                listing = normalize_item(item, fetched_at=fetched_at)
            except Exception:
                logger.exception("Skipping unparseable Facebook inbox file: %s", path)
                continue
            listings.append(listing)
            path.unlink()
        return listings
