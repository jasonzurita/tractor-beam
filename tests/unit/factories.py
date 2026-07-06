"""Shared test data builders and fakes."""

from __future__ import annotations

from collections.abc import Sequence
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


class FakeVisionClient:
    """A canned VisionClient for tests -- never touches a real model.

    `fail_first` simulates one bad listing among many, to prove batch
    processing survives a single grading failure.
    """

    def __init__(self, response: str, *, fail_first: bool = False) -> None:
        self.response = response
        self.calls = 0
        self._fail_first = fail_first

    def grade_listing(
        self, *, images: Sequence[str], title: str, description: str
    ) -> str:
        self.calls += 1
        if self._fail_first and self.calls == 1:
            raise RuntimeError("simulated grading failure")
        return self.response


class FakeResponse:
    def raise_for_status(self) -> None:
        return None


class FakeHttpxClient:
    """A canned httpx.Client stand-in for DiscordAlerts tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, *, json: dict[str, Any]) -> FakeResponse:
        self.calls.append((url, json))
        return FakeResponse()


class FakeAdapter:
    """A canned Adapter -- returns preset listings or raises on fetch()."""

    def __init__(
        self, listings: list[Listing] | None = None, *, error: Exception | None = None
    ) -> None:
        self._listings = listings or []
        self._error = error

    def fetch(self) -> list[Listing]:
        if self._error is not None:
            raise self._error
        return self._listings
