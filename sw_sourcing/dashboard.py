"""Static local observability dashboard.

Read-only: pulls run/alert history from `storage/db.py` and bug reports
from disk, decides nothing, sends nothing. `build_dashboard_data` does the
I/O; `render_dashboard_html` is a pure formatter so it's cheap to test.
Freshness is exactly "as of the last time the CLI's `dashboard` command
ran" -- there is no live refresh, by design (see CLAUDE.md: this is a
regenerate-on-demand snapshot, not a server).
"""

from __future__ import annotations

import html
import os
from dataclasses import dataclass
from pathlib import Path

from sw_sourcing import lock
from sw_sourcing.storage.db import AlertRecord, Database, RunRecord, RunTotals

_OUTCOME_EMOJI: dict[str, str] = {
    "buy": "🟢",
    "negotiate": "🟡",
    "review": "🔎",
    "skip": "⚪",
}
_OUTCOME_ORDER = ("buy", "negotiate", "review", "skip")


@dataclass(frozen=True)
class BugReportEntry:
    filename: str
    title: str
    when: str


@dataclass(frozen=True)
class DashboardData:
    generated_at: str
    totals: RunTotals
    email_batch_count: int
    outcome_counts: dict[str, int]
    recent_runs: list[RunRecord]
    recent_alerts: list[AlertRecord]
    bug_reports: list[BugReportEntry]
    scan_running: bool
    db_path: str
    log_path: str
    lock_path: str
    bug_reports_dir: str
    cwd: str


def _is_scan_running(lock_path: Path | str) -> bool:
    """True if another process currently holds the scan lock.

    Uses the same non-blocking flock as `lock.py` itself: if we can
    acquire it, nothing else has it (and we immediately release); if we
    can't, a scan is running right now. There's no separate "stale lock"
    state to detect -- flock releases automatically when the holding
    process exits or is killed.
    """
    with lock.acquire(lock_path) as acquired:
        return not acquired


def collect_bug_reports(
    reports_dir: Path | str, *, limit: int = 20
) -> list[BugReportEntry]:
    """Newest-first bug reports still sitting on disk.

    diagnostics.py never deletes these (see CLAUDE.md: no self-healing);
    a report "exists" here means a human hasn't reviewed/cleared it yet.
    """
    path = Path(reports_dir)
    if not path.exists():
        return []

    entries = []
    for report_path in sorted(path.glob("*.md"), reverse=True)[:limit]:
        first_line = report_path.read_text().splitlines()[0]
        title = first_line.removeprefix("# ").strip()
        when = report_path.stem.split("-", 1)[0]
        entries.append(
            BugReportEntry(filename=report_path.name, title=title, when=when)
        )
    return entries


def build_dashboard_data(
    db: Database,
    *,
    bug_reports_dir: Path | str,
    generated_at: str,
    db_path: str = "sw_sourcing.db",
    log_path: str = "sw_sourcing.log",
    lock_path: str = "sw_sourcing.scan.lock",
    recent_limit: int = 20,
) -> DashboardData:
    return DashboardData(
        generated_at=generated_at,
        totals=db.get_run_totals(),
        email_batch_count=db.get_email_batch_count(),
        outcome_counts=db.get_alert_outcome_counts(),
        recent_runs=db.get_recent_runs(limit=recent_limit),
        recent_alerts=db.get_recent_alerts(limit=recent_limit),
        bug_reports=collect_bug_reports(bug_reports_dir, limit=recent_limit),
        scan_running=_is_scan_running(lock_path),
        db_path=db_path,
        log_path=log_path,
        lock_path=lock_path,
        bug_reports_dir=str(bug_reports_dir),
        cwd=os.getcwd(),
    )


def _bar(count: int, max_count: int) -> str:
    pct = round(100 * count / max_count) if max_count else 0
    return f'<div class="bar" style="width:{pct}%"></div>'


def _stat_tile(label: str, value: object) -> str:
    return (
        '<div class="tile">'
        f'<div class="tile-value">{html.escape(str(value))}</div>'
        f'<div class="tile-label">{html.escape(label)}</div>'
        "</div>"
    )


def _render_outcome_breakdown(outcome_counts: dict[str, int]) -> str:
    max_count = max(outcome_counts.values(), default=0)
    rows = []
    for outcome in _OUTCOME_ORDER:
        count = outcome_counts.get(outcome, 0)
        if count == 0 and outcome not in outcome_counts:
            continue
        emoji = _OUTCOME_EMOJI.get(outcome, "")
        rows.append(
            "<div class='outcome-row'>"
            f"<span class='outcome-label'>{emoji} {html.escape(outcome)}</span>"
            f"<div class='outcome-bar-track'>{_bar(count, max_count)}</div>"
            f"<span class='outcome-count'>{count}</span>"
            "</div>"
        )
    return "".join(rows) or "<p>No alerts yet.</p>"


def _render_recent_runs(runs: list[RunRecord]) -> str:
    if not runs:
        return "<p>No scans recorded yet.</p>"
    max_listings = max((run.listings_seen for run in runs), default=0)
    rows = []
    for run in runs:
        failed = ", ".join(html.escape(s) for s in run.sources_failed)
        failed_cell = f"<span class='failed'>{failed}</span>" if failed else "—"
        rows.append(
            "<tr>"
            f"<td>{html.escape(run.started_at)}</td>"
            f"<td>{html.escape(', '.join(run.sources_ok)) or '—'}</td>"
            f"<td>{failed_cell}</td>"
            f"<td>{run.listings_seen} {_bar(run.listings_seen, max_listings)}</td>"
            f"<td>{run.alerts_sent}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>Started</th><th>Sources OK</th><th>Sources failed</th>"
        "<th>Listings seen</th><th>Alerts sent</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _render_recent_alerts(alerts: list[AlertRecord]) -> str:
    if not alerts:
        return "<p>No alerts yet.</p>"
    rows = []
    for alert in alerts:
        emoji = _OUTCOME_EMOJI.get(alert.outcome, "")
        price = f"${alert.price:.2f}" if alert.price is not None else "—"
        rows.append(
            "<li>"
            f"{emoji} <a href='{html.escape(alert.url)}'>"
            f"{html.escape(alert.title)}</a> — {price}"
            f" <span class='when'>{html.escape(alert.alerted_at)}</span>"
            "</li>"
        )
    return "<ul class='alert-feed'>" + "".join(rows) + "</ul>"


def _render_bug_reports(reports: list[BugReportEntry]) -> str:
    if not reports:
        return "<p class='all-clear'>No open bug reports 🎉</p>"
    rows = []
    for report in reports:
        rows.append(
            "<li>"
            f"<span class='when'>{html.escape(report.when)}</span> "
            f"{html.escape(report.title)} "
            f"<span class='filename'>({html.escape(report.filename)})</span>"
            "</li>"
        )
    return "<ul class='bug-reports'>" + "".join(rows) + "</ul>"


def _render_runbook(data: DashboardData) -> str:
    db_path = html.escape(data.db_path)
    log_path = html.escape(data.log_path)
    lock_path = html.escape(data.lock_path)
    bug_reports_dir = html.escape(data.bug_reports_dir)
    cwd = html.escape(data.cwd)
    return f"""
<h3>Is it running right now?</h3>
<p>The status tile above reflects the scan lock as of when this page was
generated. To check live:</p>
<pre>ps aux | grep "sw_sourcing.cli scan"
lsof {lock_path}   # shows the PID holding the lock, if any</pre>

<h3>Something looks stuck or needs a restart</h3>
<p>There's no long-running service here to restart -- <code>scan</code>,
<code>send-report</code>, and <code>dashboard</code> are all one-shot
commands fired by cron. A wedged scan is safe to kill directly: the lock
is a kernel flock tied to the process, so killing it frees the lock
immediately and there's nothing to clean up by hand.</p>
<pre>pkill -f "sw_sourcing.cli scan"</pre>
<p>Then see what happened:</p>
<pre>tail -100 {log_path}
ls {bug_reports_dir}</pre>

<h3>Start scan + email reports on a schedule</h3>
<pre>crontab -e
# add lines like (cadences are independent -- edit either without the other):
*/30 * * * * cd {cwd} &amp;&amp; .venv/bin/python -m sw_sourcing.cli scan &gt;&gt; /dev/null 2&gt;&amp;1
0 9 * * *    cd {cwd} &amp;&amp; .venv/bin/python -m sw_sourcing.cli send-report &gt;&gt; /dev/null 2&gt;&amp;1
*/10 * * * * cd {cwd} &amp;&amp; .venv/bin/python -m sw_sourcing.cli dashboard &gt;&gt; /dev/null 2&gt;&amp;1</pre>
<p>Check what's currently scheduled: <code>crontab -l</code>. On macOS,
cron needs Full Disk Access (System Settings &rarr; Privacy &amp;
Security) or these entries silently no-op.</p>
<p class="paths">DB: <code>{db_path}</code> &middot; Log:
<code>{log_path}</code> &middot; Lock: <code>{lock_path}</code></p>
"""


_STYLE = """
body { font-family: -apple-system, sans-serif; background: #0f1115; color: #e6e6e6;
       margin: 0; padding: 2rem; }
h1 { margin-bottom: 0.25rem; }
.generated-at { color: #888; margin-bottom: 2rem; }
.tiles { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 2rem; }
.tile { background: #1a1d24; border-radius: 8px; padding: 1rem 1.5rem;
        min-width: 140px; }
.tile-value { font-size: 2rem; font-weight: 700; }
.tile-label { color: #999; font-size: 0.85rem; }
section { margin-bottom: 2.5rem; }
h2 { border-bottom: 1px solid #2a2d36; padding-bottom: 0.5rem; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #2a2d36; }
.bar { display: inline-block; height: 8px; background: #4caf50; border-radius: 4px;
       vertical-align: middle; margin-left: 0.5rem; }
.outcome-row { display: flex; align-items: center; gap: 0.75rem; margin: 0.4rem 0; }
.outcome-label { width: 120px; }
.outcome-bar-track { flex: 1; background: #1a1d24; border-radius: 4px; }
.outcome-count { width: 2rem; text-align: right; }
.failed { color: #ff6b6b; }
.when { color: #888; font-size: 0.85rem; }
.filename { color: #666; font-size: 0.8rem; }
ul { list-style: none; padding: 0; }
li { padding: 0.4rem 0; border-bottom: 1px solid #2a2d36; }
a { color: #6fb3ff; text-decoration: none; }
.all-clear { color: #4caf50; }
.status-running { color: #ffb74d; }
.status-idle { color: #4caf50; }
footer.runbook { color: #ccc; }
footer.runbook h3 { margin-bottom: 0.3rem; }
footer.runbook pre { background: #1a1d24; padding: 0.75rem 1rem; border-radius: 6px;
       overflow-x: auto; }
footer.runbook code { background: #1a1d24; padding: 0.1rem 0.3rem; border-radius: 3px; }
footer.runbook .paths { color: #888; font-size: 0.85rem; }
"""


def render_dashboard_html(data: DashboardData) -> str:
    if data.scan_running:
        status_html = '<span class="status-running">🔄 Running</span>'
    else:
        status_html = '<span class="status-idle">🟢 Idle</span>'
    tiles = "".join(
        [
            _stat_tile("Scans run", data.totals.total_runs),
            _stat_tile("Listings seen", data.totals.total_listings_seen),
            _stat_tile("Alerts sent", data.totals.total_alerts_sent),
            _stat_tile("Email digests sent", data.email_batch_count),
            _stat_tile("Open bug reports", len(data.bug_reports)),
        ]
    )
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Sourcing Engine Dashboard</title>
<style>{_STYLE}</style>
</head>
<body>
<h1>🛰️ Sourcing Engine Dashboard</h1>
<p class="generated-at">Generated {html.escape(data.generated_at)} · re-run
<code>python -m sw_sourcing.cli dashboard</code> to refresh · Scan status:
{status_html}</p>
<div class="tiles">{tiles}</div>
<section>
<h2>Outcomes (all time)</h2>
{_render_outcome_breakdown(data.outcome_counts)}
</section>
<section>
<h2>Recent scans</h2>
{_render_recent_runs(data.recent_runs)}
</section>
<section>
<h2>Things to look into</h2>
{_render_bug_reports(data.bug_reports)}
</section>
<section>
<h2>Recent alerts</h2>
{_render_recent_alerts(data.recent_alerts)}
</section>
<footer class="runbook">
<h2>Runbook</h2>
{_render_runbook(data)}
</footer>
</body>
</html>
"""
