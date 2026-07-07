"""Live eBay Browse API integration test.

Marked @pytest.mark.integration -- excluded from the default `pytest` run
(see pyproject.toml). Run explicitly with `pytest -m integration` once
EBAY_APP_ID/EBAY_CERT_ID are available (via `.env` or the shell
environment).
"""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv

from sw_sourcing.adapters.ebay import EbayAdapter, get_ebay_access_token

load_dotenv()

pytestmark = pytest.mark.integration


def _ebay_credentials() -> tuple[str, str]:
    app_id = os.environ.get("EBAY_APP_ID")
    cert_id = os.environ.get("EBAY_CERT_ID")
    if not app_id or not cert_id:
        pytest.skip("EBAY_APP_ID/EBAY_CERT_ID not set; skipping live eBay test")
    return app_id, cert_id


def test_get_ebay_access_token_returns_a_real_bearer_token() -> None:
    app_id, cert_id = _ebay_credentials()

    token = get_ebay_access_token(app_id, cert_id)

    assert isinstance(token, str)
    assert len(token) > 20  # real tokens are long opaque strings


def test_ebay_adapter_fetch_hits_the_real_api_without_erroring() -> None:
    app_id, cert_id = _ebay_credentials()
    token = get_ebay_access_token(app_id, cert_id)
    adapter = EbayAdapter(app_token=token, query="vintage kenner star wars")

    listings = adapter.fetch()

    # Not asserting a nonzero count -- a real search can legitimately
    # return zero results. This test's job is to prove the auth + request
    # path and response parsing work end-to-end, not to guarantee inventory.
    assert isinstance(listings, list)
    for listing in listings:
        assert listing.source == "ebay"
        assert listing.listing_id
        assert listing.price >= 0
