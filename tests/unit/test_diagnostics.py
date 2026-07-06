from pathlib import Path

from sw_sourcing.diagnostics import write_report


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
