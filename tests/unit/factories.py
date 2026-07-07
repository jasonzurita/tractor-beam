"""Shared test data builders and fakes."""

from __future__ import annotations

from collections.abc import Sequence
from email.message import EmailMessage
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
    processing survives a single grading failure. `always_fail` simulates a
    persistently (not just transiently) broken listing.
    """

    def __init__(
        self, response: str, *, fail_first: bool = False, always_fail: bool = False
    ) -> None:
        self.response = response
        self.calls = 0
        self._fail_first = fail_first
        self._always_fail = always_fail

    def grade_listing(
        self, *, images: Sequence[str], title: str, description: str
    ) -> str:
        self.calls += 1
        if self._always_fail or (self._fail_first and self.calls == 1):
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
    """A canned Adapter -- returns preset listings or raises on fetch().

    `pages`, keyed by the exact offset the pipeline will request, lets a
    test control multi-page pagination; a plain `listings` list only ever
    answers offset 0 (matching a real single-page source).
    """

    def __init__(
        self,
        listings: list[Listing] | None = None,
        *,
        error: Exception | None = None,
        pages: dict[int, list[Listing]] | None = None,
    ) -> None:
        self._listings = listings or []
        self._error = error
        self._pages = pages
        self.fetch_offsets: list[int] = []

    def set_listings(self, listings: list[Listing]) -> None:
        """Change what offset 0 returns -- simulates a re-scan seeing the
        same source with updated listing data (e.g. a price change)."""
        self._listings = listings
        self._pages = None

    def fetch(self, *, offset: int = 0) -> list[Listing]:
        self.fetch_offsets.append(offset)
        if self._error is not None:
            raise self._error
        if self._pages is not None:
            return self._pages.get(offset, [])
        return self._listings if offset == 0 else []


class FakeSmtp:
    """A canned SMTP connection stand-in for EmailSender tests."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.login_calls: list[tuple[str, str]] = []
        self.sent_messages: list[EmailMessage] = []

    def __enter__(self) -> FakeSmtp:
        if self._error is not None:
            raise self._error
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def login(self, username: str, password: str) -> None:
        self.login_calls.append((username, password))

    def send_message(self, message: EmailMessage) -> None:
        self.sent_messages.append(message)
