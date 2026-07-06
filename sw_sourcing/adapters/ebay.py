"""eBay Browse API adapter.

Normalizes eBay Browse API `item_summary` search results into `Listing`.
Built against the documented item_summary shape and a recorded fixture, per
the project's adapter testing standard -- field mappings (especially
`returnTerms` and `shortDescription` availability) should be calibrated
against the live API once real credentials are wired up.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from sw_sourcing.core.schema import BuyingOption, Listing

_BUYING_OPTION_MAP: dict[str, BuyingOption] = {
    "BEST_OFFER": "best_offer",
    "FIXED_PRICE": "fixed_price",
    "AUCTION": "auction",
}


def _buying_option(options: list[str]) -> BuyingOption:
    """eBay lists every applicable option per item; prefer offer-accepting
    options over auction when both are somehow present."""
    for option in ("BEST_OFFER", "FIXED_PRICE", "AUCTION"):
        if option in options:
            return _BUYING_OPTION_MAP[option]
    return "fixed_price"


def normalize_item(item: dict[str, Any], *, fetched_at: datetime) -> Listing:
    """Map one eBay Browse API item_summary to a `Listing`."""
    shipping_options = item.get("shippingOptions") or [{}]
    shipping_cost = shipping_options[0].get("shippingCost", {}).get("value", "0")

    images = [item["image"]["imageUrl"]] if item.get("image") else []
    images += [img["imageUrl"] for img in item.get("additionalImages", [])]

    location = item.get("itemLocation", {})
    location_str = ", ".join(
        part
        for part in (location.get("stateOrProvince"), location.get("country"))
        if part
    )

    buying_options = item.get("buyingOptions", [])

    return Listing(
        source="ebay",
        listing_id=item["itemId"],
        url=item["itemWebUrl"],
        title=item["title"],
        description=item.get("shortDescription", ""),
        price=float(item["price"]["value"]),
        shipping=float(shipping_cost),
        buying_option=_buying_option(buying_options),
        offers_accepted="BEST_OFFER" in buying_options,
        returns_accepted=bool(
            item.get("returnTerms", {}).get("returnsAccepted", False)
        ),
        seller_feedback=float(item.get("seller", {}).get("feedbackPercentage", 0))
        / 100,
        location=location_str or "unknown",
        images=images,
        fetched_at=fetched_at,
    )


class EbayAdapter:
    """Fetches Browse API search results for one saved query."""

    def __init__(
        self, *, app_token: str, query: str, client: httpx.Client | None = None
    ) -> None:
        self._app_token = app_token
        self._query = query
        self._client = client or httpx.Client(
            base_url="https://api.ebay.com", timeout=15.0
        )

    def fetch(self) -> list[Listing]:
        response = self._client.get(
            "/buy/browse/v1/item_summary/search",
            params={"q": self._query, "limit": 50},
            headers={"Authorization": f"Bearer {self._app_token}"},
        )
        response.raise_for_status()
        payload = response.json()
        fetched_at = datetime.now(UTC)
        return [
            normalize_item(item, fetched_at=fetched_at)
            for item in payload.get("itemSummaries", [])
        ]
