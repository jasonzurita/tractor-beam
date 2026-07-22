from pathlib import Path

from sw_sourcing import lock
from sw_sourcing.dashboard import (
    BugReportEntry,
    DashboardData,
    build_dashboard_data,
    collect_bug_reports,
    render_dashboard_html,
)
from sw_sourcing.storage.db import AlertRecord, Database, RunRecord, RunTotals


def make_alert(**overrides: object) -> AlertRecord:
    defaults: dict[str, object] = {
        "id": 1,
        "source": "ebay",
        "listing_id": "1",
        "title": "Vintage Kenner lot",
        "url": "https://example.com/1",
        "image_url": None,
        "outcome": "buy",
        "cost_per_figure": 4.5,
        "target_grade_count": 10,
        "max_repro_risk": "low",
        "returns_accepted": True,
        "suggested_offer": None,
        "vision_notes": None,
        "cost_per_weapon": None,
        "price": 45.0,
        "previous_price": None,
        "alerted_at": "2026-07-07T00:00:00Z",
        "reported_at": None,
    }
    defaults.update(overrides)
    return AlertRecord(**defaults)  # type: ignore[arg-type]


def make_run(**overrides: object) -> RunRecord:
    defaults: dict[str, object] = {
        "id": 1,
        "started_at": "2026-07-07T00:00:00Z",
        "finished_at": "2026-07-07T00:01:00Z",
        "sources_ok": ["ebay"],
        "sources_failed": [],
        "listings_seen": 5,
        "alerts_sent": 1,
    }
    defaults.update(overrides)
    return RunRecord(**defaults)  # type: ignore[arg-type]


def make_dashboard_data(**overrides: object) -> DashboardData:
    defaults: dict[str, object] = {
        "generated_at": "2026-07-07T12:00:00Z",
        "totals": RunTotals(total_runs=3, total_listings_seen=42, total_alerts_sent=7),
        "email_batch_count": 2,
        "outcome_counts": {"buy": 4, "negotiate": 2, "review": 1},
        "recent_runs": [make_run()],
        "recent_alerts": [make_alert()],
        "bug_reports": [],
        "scan_running": False,
        "db_path": "sw_sourcing.db",
        "log_path": "sw_sourcing.log",
        "lock_path": "sw_sourcing.scan.lock",
        "bug_reports_dir": "bug_reports",
        "cwd": "/path/to/tractor-beam",
        "home": "/Users/example",
    }
    defaults.update(overrides)
    return DashboardData(**defaults)  # type: ignore[arg-type]


def test_render_includes_generated_at() -> None:
    html_doc = render_dashboard_html(
        make_dashboard_data(generated_at="2026-07-07T12:00:00Z")
    )
    assert "2026-07-07T12:00:00Z" in html_doc


def test_render_includes_run_totals() -> None:
    html_doc = render_dashboard_html(
        make_dashboard_data(
            totals=RunTotals(total_runs=3, total_listings_seen=42, total_alerts_sent=7)
        )
    )
    assert "3" in html_doc
    assert "42" in html_doc


def test_render_includes_email_batch_count() -> None:
    html_doc = render_dashboard_html(make_dashboard_data(email_batch_count=9))
    assert "9" in html_doc


def test_render_includes_outcome_counts() -> None:
    html_doc = render_dashboard_html(
        make_dashboard_data(outcome_counts={"buy": 4, "negotiate": 2})
    )
    assert "buy" in html_doc.lower()
    assert "negotiate" in html_doc.lower()
    assert "4" in html_doc
    assert "2" in html_doc


def test_render_escapes_alert_title_and_url() -> None:
    html_doc = render_dashboard_html(
        make_dashboard_data(
            recent_alerts=[
                make_alert(
                    title="<script>alert('x')</script>",
                    url='https://example.com/?q="><script>',
                )
            ]
        )
    )
    assert "<script>alert('x')</script>" not in html_doc
    assert "&lt;script&gt;" in html_doc


def test_render_flags_failed_sources_in_recent_runs() -> None:
    html_doc = render_dashboard_html(
        make_dashboard_data(
            recent_runs=[make_run(sources_failed=["facebook"], sources_ok=["ebay"])]
        )
    )
    assert "facebook" in html_doc


def test_render_shows_no_bug_reports_message_when_empty() -> None:
    html_doc = render_dashboard_html(make_dashboard_data(bug_reports=[]))
    assert "no" in html_doc.lower()


def test_render_lists_bug_report_titles() -> None:
    html_doc = render_dashboard_html(
        make_dashboard_data(
            bug_reports=[
                BugReportEntry(
                    filename="20260707T000000-abcd1234-adapter-ebay-failed.md",
                    title="Adapter ebay failed during fetch()",
                    when="2026-07-07T00:00:00",
                )
            ]
        )
    )
    assert "Adapter ebay failed during fetch()" in html_doc


def test_collect_bug_reports_returns_empty_when_dir_missing(tmp_path: Path) -> None:
    assert collect_bug_reports(tmp_path / "does-not-exist") == []


def test_collect_bug_reports_parses_title_from_first_heading(tmp_path: Path) -> None:
    reports_dir = tmp_path / "bug_reports"
    reports_dir.mkdir()
    (reports_dir / "20260707T000000-abcd1234-adapter-ebay-failed.md").write_text(
        "# Adapter ebay failed during fetch()\n\nmore text\n"
    )

    entries = collect_bug_reports(reports_dir)

    assert len(entries) == 1
    assert entries[0].title == "Adapter ebay failed during fetch()"
    assert entries[0].filename == "20260707T000000-abcd1234-adapter-ebay-failed.md"


def test_collect_bug_reports_returns_newest_first(tmp_path: Path) -> None:
    reports_dir = tmp_path / "bug_reports"
    reports_dir.mkdir()
    (reports_dir / "20260701T000000-aaaa1111-first.md").write_text("# First\n")
    (reports_dir / "20260707T000000-bbbb2222-second.md").write_text("# Second\n")

    entries = collect_bug_reports(reports_dir)

    assert [entry.title for entry in entries] == ["Second", "First"]


def test_collect_bug_reports_respects_limit(tmp_path: Path) -> None:
    reports_dir = tmp_path / "bug_reports"
    reports_dir.mkdir()
    for i in range(5):
        (reports_dir / f"2026070{i + 1}T000000-{i:08d}-report.md").write_text(
            f"# Report {i}\n"
        )

    assert len(collect_bug_reports(reports_dir, limit=2)) == 2


def test_build_dashboard_data_aggregates_from_db(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    run_id = db.record_run_started(started_at="2026-07-07T00:00:00Z")
    db.record_run_finished(
        run_id=run_id,
        finished_at="2026-07-07T00:01:00Z",
        sources_ok=["ebay"],
        sources_failed=[],
        listings_seen=5,
        alerts_sent=1,
    )
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
        cost_per_weapon=None,
        price=45.0,
        previous_price=None,
        alerted_at="2026-07-07T00:00:00Z",
    )
    reports_dir = tmp_path / "bug_reports"
    reports_dir.mkdir()
    (reports_dir / "20260707T000000-abcd1234-issue.md").write_text("# Something odd\n")

    data = build_dashboard_data(
        db,
        bug_reports_dir=reports_dir,
        generated_at="2026-07-07T12:00:00Z",
        db_path=str(tmp_path / "test.db"),
        log_path=str(tmp_path / "test.log"),
        lock_path=str(tmp_path / "test.lock"),
    )

    assert data.generated_at == "2026-07-07T12:00:00Z"
    assert data.totals.total_runs == 1
    assert data.totals.total_listings_seen == 5
    assert data.outcome_counts == {"buy": 1}
    assert len(data.recent_runs) == 1
    assert len(data.recent_alerts) == 1
    assert len(data.bug_reports) == 1
    assert data.bug_reports[0].title == "Something odd"


def test_render_shows_idle_status_when_scan_is_not_running() -> None:
    html_doc = render_dashboard_html(make_dashboard_data(scan_running=False))
    assert "idle" in html_doc.lower()


def test_render_shows_running_status_when_scan_is_running() -> None:
    html_doc = render_dashboard_html(make_dashboard_data(scan_running=True))
    assert "running" in html_doc.lower()


def test_render_includes_runbook_commands() -> None:
    html_doc = render_dashboard_html(make_dashboard_data())
    assert "crontab" in html_doc.lower()
    assert "pkill" in html_doc.lower()


def test_render_runbook_references_configured_paths() -> None:
    html_doc = render_dashboard_html(
        make_dashboard_data(
            db_path="/custom/sw.db",
            log_path="/custom/sw.log",
            lock_path="/custom/sw.lock",
            bug_reports_dir="/custom/bug_reports",
            cwd="/custom/project",
        )
    )
    assert "/custom/sw.log" in html_doc
    assert "/custom/sw.lock" in html_doc
    assert "/custom/bug_reports" in html_doc
    assert "/custom/project" in html_doc


def test_build_dashboard_data_reports_idle_when_lock_is_free(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")

    data = build_dashboard_data(
        db,
        bug_reports_dir=tmp_path / "bug_reports",
        generated_at="2026-07-07T12:00:00Z",
        db_path=str(tmp_path / "test.db"),
        log_path=str(tmp_path / "test.log"),
        lock_path=str(tmp_path / "test.lock"),
    )

    assert data.scan_running is False


def test_build_dashboard_data_reports_running_when_lock_is_held(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    lock_path = tmp_path / "test.lock"

    with lock.acquire(lock_path) as acquired:
        assert acquired
        data = build_dashboard_data(
            db,
            bug_reports_dir=tmp_path / "bug_reports",
            generated_at="2026-07-07T12:00:00Z",
            db_path=str(tmp_path / "test.db"),
            log_path=str(tmp_path / "test.log"),
            lock_path=str(lock_path),
        )

    assert data.scan_running is True


def test_render_runbook_uses_pgrep_instead_of_self_matching_grep() -> None:
    html_doc = render_dashboard_html(make_dashboard_data())
    assert "pgrep" in html_doc
    assert 'ps aux | grep "sw_sourcing.cli' not in html_doc


def test_render_runbook_has_copy_buttons_on_commands() -> None:
    html_doc = render_dashboard_html(make_dashboard_data())
    assert html_doc.count('class="copy-btn"') >= 5


def test_render_runbook_includes_copy_script() -> None:
    html_doc = render_dashboard_html(make_dashboard_data())
    assert "function copyBlock" in html_doc


def test_render_runbook_shows_human_readable_cadences() -> None:
    html_doc = render_dashboard_html(make_dashboard_data())
    assert "Every 30 minutes" in html_doc
    assert "Every day at 9:00 AM" in html_doc
    assert "Every 10 minutes" in html_doc


def test_render_runbook_still_shows_raw_cron_expressions() -> None:
    html_doc = render_dashboard_html(make_dashboard_data())
    assert "*/30 * * * *" in html_doc
    assert "0 9 * * *" in html_doc
    assert "*/10 * * * *" in html_doc


def test_render_runbook_includes_cadence_reference_table() -> None:
    html_doc = render_dashboard_html(make_dashboard_data())
    assert "Every hour" in html_doc
    assert "Every weekday" in html_doc


def test_render_runbook_explains_when_to_use_launchd_vs_cron() -> None:
    html_doc = render_dashboard_html(make_dashboard_data())
    assert "Not logged in" in html_doc
    assert "keychain" in html_doc.lower()


def test_render_runbook_includes_launchd_plist_labels() -> None:
    html_doc = render_dashboard_html(make_dashboard_data())
    assert "com.tractorbeam.scan" in html_doc
    assert "com.tractorbeam.send-report" in html_doc


def test_render_runbook_launchd_plist_uses_configured_home_and_cwd() -> None:
    html_doc = render_dashboard_html(
        make_dashboard_data(home="/Users/custom", cwd="/custom/project")
    )
    assert "/Users/custom/Library/LaunchAgents" in html_doc
    assert "/custom/project/.venv/bin/python" in html_doc


def test_render_runbook_launchd_commands_are_html_escaped_and_copyable() -> None:
    html_doc = render_dashboard_html(make_dashboard_data())
    assert "&lt;key&gt;Label&lt;/key&gt;" in html_doc
    assert "<key>Label</key>" not in html_doc
    assert "launchctl bootstrap" in html_doc
    assert "launchctl kickstart" in html_doc
    assert "launchctl bootout" in html_doc


def test_render_shows_last_run_finished_summary_with_duration() -> None:
    html_doc = render_dashboard_html(
        make_dashboard_data(
            generated_at="2026-07-07T00:15:00Z",
            scan_running=False,
            recent_runs=[
                make_run(
                    started_at="2026-07-07T00:00:00Z",
                    finished_at="2026-07-07T00:01:30Z",
                )
            ],
        )
    )
    assert "1m 30s" in html_doc
    assert "13m" in html_doc  # ago, generated - finished


def test_render_shows_currently_running_summary_when_latest_run_in_progress() -> None:
    html_doc = render_dashboard_html(
        make_dashboard_data(
            generated_at="2026-07-07T00:05:00Z",
            scan_running=True,
            recent_runs=[
                make_run(
                    started_at="2026-07-07T00:00:00Z",
                    finished_at=None,
                    sources_ok=[],
                    sources_failed=[],
                    listings_seen=None,
                    alerts_sent=None,
                )
            ],
        )
    )
    assert "5m" in html_doc
    assert "never finished" not in html_doc.lower()
    assert "⚠️" not in html_doc


def test_render_warns_when_latest_run_never_finished_and_scan_is_not_running() -> None:
    html_doc = render_dashboard_html(
        make_dashboard_data(
            generated_at="2026-07-07T01:00:00Z",
            scan_running=False,
            recent_runs=[
                make_run(
                    started_at="2026-07-07T00:00:00Z",
                    finished_at=None,
                    sources_ok=[],
                    sources_failed=[],
                    listings_seen=None,
                    alerts_sent=None,
                )
            ],
        )
    )
    assert "crashed" in html_doc.lower() or "never finished" in html_doc.lower()


def test_render_shows_no_scans_message_in_summary_when_no_runs_recorded() -> None:
    html_doc = render_dashboard_html(make_dashboard_data(recent_runs=[]))
    assert "no scans recorded yet" in html_doc.lower()


def test_render_recent_runs_table_flags_crashed_run() -> None:
    html_doc = render_dashboard_html(
        make_dashboard_data(
            scan_running=False,
            recent_runs=[
                make_run(
                    finished_at=None,
                    sources_ok=[],
                    sources_failed=[],
                    listings_seen=None,
                    alerts_sent=None,
                )
            ],
        )
    )
    assert "crashed" in html_doc.lower()


def test_render_recent_runs_table_flags_currently_running_run() -> None:
    html_doc = render_dashboard_html(
        make_dashboard_data(
            scan_running=True,
            recent_runs=[
                make_run(
                    finished_at=None,
                    sources_ok=[],
                    sources_failed=[],
                    listings_seen=None,
                    alerts_sent=None,
                )
            ],
        )
    )
    assert "still running" in html_doc.lower()


def test_render_recent_runs_table_handles_missing_counts_without_raising() -> None:
    html_doc = render_dashboard_html(
        make_dashboard_data(
            scan_running=True,
            recent_runs=[
                make_run(
                    finished_at=None,
                    sources_ok=[],
                    sources_failed=[],
                    listings_seen=None,
                    alerts_sent=None,
                ),
                make_run(listings_seen=5, alerts_sent=1),
            ],
        )
    )
    assert "—" in html_doc
