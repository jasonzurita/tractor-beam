from pathlib import Path

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
        "price": 45.0,
        "alerted_at": "2026-07-07T00:00:00Z",
        "reported_at": None,
    }
    defaults.update(overrides)
    return AlertRecord(**defaults)  # type: ignore[arg-type]


def make_run(**overrides: object) -> RunRecord:
    defaults: dict[str, object] = {
        "id": 1,
        "started_at": "2026-07-07T00:00:00Z",
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
    assert "<script>" not in html_doc
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
    db.record_run(
        started_at="2026-07-07T00:00:00Z",
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
        price=45.0,
        alerted_at="2026-07-07T00:00:00Z",
    )
    reports_dir = tmp_path / "bug_reports"
    reports_dir.mkdir()
    (reports_dir / "20260707T000000-abcd1234-issue.md").write_text("# Something odd\n")

    data = build_dashboard_data(
        db, bug_reports_dir=reports_dir, generated_at="2026-07-07T12:00:00Z"
    )

    assert data.generated_at == "2026-07-07T12:00:00Z"
    assert data.totals.total_runs == 1
    assert data.totals.total_listings_seen == 5
    assert data.outcome_counts == {"buy": 1}
    assert len(data.recent_runs) == 1
    assert len(data.recent_alerts) == 1
    assert len(data.bug_reports) == 1
    assert data.bug_reports[0].title == "Something odd"
