"""Craigslist search-feed adapter (Tier-3, public RSS).

Reads Craigslist's per-search RSS feeds (`?format=rss`) for one regional
site (e.g. `longisland` for the Nassau/Suffolk area around zip 11566) and
normalizes them to `Listing`. Public listing data only -- no login, no
scraping of the JS search UI, and never any auto-messaging of sellers
(contacting a seller is a human step outside this code). ToS-wise this is
the same "public data, human contacts the seller" posture as the Facebook
adapter.

Built against a recorded fixture per the project's adapter testing
standard. Craigslist has reworked its search UI over the years and the RSS
field shapes (especially the image `enclosure` attribute and whether a
price is present) should be recalibrated against the live feed once this is
pointed at production -- the parser is deliberately tolerant of namespace
and attribute-name quirks for that reason.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

import httpx

from sw_sourcing.core.schema import Listing

logger = logging.getLogger(__name__)

# Craigslist is local pickup; there is no shipping and no formal
# buying-option/returns signal -- a human always haggles over contact, so
# treat it as an offer-accepting source (mirrors the Facebook adapter) with
# no returns. This makes CL eligible for negotiate alerts, unlike an auction.
_SHIPPING = 0.0
_BUYING_OPTION = "best_offer"

_PRICE_RE = re.compile(r"\$([\d,]+(?:\.\d{1,2})?)")
_POST_ID_RE = re.compile(r"/(\d+)\.html")
_TRAILING_LOCATION_RE = re.compile(r"\(([^()]+)\)\s*$")
_HTTP_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _localname(tag: str) -> str:
    """Strip an ElementTree ``{namespace}tag`` down to its local name, so the
    parser can match feed elements without hardcoding Craigslist's exact
    (and historically shifting) RSS/RDF namespace URIs."""
    return tag.rsplit("}", 1)[-1]


def _child_text(item: ET.Element, name: str) -> str | None:
    for child in item:
        if _localname(child.tag) == name and child.text is not None:
            return child.text.strip()
    return None


def _item_url(item: ET.Element) -> str | None:
    """The listing URL: the `rdf:about` attribute, falling back to a `<link>`
    child. Matched by local name so a namespace change doesn't break it."""
    for key, value in item.attrib.items():
        if _localname(key) == "about" and _HTTP_URL_RE.match(value):
            return value
    link = _child_text(item, "link")
    return link if link and _HTTP_URL_RE.match(link) else None


def _item_images(item: ET.Element) -> list[str]:
    """Every image URL on the item -- any `enclosure` element's URL-looking
    attribute value, regardless of which (namespaced) attribute holds it."""
    images: list[str] = []
    for child in item:
        if _localname(child.tag) != "enclosure":
            continue
        for value in child.attrib.values():
            if _HTTP_URL_RE.match(value):
                images.append(value)
                break
    return images


def _parse_price(title: str) -> float | None:
    """Craigslist puts the asking price in the listing title (e.g.
    ``... - $50 (Merrick)``). Returns None when no price is present -- those
    listings are dropped rather than defaulted, since a missing price can't
    be defaulted to $0 without manufacturing a false "great deal" for the
    cost math downstream."""
    match = _PRICE_RE.search(title)
    if match is None:
        return None
    return float(match.group(1).replace(",", ""))


def _parse_location(title: str, *, default: str) -> str:
    match = _TRAILING_LOCATION_RE.search(title)
    return match.group(1).strip() if match else default


def parse_search_rss(
    xml_text: str, *, fetched_at: datetime, site: str
) -> list[Listing]:
    """Parse one Craigslist search RSS feed into `Listing` objects.

    Items without a parseable post id, URL, title, or price are skipped
    (logged), never guessed -- a bad row must not stop the rest of the feed.
    """
    # Craigslist is a trusted first-party HTTPS feed; ElementTree does not
    # resolve external entities, so this parses the document as-is.
    root = ET.fromstring(xml_text)
    default_location = f"{site} (Craigslist)"

    listings: list[Listing] = []
    for item in root.iter():
        if _localname(item.tag) != "item":
            continue
        url = _item_url(item)
        title = _child_text(item, "title")
        if url is None or title is None:
            continue
        post_id_match = _POST_ID_RE.search(url)
        if post_id_match is None:
            continue
        price = _parse_price(title)
        if price is None:
            logger.info("Skipping Craigslist listing with no price: %s", url)
            continue

        # model_validate (not the kwargs constructor) so pydantic coerces the
        # parsed strings into the schema's HttpUrl fields -- the same shape
        # the eBay adapter gets for free from its dict-of-Any source payload.
        listings.append(
            Listing.model_validate(
                {
                    "source": "craigslist",
                    "listing_id": post_id_match.group(1),
                    "url": url,
                    "title": title,
                    "description": _child_text(item, "description") or "",
                    "price": price,
                    "shipping": _SHIPPING,
                    "buying_option": _BUYING_OPTION,
                    "offers_accepted": True,
                    "returns_accepted": False,
                    "seller_feedback": 0.0,
                    "location": _parse_location(title, default=default_location),
                    "images": _item_images(item),
                    "fetched_at": fetched_at,
                }
            )
        )
    return listings


class CraigslistAdapter:
    """Fetches Craigslist search RSS feeds for one regional site.

    Runs each (query x category) as a separate `?format=rss` search and
    unions the results (deduped by listing id) into one `Listing` batch --
    the same widen-by-config, source-agnostic-downstream shape the eBay
    adapter uses. One-shot like the Facebook adapter: a Craigslist RSS feed
    already returns only its newest page, so any offset>0 request is treated
    as exhausted.
    """

    def __init__(
        self,
        *,
        site: str,
        queries: Sequence[str],
        categories: Sequence[str],
        client: httpx.Client | None = None,
    ) -> None:
        self._site = site
        self._queries = list(queries)
        self._categories = list(categories)
        self._client = client or httpx.Client(
            base_url=f"https://{site}.craigslist.org", timeout=15.0
        )

    def fetch(self, *, offset: int = 0) -> list[Listing]:
        if offset > 0:
            return []

        fetched_at = datetime.now(UTC)
        listings: list[Listing] = []
        seen: set[str] = set()
        for category in self._categories:
            for query in self._queries:
                for listing in self._search(category, query, fetched_at=fetched_at):
                    if listing.listing_id in seen:
                        continue
                    seen.add(listing.listing_id)
                    listings.append(listing)
        return listings

    def _search(
        self, category: str, query: str, *, fetched_at: datetime
    ) -> list[Listing]:
        response = self._client.get(
            f"/search/{category}",
            # sort=date so a periodic re-scan sees fresh inventory first,
            # not a stable relevance ranking (same rationale as eBay).
            params={"query": query, "sort": "date", "format": "rss"},
        )
        response.raise_for_status()
        return parse_search_rss(response.text, fetched_at=fetched_at, site=self._site)
