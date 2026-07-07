from datetime import UTC, datetime
from pathlib import Path

from sw_sourcing.diagnostics import should_report_failure, write_report


def test_write_report_creates_a_file_in_the_reports_dir(tmp_path: Path) -> None:
    path = write_report(
        summary="ebay adapter failed", context={"source": "ebay"}, reports_dir=tmp_path
    )

    assert path.exists()
    assert path.parent == tmp_path


def test_write_report_includes_the_summary_and_context(tmp_path: Path) -> None:
    path = write_report(
        summary="weird price",
        context={"listing_id": "123", "price": -5},
        reports_dir=tmp_path,
    )

    content = path.read_text()
    assert "weird price" in content
    assert "123" in content
    assert "-5" in content


def test_write_report_includes_the_exception_traceback_when_given(
    tmp_path: Path,
) -> None:
    try:
        raise ValueError("boom")
    except ValueError as exc:
        path = write_report(
            summary="boom happened", context={}, exception=exc, reports_dir=tmp_path
        )

    content = path.read_text()
    assert "ValueError" in content
    assert "boom" in content


def test_write_report_omits_exception_section_when_none_given(tmp_path: Path) -> None:
    path = write_report(summary="just a note", context={}, reports_dir=tmp_path)

    assert "## Exception" not in path.read_text()


def test_write_report_creates_the_reports_dir_if_missing(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "reports"

    path = write_report(summary="test", context={}, reports_dir=nested)

    assert path.exists()


def test_write_report_filenames_never_collide_even_within_the_same_second(
    tmp_path: Path,
) -> None:
    paths = {
        write_report(summary="same summary", context={}, reports_dir=tmp_path)
        for _ in range(20)
    }

    assert len(paths) == 20


def test_write_report_serializes_a_pydantic_model_in_context(tmp_path: Path) -> None:
    from tests.unit.factories import make_listing

    listing = make_listing(listing_id="p1")

    path = write_report(
        summary="odd listing", context={"listing": listing}, reports_dir=tmp_path
    )

    assert "p1" in path.read_text()


def test_should_report_failure_true_when_never_reported_before() -> None:
    assert should_report_failure(None, now=datetime(2026, 1, 1, tzinfo=UTC))


def test_should_report_failure_false_within_the_cooldown_window() -> None:
    last = "2026-01-01T00:00:00+00:00"
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)  # 12 hours later

    assert not should_report_failure(last, now=now, cooldown_hours=24.0)


def test_should_report_failure_true_once_the_cooldown_expires() -> None:
    last = "2026-01-01T00:00:00+00:00"
    now = datetime(2026, 1, 2, 1, tzinfo=UTC)  # 25 hours later

    assert should_report_failure(last, now=now, cooldown_hours=24.0)


def test_should_report_failure_respects_a_custom_cooldown() -> None:
    last = "2026-01-01T00:00:00+00:00"
    now = datetime(2026, 1, 1, 2, tzinfo=UTC)  # 2 hours later

    assert should_report_failure(last, now=now, cooldown_hours=1.0)
    assert not should_report_failure(last, now=now, cooldown_hours=3.0)
