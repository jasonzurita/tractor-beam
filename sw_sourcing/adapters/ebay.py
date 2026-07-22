"""eBay Browse API adapter.

Normalizes eBay Browse API `item_summary` search results into `Listing`.
Built against the documented item_summary shape and a recorded fixture, per
the project's adapter testing standard -- field mappings (especially
`returnTerms` and `shortDescription` availability) should be calibrated
against the live API once real credentials are wired up.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from sw_sourcing.core.schema import BuyingOption, Listing

_BUYING_OPTION_MAP: dict[str, BuyingOption] = {
    "BEST_OFFER": "best_offer",
    "FIXED_PRICE": "fixed_price",
    "AUCTION": "auction",
}

_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"

# Calculated-shipping listings often omit shippingCost entirely from the
# summary API (the real cost depends on buyer location), which is not the
# same as free shipping -- defaulting an undetermined cost to $0 would
# systematically underprice those listings, so it assumes this flat
# fallback instead. $0 is only ever used when eBay explicitly reports a
# shippingCost value (including a genuine "0.00" for free shipping).
_UNKNOWN_SHIPPING_FALLBACK = 5.00


def get_ebay_access_token(
    app_id: str, cert_id: str, *, client: httpx.Client | None = None
) -> str:
    """Exchange the (non-expiring) App ID/Cert ID pair for a fresh bearer
    token via eBay's client-credentials grant. Call this once per run --
    the app_id/cert_id never expire, so there's nothing to manage by hand.
    """
    client = client or httpx.Client(timeout=15.0)
    credentials = base64.b64encode(f"{app_id}:{cert_id}".encode()).decode()
    response = client.post(
        _TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        },
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


def is_still_listed(
    item_id: str, *, app_token: str, client: httpx.Client | None = None
) -> bool:
    """True unless eBay confirms this listing has ended or sold out.

    Fails open (assumes still listed) on a 404/5xx-adjacent ambiguity, an
    unexpected response shape, or a network error -- a transient blip
    should never silently drop a still-good alert from the digest, per the
    project's graceful-degradation rule. Only an explicit "gone" (404/410)
    or "sold out" (OUT_OF_STOCK) signal counts as not listed.
    """
    client = client or httpx.Client(base_url="https://api.ebay.com", timeout=15.0)
    try:
        response = client.get(
            f"/buy/browse/v1/item/{quote(item_id, safe='')}",
            headers={
                "Authorization": f"Bearer {app_token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            },
        )
    except httpx.HTTPError:
        return True

    if response.status_code in (404, 410):
        return False
    if response.status_code != 200:
        return True

    availabilities = response.json().get("estimatedAvailabilities", [])
    return not any(
        a.get("estimatedAvailabilityStatus") == "OUT_OF_STOCK" for a in availabilities
    )


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
    shipping_cost_field = shipping_options[0].get("shippingCost")
    if shipping_cost_field is not None and "value" in shipping_cost_field:
        shipping_cost = float(shipping_cost_field["value"])
    else:
        shipping_cost = _UNKNOWN_SHIPPING_FALLBACK

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
        shipping=shipping_cost,
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
    """Fetches Browse API search results across one or more saved queries.

    Each query is a separate Browse API search; the adapter unions their
    results (deduped by listing id) into one `Listing` batch, so widening
    the query list widens coverage without any change downstream -- the
    core still sees plain `Listing` objects from a single "ebay" source.
    """

    def __init__(
        self,
        *,
        app_token: str,
        queries: Sequence[str],
        client: httpx.Client | None = None,
    ) -> None:
        self._app_token = app_token
        self._queries = list(queries)
        self._client = client or httpx.Client(
            base_url="https://api.ebay.com", timeout=15.0
        )

    def fetch(self, *, offset: int = 0) -> list[Listing]:
        # One-shot: several queries at 50 results each already far exceed a
        # run's fresh-analysis budget, and `newlyListed` sort keeps the
        # freshest inventory on the first page of each query -- so there's
        # nothing to gain from paging deeper. Any offset>0 request is
        # therefore "exhausted", the same contract the Facebook inbox
        # adapter uses (see adapters/base.py).
        if offset > 0:
            return []

        fetched_at = datetime.now(UTC)
        listings: list[Listing] = []
        seen: set[str] = set()
        for query in self._queries:
            for item in self._search(query):
                listing = normalize_item(item, fetched_at=fetched_at)
                if listing.listing_id in seen:
                    continue
                seen.add(listing.listing_id)
                listings.append(listing)
        return listings

    def _search(self, query: str) -> list[dict[str, Any]]:
        response = self._client.get(
            "/buy/browse/v1/item_summary/search",
            # Sorted newest-first, not eBay's default best-match relevance --
            # otherwise a periodic re-scan would keep re-fetching the same
            # top-50 static "best match" page and never see fresh inventory
            # that ranks outside it.
            params={
                "q": query,
                "limit": 50,
                "sort": "newlyListed",
                "offset": 0,
            },
            headers={
                "Authorization": f"Bearer {self._app_token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            },
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        items: list[dict[str, Any]] = payload.get("itemSummaries", [])
        return items
