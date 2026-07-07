import json
from pathlib import Path

from sw_sourcing.alerts.discord import DiscordAlerts
from sw_sourcing.core.dedupe import Dedupe
from sw_sourcing.core.vision import Vision
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
) -> tuple[Pipeline, FakeVisionClient, FakeHttpxClient]:
    db = Database(tmp_path / "test.db")
    config = Config(db)
    dedupe = Dedupe(db)
    vision_client = FakeVisionClient(vision_response, fail_first=fail_first)
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
    return pipeline, vision_client, discord_client


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


def test_run_sends_a_buy_alert_for_a_qualifying_listing(tmp_path: Path) -> None:
    listing = make_listing(listing_id="buy-1", price=10.0, shipping=0.0)
    pipeline, _, discord_client = make_pipeline(
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
    pipeline, vision_client, _ = make_pipeline(
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
    pipeline, vision_client, _ = make_pipeline(
        tmp_path, {"ebay": FakeAdapter([listing])}
    )

    summary = pipeline.run()

    assert vision_client.calls == 0
    assert summary.alerts_sent == 0


def test_run_never_reprocesses_a_listing_already_seen(tmp_path: Path) -> None:
    listing = make_listing(listing_id="dupe-1", price=10.0, shipping=0.0)
    pipeline, vision_client, _ = make_pipeline(
        tmp_path, {"ebay": FakeAdapter([listing])}, vision_response=BUY_RESULT
    )

    first = pipeline.run()
    second = pipeline.run()

    assert first.alerts_sent == 1
    assert second.alerts_sent == 0
    assert second.listings_seen == 1
    assert vision_client.calls == 1  # never re-billed on the reseen listing


def test_run_isolates_a_failing_adapter_and_still_processes_others(
    tmp_path: Path,
) -> None:
    good_listing = make_listing(listing_id="good-1", price=10.0, shipping=0.0)
    adapters = {
        "broken": FakeAdapter(error=RuntimeError("source down")),
        "ebay": FakeAdapter([good_listing]),
    }
    pipeline, _, _ = make_pipeline(tmp_path, adapters, vision_response=BUY_RESULT)

    summary = pipeline.run()

    assert summary.sources_failed == ["broken"]
    assert summary.sources_ok == ["ebay"]
    assert summary.alerts_sent == 1


def test_run_writes_a_bug_report_when_an_adapter_fails(tmp_path: Path) -> None:
    adapters = {"broken": FakeAdapter(error=RuntimeError("source down"))}
    pipeline, _, _ = make_pipeline(tmp_path, adapters)

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
    pipeline, vision_client, _ = make_pipeline(
        tmp_path,
        {"ebay": FakeAdapter([listing_a, listing_b])},
        vision_response=BUY_RESULT,
        fail_first=True,
    )

    summary = pipeline.run()

    assert vision_client.calls == 2
    assert summary.alerts_sent == 1  # listing_a's grading failed; listing_b succeeded


def test_run_writes_a_bug_report_when_a_listing_fails_to_process(
    tmp_path: Path,
) -> None:
    listing = make_listing(listing_id="a", price=10.0, shipping=0.0)
    pipeline, _, _ = make_pipeline(
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
    pipeline, _, discord_client = make_pipeline(
        tmp_path, {"ebay": FakeAdapter([listing])}, vision_response=BUY_RESULT
    )

    pipeline.run()

    heartbeats = [
        payload
        for _, payload in discord_client.calls
        if "Run complete" in payload["content"]
    ]
    assert len(heartbeats) == 1
