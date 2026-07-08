"""Orchestrates one pipeline run.

adapters -> prefilter + repro-text screen -> vision -> decision ->
negotiation -> alert-dedupe -> alerts, with per-adapter and per-listing
isolation and a heartbeat run summary. Wires together core/, storage/, and
alerts/ -- it reimplements none of their logic.
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
        run_id = self._db.record_run_started(started_at=started_at)

        for source, adapter in self._adapters.items():
            try:
                listings = self._fetch_for_analysis(adapter)
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

        self._db.record_run_finished(
            run_id=run_id,
            finished_at=datetime.now(UTC).isoformat(),
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

    def _fetch_for_analysis(self, adapter: Adapter) -> list[Listing]:
        """Pages an adapter until enough listings need a *fresh* vision
        grade to fill this run's analysis budget, the source runs out of
        listings, or a safety cap on page fetches is hit.

        A single page (e.g. eBay's newest-50) can be mostly listings this
        pipeline has already graded before -- without paging deeper on
        those runs, the pipeline would look busy while barely analyzing
        anything new.
        """
        budget = self._config.get("vision_analysis_budget_per_run")
        max_pages = self._config.get("max_fetch_pages_per_source")

        listings: list[Listing] = []
        fresh_count = 0
        offset = 0
        for _ in range(max_pages):
            page = adapter.fetch(offset=offset)
            if not page:
                break
            listings.extend(page)
            fresh_count += sum(
                1 for listing in page if self._needs_fresh_analysis(listing)
            )
            offset += len(page)
            if fresh_count >= budget:
                break
        return listings

    def _needs_fresh_analysis(self, listing: Listing) -> bool:
        if not self._passes_free_screen(listing):
            return False
        return not self._vision.has_cached_grade(
            images=[str(url) for url in listing.images],
            title=listing.title,
            description=listing.description,
        )

    def _passes_free_screen(self, listing: Listing) -> bool:
        if not passes_prefilter(
            listing,
            required_keywords=self._config.get("prefilter_required_keywords"),
            max_listing_price=self._config.get("prefilter_max_listing_price"),
        ):
            return False
        if is_disclosed_repro(
            f"{listing.title} {listing.description}",
            blocklist=self._config.get("repro_keyword_blocklist"),
        ):
            return False
        return True

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
        if not self._passes_free_screen(listing):
            return

        # Grading is billed once per images/title/description combo (see
        # vision_cache) regardless of how many times a still-live listing
        # is re-fetched, so re-running this on every scan costs nothing
        # extra on a repeat listing -- it's what lets a later price change
        # on the same listing get a fresh decision instead of being
        # silently dropped forever.
        vision_result = self._vision.grade(
            images=[str(url) for url in listing.images],
            title=listing.title,
            description=listing.description,
            graded_at=datetime.now(UTC).isoformat(),
        )
        target_grade_count = vision_result.target_grade_count(
            grade_floor=self._config.get("grade_floor")
        )

        outcome = self._decide(listing, vision_result, target_grade_count)
        if outcome not in _ALERTED_OUTCOMES:
            return

        if self._dedupe.already_alerted(listing, outcome=outcome):
            return

        self._alert(listing, vision_result, target_grade_count, outcome)
        summary.alerts_sent += 1

    def _decide(
        self, listing: Listing, vision_result: VisionResult, target_grade_count: int
    ) -> Outcome:
        authenticity_clear = clear_repro_risk(
            max_repro_risk=vision_result.max_repro_risk,
            has_uncertain_grade=vision_result.has_uncertain_grade,
            has_rare_candidate=vision_result.has_rare_candidate,
            max_repro_risk_for_autobuy=self._config.get("max_repro_risk_for_autobuy"),
        )
        damaged_or_low_count = sum(
            1 for item in vision_result.items if item.grade in _DAMAGED_GRADES
        )
        decision_input = DecisionInput(
            price=listing.price,
            shipping=listing.shipping,
            target_grade_count=target_grade_count,
            authentic_weapon_count=vision_result.authentic_weapon_count,
            total_item_count=len(vision_result.items),
            damaged_or_low_count=damaged_or_low_count,
            confidence=vision_result.min_confidence,
            authenticity_clear=authenticity_clear,
            buying_option=listing.buying_option,
            offers_accepted=listing.offers_accepted,
        )
        # target_per_weapon defaults to None (price TBD, per the spec) --
        # 0.0 makes that a no-op: no weapon credit on figure lots, and a
        # weapon-only lot always skips rather than pricing against nothing.
        target_per_weapon = self._config.get("target_per_weapon")
        decision_config = DecisionConfig(
            target_per_figure=self._config.get("target_per_figure"),
            target_per_weapon=(
                target_per_weapon if target_per_weapon is not None else 0.0
            ),
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
        authentic_weapon_count = vision_result.authentic_weapon_count
        target_per_figure = self._config.get("target_per_figure")
        target_per_weapon = self._config.get("target_per_weapon")
        target_per_weapon = target_per_weapon if target_per_weapon is not None else 0.0
        total_cost = listing.price + listing.shipping

        cost_per_figure = None
        cost_per_weapon = None
        if target_grade_count > 0:
            weapon_credit = authentic_weapon_count * target_per_weapon
            effective_cost = max(0.0, total_cost - weapon_credit)
            cost_per_figure = effective_cost / target_grade_count
        elif authentic_weapon_count > 0:
            cost_per_weapon = total_cost / authentic_weapon_count

        offer = None
        if outcome == "negotiate":
            if target_grade_count > 0:
                # The figure-based offer already undercuts target_per_figure
                # slightly; the weapon credit is added on top at full value
                # rather than also undercut, since target_per_weapon is
                # already treated as a fair price, not a haggling ceiling.
                weapon_credit = authentic_weapon_count * target_per_weapon
                offer = suggested_offer(
                    shipping=listing.shipping,
                    target_grade_count=target_grade_count,
                    target_per_figure=target_per_figure,
                )
                offer = round(offer + weapon_credit, 2)
            else:
                offer = suggested_offer(
                    shipping=listing.shipping,
                    target_grade_count=authentic_weapon_count,
                    target_per_figure=target_per_weapon,
                )

        notes_parts = [vision_result.notes] if vision_result.notes else []
        if vision_result.rare_items_summary:
            notes_parts.append(
                f"⭐ Possible rare item: {vision_result.rare_items_summary}"
            )
        combined_notes = " | ".join(notes_parts) or None

        if self._alerts is not None:
            self._alerts.send(
                format_alert(
                    listing,
                    outcome,
                    cost_per_figure=cost_per_figure,
                    cost_per_weapon=cost_per_weapon,
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
            vision_notes=combined_notes,
            cost_per_weapon=cost_per_weapon,
            price=listing.price,
            alerted_at=datetime.now(UTC).isoformat(),
        )
