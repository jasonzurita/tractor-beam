import sqlite3
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


def record_sample_alert(db: Database, **overrides: object) -> None:
    defaults: dict[str, object] = {
        "source": "ebay",
        "listing_id": "1",
        "title": "Vintage Kenner lot",
        "url": "https://example.com/1",
        "image_url": "https://example.com/1.jpg",
        "outcome": "buy",
        "cost_per_figure": 4.5,
        "target_grade_count": 10,
        "max_repro_risk": "low",
        "returns_accepted": True,
        "suggested_offer": None,
        "vision_notes": None,
        "alerted_at": "2026-07-06T00:00:00Z",
    }
    defaults.update(overrides)
    db.record_alert(**defaults)  # type: ignore[arg-type]


def test_record_alert_does_not_raise(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    record_sample_alert(db)


def test_unreported_alerts_returns_a_freshly_recorded_alert(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    record_sample_alert(db, listing_id="1", title="Vintage Kenner lot")

    unreported = db.get_unreported_alerts()

    assert len(unreported) == 1
    assert unreported[0].listing_id == "1"
    assert unreported[0].title == "Vintage Kenner lot"
    assert unreported[0].reported_at is None


def test_unreported_alerts_round_trips_vision_notes(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    record_sample_alert(
        db, listing_id="1", vision_notes="Two droids lack a visible backstamp."
    )

    unreported = db.get_unreported_alerts()

    assert unreported[0].vision_notes == "Two droids lack a visible backstamp."


def test_opening_a_pre_vision_notes_db_migrates_the_column(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE alerts (
            id INTEGER PRIMARY KEY,
            source TEXT NOT NULL,
            listing_id TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            image_url TEXT,
            outcome TEXT NOT NULL,
            cost_per_figure REAL,
            target_grade_count INTEGER,
            max_repro_risk TEXT,
            returns_accepted INTEGER,
            suggested_offer REAL,
            alerted_at TEXT NOT NULL,
            reported_at TEXT
        )
        """)
    conn.commit()
    conn.close()

    db = Database(path)
    record_sample_alert(db, listing_id="1")

    assert db.get_unreported_alerts()[0].vision_notes is None


def test_marking_reported_removes_it_from_unreported(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    record_sample_alert(db, listing_id="1")

    unreported = db.get_unreported_alerts()
    db.mark_alerts_reported(
        [alert.id for alert in unreported], reported_at="2026-07-07T12:00:00Z"
    )

    assert db.get_unreported_alerts() == []


def test_marking_reported_does_not_affect_other_alerts(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    record_sample_alert(db, listing_id="1")
    record_sample_alert(db, listing_id="2")

    first = db.get_unreported_alerts()[0]
    db.mark_alerts_reported([first.id], reported_at="2026-07-07T12:00:00Z")

    remaining = db.get_unreported_alerts()
    assert len(remaining) == 1
    assert remaining[0].listing_id == "2"


def test_record_run_does_not_raise(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    db.record_run(
        started_at="2026-07-06T00:00:00Z",
        sources_ok=["ebay"],
        sources_failed=["mercari"],
        listings_seen=5,
        alerts_sent=1,
    )


def test_get_last_failure_report_returns_none_when_never_recorded(
    tmp_path: Path,
) -> None:
    db = make_db(tmp_path)
    assert db.get_last_failure_report("listing:ebay:1") is None


def test_record_and_get_last_failure_report_round_trips(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    db.record_failure_report("listing:ebay:1", reported_at="2026-07-07T00:00:00Z")
    assert db.get_last_failure_report("listing:ebay:1") == "2026-07-07T00:00:00Z"


def test_record_failure_report_twice_updates_without_raising(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    db.record_failure_report("listing:ebay:1", reported_at="2026-07-07T00:00:00Z")
    db.record_failure_report("listing:ebay:1", reported_at="2026-07-08T00:00:00Z")
    assert db.get_last_failure_report("listing:ebay:1") == "2026-07-08T00:00:00Z"


def test_failure_report_keys_are_independent(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    db.record_failure_report("listing:ebay:1", reported_at="2026-07-07T00:00:00Z")
    assert db.get_last_failure_report("listing:ebay:2") is None
    assert db.get_last_failure_report("adapter:ebay") is None
