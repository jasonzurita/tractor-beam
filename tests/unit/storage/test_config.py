from pathlib import Path

import pytest

from sw_sourcing.storage.config import Config
from sw_sourcing.storage.db import Database


def make_config(tmp_path: Path) -> Config:
    return Config(Database(tmp_path / "test.db"))


def test_unset_key_returns_documented_default(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    assert config.get("target_per_figure") == 5.00


def test_target_per_weapon_has_a_real_default_not_none(tmp_path: Path) -> None:
    # A live placeholder, not "TBD" -- weapon pricing is a real feature now,
    # not a config value that silently no-ops until someone sets it. Tune
    # via `cli.py config set target_per_weapon <value>` once you have comps.
    config = make_config(tmp_path)
    assert config.get("target_per_weapon") == 8.00


def test_set_then_get_round_trips_a_float(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.set("target_per_figure", 6.5)
    assert config.get("target_per_figure") == 6.5


def test_set_then_get_round_trips_a_list(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.set("sources_enabled", ["ebay"])
    assert config.get("sources_enabled") == ["ebay"]


def test_set_then_get_round_trips_a_bool(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.set("prefer_returnable", False)
    assert config.get("prefer_returnable") is False


def test_unknown_key_raises_on_get(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with pytest.raises(KeyError):
        config.get("not_a_real_key")


def test_unknown_key_raises_on_set(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    with pytest.raises(KeyError):
        config.set("not_a_real_key", 1)


def test_default_blocklist_matches_spec(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    blocklist = config.get("repro_keyword_blocklist")
    assert "reproduction" in blocklist
    assert "restored" in blocklist


def test_default_era_mismatch_blocklist_covers_known_reissue_lines(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    blocklist = config.get("era_mismatch_keyword_blocklist")
    assert "90's" in blocklist
    assert "potf2" in blocklist
    assert "vintage collection" in blocklist


def test_default_max_repro_risk_for_autobuy_is_low(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    assert config.get("max_repro_risk_for_autobuy") == "low"


def test_default_prefilter_keywords_and_price_ceiling_are_set(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    assert "kenner" in config.get("prefilter_required_keywords")
    assert config.get("prefilter_max_listing_price") == 500.00


def test_default_vision_analysis_budget_and_page_cap_are_set(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    assert config.get("vision_analysis_budget_per_run") == 25
    assert config.get("max_fetch_pages_per_source") == 5


def test_craigslist_is_disabled_by_default_but_configured_for_long_island(
    tmp_path: Path,
) -> None:
    # Deliberately off by default: live Craigslist blocks the RSS feed (403)
    # and server-renders no listings, so the direct adapter can't reach it
    # (see adapters/craigslist.py). Config stays oriented to Long Island for
    # when it's wired to a managed scraper.
    config = make_config(tmp_path)
    assert "craigslist" not in config.get("sources_enabled")
    assert config.get("craigslist_site") == "longisland"
    assert config.get("craigslist_categories") == ["sss"]
    queries = config.get("craigslist_search_queries")
    assert queries and all(
        any(term in q.lower() for term in ("star wars", "kenner", "vintage"))
        for q in queries
    )


def test_default_ebay_search_queries_widen_beyond_a_single_phrase(
    tmp_path: Path,
) -> None:
    # eBay breadth is config-driven: several SW-specific angles, not one
    # hardcoded phrase. Every query stays Star-Wars-specific so the cheap
    # prefilter keyword screen still applies (no generic "toy lot" queries
    # that would defeat the pre-vision cost gate).
    config = make_config(tmp_path)
    queries = config.get("ebay_search_queries")
    assert isinstance(queries, list)
    assert len(queries) > 1
    assert "vintage kenner star wars" in queries
    assert all(
        any(term in q.lower() for term in ("star wars", "kenner", "vintage"))
        for q in queries
    )
