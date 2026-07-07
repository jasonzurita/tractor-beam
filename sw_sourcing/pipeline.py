"""Orchestrates one pipeline run.

adapters -> prefilter + repro-text screen -> dedupe -> vision -> decision
-> negotiation -> alerts, with per-adapter and per-listing isolation and a
heartbeat run summary. Wires together core/, storage/, and alerts/ -- it
reimplements none of their logic.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sw_sourcing.adapters.base import Adapter
from sw_sourcing.alerts.discord import DiscordAlerts, format_alert, format_heartbeat
from sw_sourcing.core.authenticity import clear_repro_risk, is_disclosed_repro
from sw_sourcing.core.decision import DecisionConfig, DecisionInput, Outcome, decide
from sw_sourcing.core.dedupe import Dedupe
from sw_sourcing.core.negotiation import suggested_offer
from sw_sourcing.core.prefilter import passes_prefilter
from sw_sourcing.core.schema import Listing
from sw_sourcing.core.vision import Vision, VisionResult
from sw_sourcing.diagnostics import (
    DEFAULT_REPORTS_DIR,
    should_report_failure,
    write_report,
)
from sw_sourcing.storage.config import Config
from sw_sourcing.storage.db import Database

logger = logging.getLogger(__name__)

_ALERTED_OUTCOMES: frozenset[Outcome] = frozenset({"buy", "negotiate", "review"})
_DAMAGED_GRADES = frozenset({"damaged", "low"})


@dataclass
class RunSummary:
    sources_ok: list[str] = field(default_factory=list)
    sources_failed: list[str] = field(default_factory=list)
    listings_seen: int = 0
    alerts_sent: int = 0
    bug_reports_written: int = 0


class Pipeline:
    def __init__(
        self,
        *,
        adapters: Mapping[str, Adapter],
        dedupe: Dedupe,
        vision: Vision,
        config: Config,
        db: Database,
        alerts: DiscordAlerts | None = None,
        bug_reports_dir: Path | str = DEFAULT_REPORTS_DIR,
    ) -> None:
        self._adapters = adapters
        self._dedupe = dedupe
        self._vision = vision
        self._config = config
        self._db = db
        self._alerts = alerts
        self._bug_reports_dir = bug_reports_dir

    def run(self) -> RunSummary:
        summary = RunSummary()
        started_at = datetime.now(UTC).isoformat()

        for source, adapter in self._adapters.items():
            try:
                listings = adapter.fetch()
            except Exception as exc:
                logger.exception("Adapter %s failed; skipping this run", source)
                summary.sources_failed.append(source)
                self._maybe_write_report(
                    key=f"adapter:{source}",
                    summary_text=f"Adapter {source} failed during fetch()",
                    context={"source": source},
                    exception=exc,
                    run_summary=summary,
                )
                continue

            summary.sources_ok.append(source)
            summary.listings_seen += len(listings)
            for listing in listings:
                try:
                    self._process(listing, summary)
                except Exception as exc:
                    logger.exception(
                        "Failed to process %s/%s; skipping",
                        listing.source,
                        listing.listing_id,
                    )
                    self._maybe_write_report(
                        key=f"listing:{listing.source}:{listing.listing_id}",
                        summary_text=(
                            f"Failed to process listing {listing.source}/"
                            f"{listing.listing_id}"
                        ),
                        context={"listing": listing},
                        exception=exc,
                        run_summary=summary,
                    )

        self._db.record_run(
            started_at=started_at,
            sources_ok=summary.sources_ok,
            sources_failed=summary.sources_failed,
            listings_seen=summary.listings_seen,
            alerts_sent=summary.alerts_sent,
        )
        if self._alerts is not None:
            self._alerts.send(
                format_heartbeat(
                    sources_ok=summary.sources_ok,
                    sources_failed=summary.sources_failed,
                    listings_seen=summary.listings_seen,
                    alerts_sent=summary.alerts_sent,
                    bug_reports_written=summary.bug_reports_written,
                )
            )
        return summary

    def _maybe_write_report(
        self,
        *,
        key: str,
        summary_text: str,
        context: dict[str, object],
        exception: Exception,
        run_summary: RunSummary,
    ) -> None:
        """Write a bug report unless one for this exact failure key already
        went out within the cooldown window -- a persistently (not just
        transiently) failing source/listing shouldn't produce a fresh
        report every single run.
        """
        now = datetime.now(UTC)
        last_reported_at = self._db.get_last_failure_report(key)
        if not should_report_failure(last_reported_at, now=now):
            return
        write_report(
            summary=summary_text,
            context=context,
            exception=exception,
            reports_dir=self._bug_reports_dir,
        )
        self._db.record_failure_report(key, reported_at=now.isoformat())
        run_summary.bug_reports_written += 1

    def _process(self, listing: Listing, summary: RunSummary) -> None:
        if not self._dedupe.is_new(listing):
            return

        if not passes_prefilter(
            listing,
            required_keywords=self._config.get("prefilter_required_keywords"),
            max_listing_price=self._config.get("prefilter_max_listing_price"),
        ):
            return

        if is_disclosed_repro(
            f"{listing.title} {listing.description}",
            blocklist=self._config.get("repro_keyword_blocklist"),
        ):
            return

        vision_result = self._vision.grade(
            images=[str(url) for url in listing.images],
            title=listing.title,
            description=listing.description,
            graded_at=datetime.now(UTC).isoformat(),
        )
        # Marked only after a successful grade, so a transient grading
        # failure (bad model output, network error) leaves the listing
        # retryable on the next run instead of dropping it forever.
        self._dedupe.mark_processed(listing, seen_at=datetime.now(UTC).isoformat())
        target_grade_count = vision_result.target_grade_count(
            grade_floor=self._config.get("grade_floor")
        )

        outcome = self._decide(listing, vision_result, target_grade_count)
        if outcome not in _ALERTED_OUTCOMES:
            return

        self._alert(listing, vision_result, target_grade_count, outcome)
        summary.alerts_sent += 1

    def _decide(
        self, listing: Listing, vision_result: VisionResult, target_grade_count: int
    ) -> Outcome:
        authenticity_clear = clear_repro_risk(
            max_repro_risk=vision_result.max_repro_risk,
            has_uncertain_grade=vision_result.has_uncertain_grade,
            max_repro_risk_for_autobuy=self._config.get("max_repro_risk_for_autobuy"),
        )
        damaged_or_low_count = sum(
            1 for item in vision_result.items if item.grade in _DAMAGED_GRADES
        )
        decision_input = DecisionInput(
            price=listing.price,
            shipping=listing.shipping,
            target_grade_count=target_grade_count,
            total_item_count=len(vision_result.items),
            damaged_or_low_count=damaged_or_low_count,
            confidence=vision_result.min_confidence,
            authenticity_clear=authenticity_clear,
            buying_option=listing.buying_option,
            offers_accepted=listing.offers_accepted,
        )
        decision_config = DecisionConfig(
            target_per_figure=self._config.get("target_per_figure"),
            negotiate_band_pct=self._config.get("negotiate_band_pct"),
            max_damage_ratio=self._config.get("max_damage_ratio"),
            confidence_floor=self._config.get("confidence_floor"),
        )
        return decide(decision_input, decision_config)

    def _alert(
        self,
        listing: Listing,
        vision_result: VisionResult,
        target_grade_count: int,
        outcome: Outcome,
    ) -> None:
        cost_per_figure = None
        if target_grade_count > 0:
            cost_per_figure = (listing.price + listing.shipping) / target_grade_count

        offer = None
        if outcome == "negotiate":
            offer = suggested_offer(
                shipping=listing.shipping,
                target_grade_count=target_grade_count,
                target_per_figure=self._config.get("target_per_figure"),
            )

        if self._alerts is not None:
            self._alerts.send(
                format_alert(
                    listing,
                    outcome,
                    cost_per_figure=cost_per_figure,
                    target_grade_count=target_grade_count,
                    suggested_offer=offer,
                    max_repro_risk=vision_result.max_repro_risk,
                    returns_accepted=listing.returns_accepted,
                )
            )
        self._db.record_alert(
            source=listing.source,
            listing_id=listing.listing_id,
            title=listing.title,
            url=str(listing.url),
            image_url=str(listing.images[0]) if listing.images else None,
            outcome=outcome,
            cost_per_figure=cost_per_figure,
            target_grade_count=target_grade_count,
            max_repro_risk=vision_result.max_repro_risk,
            returns_accepted=listing.returns_accepted,
            suggested_offer=offer,
            vision_notes=vision_result.notes or None,
            alerted_at=datetime.now(UTC).isoformat(),
        )
