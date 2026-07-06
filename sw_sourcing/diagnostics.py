"""Automatic bug reporting.

Captures unexpected errors and anomalies into human-reviewable markdown
files with context and a suggested repro -- deliberately NOT an auto-fix
mechanism. Nothing in this codebase acts on these reports automatically;
they exist so you can periodically review and fix them yourself (see
CLAUDE.md's TDD rule: reproduce with a regression test, then fix).
"""

from __future__ import annotations

import json
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

DEFAULT_REPORTS_DIR = Path("bug_reports")


def _slugify(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return str(value)


def write_report(
    *,
    summary: str,
    context: dict[str, Any],
    exception: BaseException | None = None,
    reports_dir: Path | str = DEFAULT_REPORTS_DIR,
) -> Path:
    """Write one bug report: what happened, in what context, with a repro."""
    reports_path = Path(reports_dir)
    reports_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC)
    filename = (
        f"{timestamp.strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}-"
        f"{_slugify(summary)[:60]}.md"
    )

    lines = [
        f"# {summary}",
        "",
        f"**When:** {timestamp.isoformat()}",
        "",
        "## Context",
        "```json",
        json.dumps(context, indent=2, default=_json_default),
        "```",
    ]
    if exception is not None:
        lines += [
            "",
            "## Exception",
            "```",
            "".join(
                traceback.format_exception(
                    type(exception), exception, exception.__traceback__
                )
            ).rstrip(),
            "```",
        ]
    lines += [
        "",
        "## Suggested repro",
        "1. Check the context above for exact inputs (source, listing_id,"
        " config values).",
        "2. Reproduce with a new regression test using those inputs, or"
        " replay via `python -m sw_sourcing.cli scan --source <source>`.",
        "3. Fix with a failing test first, per CLAUDE.md's TDD rule.",
        "",
    ]

    report_path = reports_path / filename
    report_path.write_text("\n".join(lines))
    return report_path
