import sqlite3
from pathlib import Path

from sw_sourcing.storage.db import Database


def make_db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


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
        "cost_per_weapon": None,
        "price": 45.0,
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


def test_unreported_alerts_round_trips_cost_per_weapon(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    record_sample_alert(db, listing_id="1", cost_per_weapon=6.5)

    unreported = db.get_unreported_alerts()

    assert unreported[0].cost_per_weapon == 6.5


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


def test_opening_a_pre_price_column_db_migrates_the_column(tmp_path: Path) -> None:
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
            vision_notes TEXT,
            alerted_at TEXT NOT NULL,
            reported_at TEXT
        )
        """)
    conn.commit()
    conn.close()

    db = Database(path)
    record_sample_alert(db, listing_id="1", price=12.5)

    assert db.get_unreported_alerts()[0].price == 12.5


def test_opening_a_pre_cost_per_weapon_db_migrates_the_column(tmp_path: Path) -> None:
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
            vision_notes TEXT,
            price REAL,
            alerted_at TEXT NOT NULL,
            reported_at TEXT
        )
        """)
    conn.commit()
    conn.close()

    db = Database(path)
    record_sample_alert(db, listing_id="1")

    assert db.get_unreported_alerts()[0].cost_per_weapon is None


def test_has_alerted_is_false_for_a_never_alerted_combo(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    assert not db.has_alerted("ebay", "1", outcome="buy", price=10.0)


def test_has_alerted_is_true_after_recording_that_exact_combo(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    record_sample_alert(db, listing_id="1", outcome="buy", price=10.0)

    assert db.has_alerted("ebay", "1", outcome="buy", price=10.0)


def test_has_alerted_is_false_when_price_differs(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    record_sample_alert(db, listing_id="1", outcome="buy", price=10.0)

    assert not db.has_alerted("ebay", "1", outcome="buy", price=5.0)


def test_has_alerted_is_false_when_outcome_differs(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    record_sample_alert(db, listing_id="1", outcome="review", price=10.0)

    assert not db.has_alerted("ebay", "1", outcome="buy", price=10.0)


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


def record_finished_run(
    db: Database,
    *,
    started_at: str,
    finished_at: str,
    sources_ok: list[str],
    sources_failed: list[str],
    listings_seen: int,
    alerts_sent: int,
) -> int:
    run_id = db.record_run_started(started_at=started_at)
    db.record_run_finished(
        run_id=run_id,
        finished_at=finished_at,
        sources_ok=sources_ok,
        sources_failed=sources_failed,
        listings_seen=listings_seen,
        alerts_sent=alerts_sent,
    )
    return run_id


def test_record_run_started_and_finished_does_not_raise(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    record_finished_run(
        db,
        started_at="2026-07-06T00:00:00Z",
        finished_at="2026-07-06T00:01:00Z",
        sources_ok=["ebay"],
        sources_failed=["mercari"],
        listings_seen=5,
        alerts_sent=1,
    )


def test_record_run_started_returns_an_id_finish_can_target(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    first_id = db.record_run_started(started_at="2026-07-06T00:00:00Z")
    second_id = db.record_run_started(started_at="2026-07-06T00:05:00Z")

    assert first_id != second_id


def test_get_recent_runs_returns_most_recent_first(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    record_finished_run(
        db,
        started_at="2026-07-06T00:00:00Z",
        finished_at="2026-07-06T00:01:00Z",
        sources_ok=["ebay"],
        sources_failed=[],
        listings_seen=5,
        alerts_sent=1,
    )
    record_finished_run(
        db,
        started_at="2026-07-07T00:00:00Z",
        finished_at="2026-07-07T00:02:00Z",
        sources_ok=["ebay"],
        sources_failed=["facebook"],
        listings_seen=8,
        alerts_sent=2,
    )

    runs = db.get_recent_runs(limit=10)

    assert [run.started_at for run in runs] == [
        "2026-07-07T00:00:00Z",
        "2026-07-06T00:00:00Z",
    ]
    assert runs[0].finished_at == "2026-07-07T00:02:00Z"
    assert runs[0].sources_ok == ["ebay"]
    assert runs[0].sources_failed == ["facebook"]
    assert runs[0].listings_seen == 8
    assert runs[0].alerts_sent == 2


def test_get_recent_runs_shows_null_finished_at_for_a_run_in_progress(
    tmp_path: Path,
) -> None:
    db = make_db(tmp_path)
    db.record_run_started(started_at="2026-07-07T00:00:00Z")

    runs = db.get_recent_runs(limit=10)

    assert len(runs) == 1
    assert runs[0].finished_at is None
    assert runs[0].sources_ok == []
    assert runs[0].sources_failed == []
    assert runs[0].listings_seen is None
    assert runs[0].alerts_sent is None


def test_get_recent_runs_respects_limit(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    for i in range(5):
        record_finished_run(
            db,
            started_at=f"2026-07-0{i + 1}T00:00:00Z",
            finished_at=f"2026-07-0{i + 1}T00:01:00Z",
            sources_ok=["ebay"],
            sources_failed=[],
            listings_seen=1,
            alerts_sent=0,
        )

    assert len(db.get_recent_runs(limit=2)) == 2


def test_get_recent_runs_returns_empty_list_when_no_runs_recorded(
    tmp_path: Path,
) -> None:
    db = make_db(tmp_path)
    assert db.get_recent_runs(limit=10) == []


def test_get_run_totals_sums_across_all_runs(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    record_finished_run(
        db,
        started_at="2026-07-06T00:00:00Z",
        finished_at="2026-07-06T00:01:00Z",
        sources_ok=["ebay"],
        sources_failed=[],
        listings_seen=5,
        alerts_sent=1,
    )
    record_finished_run(
        db,
        started_at="2026-07-07T00:00:00Z",
        finished_at="2026-07-07T00:02:00Z",
        sources_ok=["ebay"],
        sources_failed=["facebook"],
        listings_seen=8,
        alerts_sent=2,
    )

    totals = db.get_run_totals()

    assert totals.total_runs == 2
    assert totals.total_listings_seen == 13
    assert totals.total_alerts_sent == 3


def test_get_run_totals_ignores_a_run_still_in_progress(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    record_finished_run(
        db,
        started_at="2026-07-06T00:00:00Z",
        finished_at="2026-07-06T00:01:00Z",
        sources_ok=["ebay"],
        sources_failed=[],
        listings_seen=5,
        alerts_sent=1,
    )
    db.record_run_started(started_at="2026-07-07T00:00:00Z")

    totals = db.get_run_totals()

    assert totals.total_runs == 2
    assert totals.total_listings_seen == 5
    assert totals.total_alerts_sent == 1


def test_get_run_totals_is_all_zero_when_no_runs_recorded(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    totals = db.get_run_totals()

    assert totals.total_runs == 0
    assert totals.total_listings_seen == 0
    assert totals.total_alerts_sent == 0


def test_get_alert_outcome_counts_groups_by_outcome(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    record_sample_alert(db, listing_id="1", outcome="buy")
    record_sample_alert(db, listing_id="2", outcome="buy")
    record_sample_alert(db, listing_id="3", outcome="negotiate")

    assert db.get_alert_outcome_counts() == {"buy": 2, "negotiate": 1}


def test_get_alert_outcome_counts_is_empty_when_no_alerts(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    assert db.get_alert_outcome_counts() == {}


def test_get_email_batch_count_counts_distinct_report_sends(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    record_sample_alert(db, listing_id="1")
    record_sample_alert(db, listing_id="2")

    unreported = [alert.id for alert in db.get_unreported_alerts()]
    db.mark_alerts_reported(unreported, reported_at="2026-07-07T00:00:00Z")

    assert db.get_email_batch_count() == 1


def test_get_email_batch_count_counts_separate_sends_separately(
    tmp_path: Path,
) -> None:
    db = make_db(tmp_path)
    record_sample_alert(db, listing_id="1")
    first_batch = [alert.id for alert in db.get_unreported_alerts()]
    db.mark_alerts_reported(first_batch, reported_at="2026-07-07T00:00:00Z")

    record_sample_alert(db, listing_id="2")
    second_batch = [alert.id for alert in db.get_unreported_alerts()]
    db.mark_alerts_reported(second_batch, reported_at="2026-07-08T00:00:00Z")

    assert db.get_email_batch_count() == 2


def test_get_email_batch_count_is_zero_when_nothing_reported(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    assert db.get_email_batch_count() == 0


def test_get_recent_alerts_returns_most_recent_first(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    record_sample_alert(
        db, listing_id="1", title="First", alerted_at="2026-07-06T00:00:00Z"
    )
    record_sample_alert(
        db, listing_id="2", title="Second", alerted_at="2026-07-07T00:00:00Z"
    )

    recent = db.get_recent_alerts(limit=10)

    assert [alert.title for alert in recent] == ["Second", "First"]


def test_get_recent_alerts_respects_limit(tmp_path: Path) -> None:
    db = make_db(tmp_path)
    for i in range(5):
        record_sample_alert(
            db, listing_id=str(i), alerted_at=f"2026-07-0{i + 1}T00:00:00Z"
        )

    assert len(db.get_recent_alerts(limit=2)) == 2


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
