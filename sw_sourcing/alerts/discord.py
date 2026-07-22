"""Discord alert formatting and delivery.

Formatting decides how an alert looks; it never decides whether something
qualifies as an alert — that's the decision engine's job upstream.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from sw_sourcing.core.decision import Outcome
from sw_sourcing.core.schema import Listing

_OUTCOME_EMOJI: dict[Outcome, str] = {
    "buy": "🟢",
    "negotiate": "🟡",
    "review": "🔎",
    "skip": "⚪",
}


def format_alert(
    listing: Listing,
    outcome: Outcome,
    *,
    cost_per_figure: float | None = None,
    cost_per_weapon: float | None = None,
    target_grade_count: int | None = None,
    suggested_offer: float | None = None,
    max_repro_risk: str | None = None,
    returns_accepted: bool | None = None,
    previous_price: float | None = None,
) -> dict[str, Any]:
    """Build a Discord webhook payload for one listing decision."""
    lines = [f"{_OUTCOME_EMOJI[outcome]} **{outcome.upper()}** — {listing.title}"]
    lines.append(str(listing.url))
    if previous_price is not None and previous_price != listing.price:
        lines.append(
            f"💲 Price changed since last alert: ${previous_price:.2f} → "
            f"${listing.price:.2f}"
        )
    if target_grade_count is not None:
        lines.append(f"Target-grade figures: {target_grade_count}")
    if cost_per_figure is not None:
        lines.append(f"Cost/figure: ${cost_per_figure:.2f}")
    if cost_per_weapon is not None:
        lines.append(f"Cost/weapon: ${cost_per_weapon:.2f}")
    if suggested_offer is not None:
        lines.append(f"Suggested offer: ${suggested_offer:.2f}")
    if max_repro_risk is not None:
        lines.append(f"Repro risk: {max_repro_risk}")
    if returns_accepted is not None:
        lines.append(f"Returns accepted: {'yes' if returns_accepted else 'no'}")

    return {"content": "\n".join(lines)}


def format_heartbeat(
    *,
    sources_ok: Sequence[str],
    sources_failed: Sequence[str],
    listings_seen: int,
    alerts_sent: int,
    bug_reports_written: int = 0,
) -> dict[str, Any]:
    """Build a Discord webhook payload for one run's heartbeat summary."""
    status = "✅" if not sources_failed else "⚠️"
    lines = [
        f"{status} Run complete — {listings_seen} listings seen, "
        f"{alerts_sent} alerts sent.",
        f"Sources OK: {', '.join(sources_ok) or 'none'}",
    ]
    if sources_failed:
        lines.append(f"Sources failed: {', '.join(sources_failed)}")
    if bug_reports_written:
        lines.append(
            f"🐛 {bug_reports_written} bug report(s) written to bug_reports/ "
            "for review"
        )

    return {"content": "\n".join(lines)}


class DiscordAlerts:
    """Sends already-formatted payloads to a Discord webhook."""

    def __init__(self, webhook_url: str, *, client: httpx.Client | None = None) -> None:
        self._webhook_url = webhook_url
        self._client = client or httpx.Client(timeout=10.0)

    def send(self, payload: dict[str, Any]) -> None:
        response = self._client.post(self._webhook_url, json=payload)
        response.raise_for_status()
