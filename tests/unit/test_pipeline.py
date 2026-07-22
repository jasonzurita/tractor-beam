import json
from pathlib import Path

from sw_sourcing.alerts.discord import DiscordAlerts
from sw_sourcing.core.dedupe import Dedupe
from sw_sourcing.core.vision import Vision, hash_listing_content
from sw_sourcing.pipeline import Pipeline
from sw_sourcing.storage.config import Config
from sw_sourcing.storage.db import Database
from tests.unit.factories import (
    FakeAdapter,
    FakeHttpxClient,
    FakeVisionClient,
    make_listing,
)

BUY_RESULT = json.dumps(
    {
        "items": [
            {
                "id": 1,
                "type": "figure",
                "grade": "high",
                "issues": [],
                "repro_risk": "low",
                "confidence": 0.9,
            },
            {
                "id": 2,
                "type": "figure",
                "grade": "high",
                "issues": [],
                "repro_risk": "low",
                "confidence": 0.9,
            },
        ],
        "photo_quality": "clear",
        "notes": "",
    }
)


def make_pipeline(
    tmp_path: Path,
    adapters: dict[str, FakeAdapter],
    *,
    vision_response: str = '{"items": [], "photo_quality": "clear", "notes": ""}',
    fail_first: bool = False,
    always_fail: bool = False,
) -> tuple[Pipeline, FakeVisionClient, FakeHttpxClient, Database]:
    db = Database(tmp_path / "test.db")
    config = Config(db)
    dedupe = Dedupe(db)
    vision_client = FakeVisionClient(
        vision_response, fail_first=fail_first, always_fail=always_fail
    )
    vision = Vision(vision_client, db)
    discord_client = FakeHttpxClient()
    alerts = DiscordAlerts("https://discord.example/webhook", client=discord_client)  # type: ignore[arg-type]
    pipeline = Pipeline(
        adapters=adapters,
        bug_reports_dir=tmp_path / "bug_reports",
        dedupe=dedupe,
        vision=vision,
        config=config,
        db=db,
        alerts=alerts,
    )
    return pipeline, vision_client, discord_client, db


def test_run_works_without_discord_configured_and_still_persists_alerts(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    config = Config(db)
    dedupe = Dedupe(db)
    vision = Vision(FakeVisionClient(BUY_RESULT), db)
    listing = make_listing(listing_id="buy-1", price=10.0, shipping=0.0)
    pipeline = Pipeline(
        adapters={"ebay": FakeAdapter([listing])},
        bug_reports_dir=tmp_path / "bug_reports",
        dedupe=dedupe,
        vision=vision,
        config=config,
        db=db,
        alerts=None,
    )

    summary = pipeline.run()

    assert summary.alerts_sent == 1
    unreported = db.get_unreported_alerts()
    assert len(unreported) == 1
    assert unreported[0].title == listing.title
    assert unreported[0].url == str(listing.url)


def test_run_records_a_completed_run_with_started_and_finished_timestamps(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "test.db")
    config = Config(db)
    dedupe = Dedupe(db)
    vision = Vision(FakeVisionClient(BUY_RESULT), db)
    listing = make_listing(listing_id="buy-1", price=10.0, shipping=0.0)
    pipeline = Pipeline(
        adapters={"ebay": FakeAdapter([listing])},
        bug_reports_dir=tmp_path / "bug_reports",
        dedupe=dedupe,
        vision=vision,
        config=config,
        db=db,
        alerts=None,
    )

    pipeline.run()

    runs = db.get_recent_runs(limit=1)
    assert len(runs) == 1
    assert runs[0].started_at is not None
    assert runs[0].finished_at is not None
    assert runs[0].sources_ok == ["ebay"]


def test_run_sends_a_buy_alert_for_a_qualifying_listing(tmp_path: Path) -> None:
    listing = make_listing(listing_id="buy-1", price=10.0, shipping=0.0)
    pipeline, _, discord_client, _ = make_pipeline(
        tmp_path, {"ebay": FakeAdapter([listing])}, vision_response=BUY_RESULT
    )

    summary = pipeline.run()

    assert summary.alerts_sent == 1
    buy_payloads = [
        payload for _, payload in discord_client.calls if "BUY" in payload["content"]
    ]
    assert len(buy_payloads) == 1


def test_run_skips_disclosed_repro_without_calling_vision(tmp_path: Path) -> None:
    listing = make_listing(
        listing_id="repro-1", description="This lot includes a reproduction weapon"
    )
    pipeline, vision_client, _, _ = make_pipeline(
        tmp_path, {"ebay": FakeAdapter([listing])}
    )

    summary = pipeline.run()

    assert vision_client.calls == 0
    assert summary.alerts_sent == 0


def test_run_skips_self_disclosed_era_mismatch_without_calling_vision(
    tmp_path: Path,
) -> None:
    listing = make_listing(
        listing_id="era-1",
        title="Vintage Star Wars ACTION FIGURES Kenner 90'S Lot Of 5",
    )
    pipeline, vision_client, _, _ = make_pipeline(
        tmp_path, {"ebay": FakeAdapter([listing])}
    )

    summary = pipeline.run()

    assert vision_client.calls == 0
    assert summary.alerts_sent == 0


def test_run_skips_off_topic_listings_without_calling_vision(tmp_path: Path) -> None:
    listing = make_listing(
        listing_id="offtopic-1",
        title="Modern toy",
        description="brand new, unrelated item",
    )
    pipeline, vision_client, _, _ = make_pipeline(
        tmp_path, {"ebay": FakeAdapter([listing])}
    )

    summary = pipeline.run()

    assert vision_client.calls == 0
    assert summary.alerts_sent == 0


def test_run_does_not_realert_the_same_listing_at_an_unchanged_price(
    tmp_path: Path,
) -> None:
    listing = make_listing(listing_id="dupe-1", price=10.0, shipping=0.0)
    pipeline, vision_client, _, _ = make_pipeline(
        tmp_path, {"ebay": FakeAdapter([listing])}, vision_response=BUY_RESULT
    )

    first = pipeline.run()
    second = pipeline.run()

    assert first.alerts_sent == 1
    assert second.alerts_sent == 0
    assert second.listings_seen == 1
    assert vision_client.calls == 1  # never re-billed on the reseen listing


def test_run_realerts_when_a_previously_skipped_listing_drops_in_price(
    tmp_path: Path,
) -> None:
    single_high_grade_item = json.dumps(
        {
            "items": [
                {
                    "id": 1,
                    "type": "figure",
                    "grade": "high",
                    "issues": [],
                    "repro_risk": "low",
                    "confidence": 0.9,
                },
            ],
            "photo_quality": "clear",
            "notes": "",
        }
    )
    listing = make_listing(
        listing_id="price-drop-1",
        price=100.0,
        shipping=0.0,
        offers_accepted=False,
        buying_option="fixed_price",
    )
    adapter = FakeAdapter([listing])
    pipeline, vision_client, _, _ = make_pipeline(
        tmp_path, {"ebay": adapter}, vision_response=single_high_grade_item
    )

    first = pipeline.run()
    assert first.alerts_sent == 0  # $100 for one target-grade figure: too expensive

    adapter.set_listings([listing.model_copy(update={"price": 4.0})])
    second = pipeline.run()

    assert second.alerts_sent == 1  # same listing, price dropped into buy range
    assert vision_client.calls == 1  # same images -> vision_cache hit, no re-billing


def test_run_flags_a_price_change_on_a_realert_after_a_price_drop(
    tmp_path: Path,
) -> None:
    listing = make_listing(listing_id="review-1", price=5.0, shipping=0.0)
    rare_candidate_forces_review = json.dumps(
        {
            "items": [
                {
                    "id": 1,
                    "type": "figure",
                    "grade": "high",
                    "issues": [],
                    "repro_risk": "low",
                    "confidence": 0.9,
                    "rare_candidate": True,
                    "rarity_notes": "Possible long-saber variant.",
                },
            ],
            "photo_quality": "clear",
            "notes": "",
        }
    )
    adapter = FakeAdapter([listing])
    pipeline, _, discord_client, db = make_pipeline(
        tmp_path, {"ebay": adapter}, vision_response=rare_candidate_forces_review
    )

    first = pipeline.run()
    assert first.alerts_sent == 1

    adapter.set_listings([listing.model_copy(update={"price": 3.0})])
    second = pipeline.run()

    assert second.alerts_sent == 1
    review_payloads = [
        payload for _, payload in discord_client.calls if "REVIEW" in payload["content"]
    ]
    assert len(review_payloads) == 2
    assert "5.00" in review_payloads[1]["content"]
    assert "3.00" in review_payloads[1]["content"]

    unreported = db.get_unreported_alerts()
    latest = max(unreported, key=lambda alert: alert.id)
    assert latest.previous_price == 5.0


def test_run_pages_deeper_when_the_first_page_has_no_fresh_listings(
    tmp_path: Path,
) -> None:
    stale = make_listing(
        listing_id="stale-1", price=10.0, images=["https://example.com/stale.jpg"]
    )
    fresh = make_listing(
        listing_id="fresh-1", price=10.0, images=["https://example.com/fresh.jpg"]
    )
    adapter = FakeAdapter(pages={0: [stale], 1: [fresh]})
    pipeline, vision_client, _, db = make_pipeline(
        tmp_path, {"ebay": adapter}, vision_response=BUY_RESULT
    )
    Config(db).set("vision_analysis_budget_per_run", 1)
    # Pre-cache `stale`'s grade so page 0 contributes zero *fresh* analyses,
    # forcing the pipeline to page deeper to fill the budget of 1.
    db.put_vision_cache(
        hash_listing_content(
            images=["https://example.com/stale.jpg"],
            title=stale.title,
            description=stale.description,
        ),
        BUY_RESULT,
        created_at="2026-07-06T00:00:00Z",
    )

    summary = pipeline.run()

    assert adapter.fetch_offsets == [0, 1]
    assert summary.listings_seen == 2
    assert vision_client.calls == 1  # stale: cache hit; fresh: one real grading call


def test_run_stops_paging_once_the_analysis_budget_is_met(tmp_path: Path) -> None:
    first_page = [
        make_listing(listing_id="a", price=10.0, images=["https://example.com/a.jpg"])
    ]
    second_page = [
        make_listing(listing_id="b", price=10.0, images=["https://example.com/b.jpg"])
    ]
    adapter = FakeAdapter(pages={0: first_page, 1: second_page})
    pipeline, _, _, db = make_pipeline(
        tmp_path, {"ebay": adapter}, vision_response=BUY_RESULT
    )
    Config(db).set("vision_analysis_budget_per_run", 1)

    summary = pipeline.run()

    assert adapter.fetch_offsets == [0]  # budget met on page 0; page 1 never fetched
    assert summary.listings_seen == 1


def test_run_stops_paging_at_the_max_fetch_pages_safety_cap(tmp_path: Path) -> None:
    pages = {
        offset: [
            make_listing(
                listing_id=f"l{offset}",
                price=10.0,
                images=[f"https://example.com/{offset}.jpg"],
            )
        ]
        for offset in range(5)
    }
    adapter = FakeAdapter(pages=pages)
    pipeline, _, _, db = make_pipeline(
        tmp_path, {"ebay": adapter}, vision_response=BUY_RESULT
    )
    config = Config(db)
    config.set("vision_analysis_budget_per_run", 100)  # unreachable in 2 pages
    config.set("max_fetch_pages_per_source", 2)

    summary = pipeline.run()

    assert adapter.fetch_offsets == [0, 1]  # stopped at the cap, not exhaustion
    assert summary.listings_seen == 2


def test_run_persists_the_vision_models_notes_on_the_alert(tmp_path: Path) -> None:
    result_with_notes = json.dumps(
        {
            "items": [
                {
                    "id": 1,
                    "type": "figure",
                    "grade": "high",
                    "issues": [],
                    "repro_risk": "low",
                    "confidence": 0.9,
                },
            ],
            "photo_quality": "clear",
            "notes": "No backstamp visible on one droid; recommend a closer look.",
        }
    )
    listing = make_listing(listing_id="notes-1", price=5.0, shipping=0.0)
    pipeline, _, _, db = make_pipeline(
        tmp_path, {"ebay": FakeAdapter([listing])}, vision_response=result_with_notes
    )

    pipeline.run()

    unreported = db.get_unreported_alerts()
    assert unreported[0].vision_notes == (
        "No backstamp visible on one droid; recommend a closer look."
    )


def test_run_routes_a_rare_candidate_to_review_even_at_low_repro_risk(
    tmp_path: Path,
) -> None:
    rare_but_low_risk = json.dumps(
        {
            "items": [
                {
                    "id": 1,
                    "type": "figure",
                    "grade": "high",
                    "issues": [],
                    "repro_risk": "low",
                    "confidence": 0.9,
                },
                {
                    "id": 2,
                    "type": "weapon",
                    "grade": "high",
                    "issues": [],
                    "repro_risk": "low",
                    "confidence": 0.9,
                    "rare_candidate": True,
                    "rarity_notes": "Matches the long-saber variant.",
                },
            ],
            "photo_quality": "clear",
            "notes": "",
        }
    )
    # Priced to clear the figure economics easily -- this proves the review
    # routing comes from the rarity flag itself, not from the price/cost math.
    listing = make_listing(listing_id="rare-1", price=5.0, shipping=0.0)
    pipeline, _, discord_client, db = make_pipeline(
        tmp_path,
        {"ebay": FakeAdapter([listing])},
        vision_response=rare_but_low_risk,
    )

    summary = pipeline.run()

    assert summary.alerts_sent == 1
    review_payloads = [
        payload for _, payload in discord_client.calls if "REVIEW" in payload["content"]
    ]
    assert len(review_payloads) == 1
    unreported = db.get_unreported_alerts()
    assert unreported[0].outcome == "review"


def test_run_isolates_a_failing_adapter_and_still_processes_others(
    tmp_path: Path,
) -> None:
    good_listing = make_listing(listing_id="good-1", price=10.0, shipping=0.0)
    adapters = {
        "broken": FakeAdapter(error=RuntimeError("source down")),
        "ebay": FakeAdapter([good_listing]),
    }
    pipeline, _, _, _ = make_pipeline(tmp_path, adapters, vision_response=BUY_RESULT)

    summary = pipeline.run()

    assert summary.sources_failed == ["broken"]
    assert summary.sources_ok == ["ebay"]
    assert summary.alerts_sent == 1


def test_run_writes_a_bug_report_when_an_adapter_fails(tmp_path: Path) -> None:
    adapters = {"broken": FakeAdapter(error=RuntimeError("source down"))}
    pipeline, _, _, _ = make_pipeline(tmp_path, adapters)

    summary = pipeline.run()

    reports = list((tmp_path / "bug_reports").glob("*.md"))
    assert summary.bug_reports_written == 1
    assert len(reports) == 1
    assert "source down" in reports[0].read_text()


def test_run_isolates_a_failing_listing_and_still_processes_the_rest(
    tmp_path: Path,
) -> None:
    listing_a = make_listing(
        listing_id="a", price=10.0, shipping=0.0, images=["https://example.com/a.jpg"]
    )
    listing_b = make_listing(
        listing_id="b", price=10.0, shipping=0.0, images=["https://example.com/b.jpg"]
    )
    pipeline, vision_client, _, _ = make_pipeline(
        tmp_path,
        {"ebay": FakeAdapter([listing_a, listing_b])},
        vision_response=BUY_RESULT,
        fail_first=True,
    )

    summary = pipeline.run()

    assert vision_client.calls == 2
    assert summary.alerts_sent == 1  # listing_a's grading failed; listing_b succeeded


def test_run_retries_a_listing_after_a_grading_failure_instead_of_dropping_it(
    tmp_path: Path,
) -> None:
    listing = make_listing(listing_id="retry-1", price=10.0, shipping=0.0)
    pipeline, vision_client, _, _ = make_pipeline(
        tmp_path,
        {"ebay": FakeAdapter([listing])},
        vision_response=BUY_RESULT,
        fail_first=True,
    )

    first = pipeline.run()
    assert first.alerts_sent == 0
    assert vision_client.calls == 1

    second = pipeline.run()
    assert second.alerts_sent == 1  # retried on the next run, not dropped forever
    assert vision_client.calls == 2


def test_run_writes_a_bug_report_when_a_listing_fails_to_process(
    tmp_path: Path,
) -> None:
    listing = make_listing(listing_id="a", price=10.0, shipping=0.0)
    pipeline, _, _, _ = make_pipeline(
        tmp_path,
        {"ebay": FakeAdapter([listing])},
        vision_response=BUY_RESULT,
        fail_first=True,
    )

    summary = pipeline.run()

    reports = list((tmp_path / "bug_reports").glob("*.md"))
    assert summary.bug_reports_written == 1
    assert len(reports) == 1
    content = reports[0].read_text()
    assert "a" in content
    assert "simulated grading failure" in content


def test_run_sends_a_heartbeat_after_processing(tmp_path: Path) -> None:
    listing = make_listing(listing_id="hb-1", price=10.0, shipping=0.0)
    pipeline, _, discord_client, _ = make_pipeline(
        tmp_path, {"ebay": FakeAdapter([listing])}, vision_response=BUY_RESULT
    )

    pipeline.run()

    heartbeats = [
        payload
        for _, payload in discord_client.calls
        if "Run complete" in payload["content"]
    ]
    assert len(heartbeats) == 1


def test_run_suppresses_repeat_bug_reports_for_the_same_listing_within_cooldown(
    tmp_path: Path,
) -> None:
    listing = make_listing(listing_id="always-fails", price=10.0, shipping=0.0)
    pipeline, vision_client, _, _ = make_pipeline(
        tmp_path,
        {"ebay": FakeAdapter([listing])},
        vision_response=BUY_RESULT,
        always_fail=True,
    )

    first = pipeline.run()
    second = pipeline.run()

    assert first.bug_reports_written == 1
    assert second.bug_reports_written == 0  # same listing, still within cooldown
    assert vision_client.calls == 2  # the retry itself still happens every run
    assert len(list((tmp_path / "bug_reports").glob("*.md"))) == 1


def test_run_writes_a_new_bug_report_once_the_cooldown_expires(
    tmp_path: Path,
) -> None:
    listing = make_listing(listing_id="always-fails", price=10.0, shipping=0.0)
    pipeline, _, _, db = make_pipeline(
        tmp_path,
        {"ebay": FakeAdapter([listing])},
        vision_response=BUY_RESULT,
        always_fail=True,
    )

    first = pipeline.run()
    assert first.bug_reports_written == 1

    db.record_failure_report(
        f"listing:{listing.source}:{listing.listing_id}",
        reported_at="2020-01-01T00:00:00+00:00",
    )
    second = pipeline.run()

    assert second.bug_reports_written == 1
    assert len(list((tmp_path / "bug_reports").glob("*.md"))) == 2


def test_run_suppresses_repeat_bug_reports_for_the_same_failing_adapter(
    tmp_path: Path,
) -> None:
    adapters = {"broken": FakeAdapter(error=RuntimeError("source down"))}
    pipeline, _, _, _ = make_pipeline(tmp_path, adapters)

    first = pipeline.run()
    second = pipeline.run()

    assert first.bug_reports_written == 1
    assert second.bug_reports_written == 0
