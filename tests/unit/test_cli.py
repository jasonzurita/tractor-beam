import logging
from pathlib import Path

import httpx
import pytest

import sw_sourcing.cli as cli
from sw_sourcing import lock
from sw_sourcing.cli import build_adapters, configure_logging, main, send_report
from sw_sourcing.storage.config import Config
from sw_sourcing.storage.db import Database
from tests.unit.factories import FakeSmtp


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


def make_db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


def test_send_report_emails_unreported_alerts_and_marks_them_reported(
    tmp_path: Path,
) -> None:
    db = make_db(tmp_path)
    db.record_alert(
        source="ebay",
        listing_id="1",
        title="Vintage Kenner lot",
        url="https://example.com/1",
        image_url=None,
        outcome="buy",
        cost_per_figure=4.5,
        target_grade_count=10,
        max_repro_risk="low",
        returns_accepted=True,
        suggested_offer=None,
        vision_notes=None,
        price=45.0,
        alerted_at="2026-07-07T00:00:00Z",
    )
    smtp = FakeSmtp()

    count = send_report(
        db,
        to_addr="jasonzurita@me.com",
        smtp_host="smtp.gmail.com",
        smtp_port=465,
        smtp_username="jzuri1@gmail.com",
        smtp_password="app-password",
        smtp_factory=lambda: smtp,
    )

    assert count == 1
    assert len(smtp.sent_messages) == 1
    assert db.get_unreported_alerts() == []


def test_send_report_is_a_noop_when_nothing_is_unreported(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    smtp = FakeSmtp()

    count = send_report(
        db,
        to_addr="jasonzurita@me.com",
        smtp_host="smtp.gmail.com",
        smtp_port=465,
        smtp_username="jzuri1@gmail.com",
        smtp_password="app-password",
        smtp_factory=lambda: smtp,
    )

    assert count == 0
    assert smtp.sent_messages == []


def test_scan_skips_cleanly_when_a_previous_scan_is_still_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "scan.lock"
    monkeypatch.setenv("SW_SOURCING_LOCK_PATH", str(lock_path))
    monkeypatch.setenv("SW_SOURCING_DB_PATH", str(tmp_path / "test.db"))

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "build_adapters should not run while another scan holds the lock"
        )

    monkeypatch.setattr(cli, "build_adapters", _boom)

    with lock.acquire(lock_path) as acquired:
        assert acquired
        exit_code = main(["scan"])

    assert exit_code == 0


def test_config_get_prints_the_current_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SW_SOURCING_DB_PATH", str(tmp_path / "test.db"))

    exit_code = main(["config", "get", "target_per_figure"])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "5.0"


def test_config_set_then_get_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SW_SOURCING_DB_PATH", str(tmp_path / "test.db"))

    main(["config", "set", "target_per_figure", "6.5"])
    capsys.readouterr()
    main(["config", "get", "target_per_figure"])

    assert capsys.readouterr().out.strip() == "6.5"


def test_config_set_accepts_json_lists_and_bools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SW_SOURCING_DB_PATH", str(tmp_path / "test.db"))

    main(["config", "set", "sources_enabled", '["ebay"]'])
    capsys.readouterr()
    main(["config", "get", "sources_enabled"])

    assert capsys.readouterr().out.strip() == '["ebay"]'


def test_config_get_rejects_an_unknown_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SW_SOURCING_DB_PATH", str(tmp_path / "test.db"))

    with pytest.raises(SystemExit):
        main(["config", "get", "not_a_real_key"])


def test_config_set_rejects_invalid_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SW_SOURCING_DB_PATH", str(tmp_path / "test.db"))

    with pytest.raises(SystemExit):
        main(["config", "set", "target_per_figure", "not-json"])


def test_config_list_prints_every_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SW_SOURCING_DB_PATH", str(tmp_path / "test.db"))

    exit_code = main(["config", "list"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "target_per_figure" in out
    assert "sources_enabled" in out


def test_dashboard_command_writes_an_html_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SW_SOURCING_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SW_SOURCING_BUG_REPORTS_DIR", str(tmp_path / "bug_reports"))
    out_path = tmp_path / "dashboard.html"

    exit_code = main(["dashboard", "--out", str(out_path)])

    assert exit_code == 0
    assert out_path.exists()
    assert "Sourcing Engine Dashboard" in out_path.read_text()


def test_dashboard_command_defaults_to_dashboard_html_in_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SW_SOURCING_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SW_SOURCING_BUG_REPORTS_DIR", str(tmp_path / "bug_reports"))
    monkeypatch.chdir(tmp_path)

    exit_code = main(["dashboard"])

    assert exit_code == 0
    assert (tmp_path / "dashboard.html").exists()


def test_configure_logging_writes_to_the_configured_rotating_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_path = tmp_path / "test.log"
    monkeypatch.setenv("SW_SOURCING_LOG_PATH", str(log_path))
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    try:
        configure_logging()
        logging.getLogger("sw_sourcing.test").info("hello from the test")
        for handler in root.handlers:
            handler.flush()

        assert log_path.exists()
        assert "hello from the test" in log_path.read_text()
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)
