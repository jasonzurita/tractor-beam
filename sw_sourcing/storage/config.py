"""Typed accessor over the `config` table.

Thresholds and cadences live here (or their documented default), never as
literals in `core/`.
"""

from __future__ import annotations

import json
from typing import Any

from sw_sourcing.storage.db import Database

DEFAULTS: dict[str, Any] = {
    "target_per_figure": 5.00,
    # Placeholder -- no real market comps yet. Weapon pricing is a live
    # feature (see core/decision.py); tune this once you have a sense of
    # what vintage weapons/accessories actually sell for.
    "target_per_weapon": 8.00,
    "grade_floor": "mid",
    "authenticity_required": True,
    "repro_keyword_blocklist": [
        "repro",
        "reproduction",
        "replacement",
        "restored",
        "custom",
        "aftermarket",
        "not original",
    ],
    # Self-disclosed era terms (analogous to repro_keyword_blocklist) --
    # a seller naming a specific later line is a positive admission the
    # vision gate's own era judgment can miss (see core/authenticity.py).
    "era_mismatch_keyword_blocklist": [
        "90's",
        "90s",
        "1990s",
        "power of the force",
        "potf2",
        "potf 2",
        "vintage collection",
    ],
    "max_repro_risk_for_autobuy": "low",
    "prefer_returnable": True,
    "negotiate_band_pct": 0.35,
    "max_damage_ratio": 0.20,
    "confidence_floor": 0.5,
    # Craigslist is intentionally NOT enabled by default: as of 2026-07 its
    # public search RSS feed (`?format=rss`) returns 403 "blocked" and the
    # HTML search page is a JS shell with no server-rendered listings, so the
    # direct-fetch adapter can't reach it. The adapter + `craigslist_*` config
    # below are kept for when it's wired to a managed scraper (Apify, per the
    # spec's Tier-2 approach for defended sources). Re-enable once that path
    # exists. See adapters/craigslist.py.
    "sources_enabled": ["ebay", "facebook", "mercari"],
    # Cheap pre-vision screen (core/prefilter.py) -- not in the original
    # spec table, added when wiring the pipeline together.
    "prefilter_required_keywords": ["kenner", "vintage", "star wars"],
    "prefilter_max_listing_price": 500.00,
    # The eBay adapter runs each of these as a separate Browse API search and
    # unions the results (deduped by listing id), widening coverage beyond a
    # single phrase without touching the cost gate. Every query is kept
    # Star-Wars-specific so returned listings still carry a
    # `prefilter_required_keywords` term -- generic "toy lot" queries are
    # deliberately excluded, since they'd surface listings with no SW keyword
    # to prefilter on and blow past the pre-vision cost gate.
    "ebay_search_queries": [
        "vintage kenner star wars",
        "star wars figure lot",
        "kenner star wars weapon",
        "vintage star wars loose lot",
    ],
    # Craigslist (Tier-3 public RSS). `site` is the regional subdomain --
    # "longisland" covers the Nassau/Suffolk area around zip 11566. Each
    # (category x query) is a separate `?format=rss` search, unioned +
    # deduped. Categories default to "sss" (all "for sale"), which already
    # spans toys/collectibles/antiques in one request per query. Queries are
    # kept Star-Wars-specific for the same cost-gate reason as eBay's.
    "craigslist_site": "longisland",
    "craigslist_categories": ["sss"],
    "craigslist_search_queries": [
        "vintage star wars",
        "kenner star wars",
        "star wars figure lot",
    ],
    # How many listings per run should get a *fresh* (non-cache-hit) vision
    # grade before a source stops paging for more, and the hard cap on how
    # many pages it'll fetch trying to reach that -- without the cap, a
    # source with fewer than the budget's worth of ever-new listings would
    # page indefinitely looking for fresh ones that don't exist.
    "vision_analysis_budget_per_run": 25,
    "max_fetch_pages_per_source": 5,
    # Preflight retry (sw_sourcing/network.py) before `scan`/`send-report`
    # touch the network -- rides out the brief DNS/connectivity gap right
    # after waking from sleep instead of failing on the first raw error.
    # Defaults: 1s, 2s, 4s, 8s, 16s between attempts (~31s total).
    "network_check_max_attempts": 5,
    "network_check_initial_delay_seconds": 1.0,
    "network_check_max_delay_seconds": 16.0,
}


class Config:
    """Reads config values from SQLite, falling back to the documented default."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, key: str) -> Any:
        if key not in DEFAULTS:
            raise KeyError(f"unknown config key: {key}")
        raw = self._db.get_config(key)
        if raw is None:
            return DEFAULTS[key]
        return json.loads(raw)

    def set(self, key: str, value: Any) -> None:
        if key not in DEFAULTS:
            raise KeyError(f"unknown config key: {key}")
        self._db.set_config(key, json.dumps(value))
