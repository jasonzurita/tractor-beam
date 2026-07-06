from pathlib import Path

import httpx
import pytest

from sw_sourcing.cli import build_adapters, main
from sw_sourcing.storage.config import Config
from sw_sourcing.storage.db import Database


def make_config(tmp_path: Path) -> Config:
    return Config(Database(tmp_path / "test.db"))


def fake_token_client(
    access_token: str = "fake-token", *, status_code: int = 200
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if status_code != 200:
            return httpx.Response(status_code, json={"error": "invalid_client"})
        return httpx.Response(200, json={"access_token": access_token})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ebay_is_wired_when_app_id_and_cert_id_are_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EBAY_APP_ID", "app-id")
    monkeypatch.setenv("EBAY_CERT_ID", "cert-id")

    adapters = build_adapters(
        make_config(tmp_path), ebay_token_client=fake_token_client()
    )

    assert "ebay" in adapters


def test_ebay_is_skipped_without_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("EBAY_APP_ID", raising=False)
    monkeypatch.delenv("EBAY_CERT_ID", raising=False)

    adapters = build_adapters(make_config(tmp_path))

    assert "ebay" not in adapters


def test_ebay_is_skipped_and_reported_when_token_fetch_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EBAY_APP_ID", "app-id")
    monkeypatch.setenv("EBAY_CERT_ID", "cert-id")
    reports_dir = tmp_path / "bug_reports"

    adapters = build_adapters(
        make_config(tmp_path),
        bug_reports_dir=reports_dir,
        ebay_token_client=fake_token_client(status_code=401),
    )

    assert "ebay" not in adapters
    assert list(reports_dir.glob("*.md"))


def test_facebook_is_always_wired(tmp_path: Path) -> None:
    adapters = build_adapters(make_config(tmp_path))

    assert "facebook" in adapters


def test_adapters_are_filtered_to_sources_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EBAY_APP_ID", "app-id")
    monkeypatch.setenv("EBAY_CERT_ID", "cert-id")
    config = make_config(tmp_path)
    config.set("sources_enabled", ["facebook"])

    adapters = build_adapters(config, ebay_token_client=fake_token_client())

    assert "ebay" not in adapters
    assert "facebook" in adapters


def test_mercari_is_never_wired_since_no_apify_client_is_configured(
    tmp_path: Path,
) -> None:
    # default sources_enabled includes "mercari", but no tier-2 adapter is
    # registered yet -- widening the net is a later phase per the spec.
    adapters = build_adapters(make_config(tmp_path))

    assert "mercari" not in adapters


def test_report_bug_command_writes_a_report_and_exits_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reports_dir = tmp_path / "reports"
    monkeypatch.setenv("SW_SOURCING_BUG_REPORTS_DIR", str(reports_dir))

    exit_code = main(["report-bug", "cost per figure looked wrong for listing X"])

    assert exit_code == 0
    reports = list(reports_dir.glob("*.md"))
    assert len(reports) == 1
    assert "cost per figure looked wrong" in reports[0].read_text()
