"""Cheap keyword/price screen applied before any vision call."""

from __future__ import annotations

from collections.abc import Sequence

from sw_sourcing.core.schema import Listing


def passes_prefilter(
    listing: Listing,
    *,
    required_keywords: Sequence[str],
    max_listing_price: float,
) -> bool:
    """Reject listings that are off-topic or priced beyond any plausible deal.

    Text and price only — never inspects images or calls the vision model.
    """
    if listing.price + listing.shipping > max_listing_price:
        return False

    if not required_keywords:
        return True

    haystack = f"{listing.title} {listing.description}".lower()
    return any(keyword.lower() in haystack for keyword in required_keywords)
