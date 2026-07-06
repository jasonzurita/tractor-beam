from pathlib import Path

import pytest

from sw_sourcing.storage.config import Config
from sw_sourcing.storage.db import Database


def make_config(tmp_path: Path) -> Config:
    return Config(Database(tmp_path / "test.db"))


def test_unset_key_returns_documented_default(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    assert config.get("target_per_figure") == 5.00


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


def test_default_max_repro_risk_for_autobuy_is_low(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    assert config.get("max_repro_risk_for_autobuy") == "low"


def test_default_prefilter_keywords_and_price_ceiling_are_set(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    assert "kenner" in config.get("prefilter_required_keywords")
    assert config.get("prefilter_max_listing_price") == 500.00
