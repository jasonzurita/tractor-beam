from pathlib import Path

import pytest

from sw_sourcing.cli import build_adapters
from sw_sourcing.storage.config import Config
from sw_sourcing.storage.db import Database


def make_config(tmp_path: Path) -> Config:
    return Config(Database(tmp_path / "test.db"))


def test_ebay_is_wired_when_a_token_is_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EBAY_OAUTH_TOKEN", "fake-token")

    adapters = build_adapters(make_config(tmp_path))

    assert "ebay" in adapters


def test_ebay_is_skipped_without_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EBAY_OAUTH_TOKEN", raising=False)

    adapters = build_adapters(make_config(tmp_path))

    assert "ebay" not in adapters


def test_facebook_is_always_wired(tmp_path: Path) -> None:
    adapters = build_adapters(make_config(tmp_path))

    assert "facebook" in adapters


def test_adapters_are_filtered_to_sources_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EBAY_OAUTH_TOKEN", "fake-token")
    config = make_config(tmp_path)
    config.set("sources_enabled", ["facebook"])

    adapters = build_adapters(config)

    assert "ebay" not in adapters
    assert "facebook" in adapters


def test_mercari_is_never_wired_since_no_apify_client_is_configured(
    tmp_path: Path,
) -> None:
    # default sources_enabled includes "mercari", but no tier-2 adapter is
    # registered yet -- widening the net is a later phase per the spec.
    adapters = build_adapters(make_config(tmp_path))

    assert "mercari" not in adapters
