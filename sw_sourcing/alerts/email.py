"""Periodic email digest: formatting + SMTP delivery.

Formatting decides how the digest looks; it never decides what qualifies
for the digest -- that's the pipeline's job, persisted to the `alerts`
table. Cadence lives outside this module entirely (a cron entry calling
`cli.py send-report`), so "how often" is a one-line schedule change, not a
code change.
"""

from __future__ import annotations

import html
import smtplib
from collections.abc import Callable, Sequence
from email.message import EmailMessage
from typing import Any

from sw_sourcing.storage.db import AlertRecord

_OUTCOME_LABEL: dict[str, str] = {
    "buy": "Buy",
    "negotiate": "Negotiate",
    "review": "Review",
}
_OUTCOME_ORDER = ("buy", "negotiate", "review")


def _render_alert_html(alert: AlertRecord) -> str:
    parts = [
        "<div>",
        f'<a href="{alert.url}"><strong>{alert.title}</strong></a><br>',
    ]
    if alert.image_url:
        parts.append(f'<img src="{alert.image_url}" style="max-width:200px"><br>')
    if alert.target_grade_count is not None:
        parts.append(f"Target-grade figures: {alert.target_grade_count}<br>")
    if alert.cost_per_figure is not None:
        parts.append(f"Cost/figure: ${alert.cost_per_figure:.2f}<br>")
    if alert.suggested_offer is not None:
        parts.append(f"Suggested offer: ${alert.suggested_offer:.2f}<br>")
    if alert.max_repro_risk is not None:
        parts.append(f"Repro risk: {alert.max_repro_risk}<br>")
    parts.append(f"Returns accepted: {'yes' if alert.returns_accepted else 'no'}<br>")
    if alert.vision_notes:
        parts.append(f"Notes: {html.escape(alert.vision_notes)}<br>")
    parts.append("</div><hr>")
    return "".join(parts)


def format_report(alerts: Sequence[AlertRecord]) -> tuple[str, str]:
    """Build (subject, html_body) for one digest email."""
    subject = f"Sourcing report — {len(alerts)} listing(s) to review"

    grouped: dict[str, list[AlertRecord]] = {}
    for alert in alerts:
        grouped.setdefault(alert.outcome, []).append(alert)

    blocks = []
    for outcome in _OUTCOME_ORDER:
        group = grouped.get(outcome, [])
        if not group:
            continue
        label = _OUTCOME_LABEL[outcome]
        blocks.append(f"<h2>{label} ({len(group)})</h2>")
        blocks.extend(_render_alert_html(alert) for alert in group)

    html = f"<html><body>{''.join(blocks)}</body></html>"
    return subject, html


class EmailSender:
    """Sends a pre-formatted HTML digest over SMTP."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        smtp_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._username = username
        self._password = password
        self._smtp_factory = smtp_factory or (lambda: smtplib.SMTP_SSL(host, port))

    def send(self, *, to_addr: str, subject: str, html_body: str) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self._username
        message["To"] = to_addr
        message.set_content("This report requires an HTML-capable mail client.")
        message.add_alternative(html_body, subtype="html")

        with self._smtp_factory() as smtp:
            smtp.login(self._username, self._password)
            smtp.send_message(message)
