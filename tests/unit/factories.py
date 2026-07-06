"""Shared test data builders."""

from __future__ import annotations

from typing import Any

from sw_sourcing.core.schema import Listing


def make_listing(**overrides: Any) -> Listing:
    defaults: dict[str, Any] = {
        "source": "ebay",
        "listing_id": "1",
        "url": "https://example.com/listing/1",
        "title": "Vintage Kenner Star Wars loose figure",
        "description": "Genuine vintage Kenner figure, minor wear.",
        "price": 10.0,
        "shipping": 5.0,
        "buying_option": "fixed_price",
        "offers_accepted": False,
        "returns_accepted": True,
        "seller_feedback": 0.99,
        "location": "OH, USA",
        "images": ["https://example.com/img1.jpg"],
        "fetched_at": "2026-07-06T14:00:00Z",
    }
    defaults.update(overrides)
    return Listing(**defaults)
