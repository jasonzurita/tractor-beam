from pathlib import Path

from sw_sourcing.storage.db import Database


def make_db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


def test_unseen_listing_reports_not_seen(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    assert not db.has_seen("ebay", "123")


def test_marking_seen_makes_has_seen_true(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    db.mark_seen("ebay", "123", seen_at="2026-07-06T00:00:00Z")
    assert db.has_seen("ebay", "123")


def test_marking_seen_twice_updates_last_seen_without_raising(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    db.mark_seen("ebay", "123", seen_at="2026-07-06T00:00:00Z")
    db.mark_seen("ebay", "123", seen_at="2026-07-07T00:00:00Z")
    assert db.has_seen("ebay", "123")


def test_seen_is_scoped_per_source(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    db.mark_seen("ebay", "123", seen_at="2026-07-06T00:00:00Z")
    assert not db.has_seen("mercari", "123")


def test_vision_cache_miss_returns_none(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    assert db.get_vision_cache("hash1") is None


def test_vision_cache_hit_returns_stored_json(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    db.put_vision_cache(
        "hash1", '{"figure_count": 1}', created_at="2026-07-06T00:00:00Z"
    )
    assert db.get_vision_cache("hash1") == '{"figure_count": 1}'


def test_vision_cache_put_twice_updates_without_raising(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    db.put_vision_cache("hash1", "{}", created_at="2026-07-06T00:00:00Z")
    db.put_vision_cache(
        "hash1", '{"figure_count": 2}', created_at="2026-07-07T00:00:00Z"
    )
    assert db.get_vision_cache("hash1") == '{"figure_count": 2}'


def test_config_round_trips_through_get_and_set(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    db.set_config("target_per_figure", "5.0")
    assert db.get_config("target_per_figure") == "5.0"


def test_config_missing_key_returns_none(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    assert db.get_config("target_per_figure") is None


def test_record_alert_does_not_raise(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    db.record_alert(
        source="ebay",
        listing_id="1",
        outcome="buy",
        cost_per_figure=4.5,
        target_grade_count=10,
        max_repro_risk="low",
        returns_accepted=True,
        suggested_offer=None,
        alerted_at="2026-07-06T00:00:00Z",
    )


def test_record_run_does_not_raise(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    db.record_run(
        started_at="2026-07-06T00:00:00Z",
        sources_ok=["ebay"],
        sources_failed=["mercari"],
        listings_seen=5,
        alerts_sent=1,
    )
