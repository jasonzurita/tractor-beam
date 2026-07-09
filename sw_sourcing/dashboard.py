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
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from itertools import count as _counter
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
    home: str


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
        home=str(Path.home()),
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


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _humanize_duration(seconds: float) -> str:
    """Rounds down to whole seconds -- sub-second precision isn't useful
    here and would make otherwise-identical durations look different."""
    total_seconds = max(0, int(seconds))
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes, remaining_seconds = divmod(total_seconds, 60)
    if minutes < 60:
        if remaining_seconds:
            return f"{minutes}m {remaining_seconds}s"
        return f"{minutes}m"
    hours, remaining_minutes = divmod(minutes, 60)
    if remaining_minutes:
        return f"{hours}h {remaining_minutes}m"
    return f"{hours}h"


def _render_last_run_summary(
    recent_runs: list[RunRecord], *, scan_running: bool, generated_at: str
) -> str:
    if not recent_runs:
        return "no scans recorded yet"
    latest = recent_runs[0]
    now = _parse_iso(generated_at)
    started = _parse_iso(latest.started_at)

    if latest.finished_at is not None:
        finished = _parse_iso(latest.finished_at)
        duration = _humanize_duration((finished - started).total_seconds())
        ago = _humanize_duration((now - finished).total_seconds())
        return f"last run finished {ago} ago (took {duration})"

    elapsed = _humanize_duration((now - started).total_seconds())
    if scan_running:
        return f"current run has been going {elapsed}"
    return (
        "<span class='status-crashed'>⚠️ last run started "
        f"{elapsed} ago and never finished -- it likely crashed, check logs"
        "/bug reports</span>"
    )


def _render_recent_runs(runs: list[RunRecord], *, scan_running: bool) -> str:
    if not runs:
        return "<p>No scans recorded yet.</p>"
    max_listings = max((run.listings_seen or 0 for run in runs), default=0)
    rows = []
    for index, run in enumerate(runs):
        failed = ", ".join(html.escape(s) for s in run.sources_failed)
        failed_cell = f"<span class='failed'>{failed}</span>" if failed else "—"
        if run.listings_seen is None:
            listings_cell = "—"
        else:
            listings_cell = (
                f"{run.listings_seen} {_bar(run.listings_seen, max_listings)}"
            )
        alerts_cell = "—" if run.alerts_sent is None else str(run.alerts_sent)

        if run.finished_at is not None:
            duration = _humanize_duration(
                (
                    _parse_iso(run.finished_at) - _parse_iso(run.started_at)
                ).total_seconds()
            )
            status_cell = f"✅ {duration}"
        elif index == 0 and scan_running:
            status_cell = "<span class='status-running'>🔄 still running</span>"
        else:
            status_cell = "<span class='status-crashed'>⚠️ crashed</span>"

        rows.append(
            "<tr>"
            f"<td>{html.escape(run.started_at)}</td>"
            f"<td>{status_cell}</td>"
            f"<td>{html.escape(', '.join(run.sources_ok)) or '—'}</td>"
            f"<td>{failed_cell}</td>"
            f"<td>{listings_cell}</td>"
            f"<td>{alerts_cell}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>Started</th><th>Status</th><th>Sources OK</th><th>Sources failed</th>"
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


_CRON_SCHEDULE = (
    {
        "command": "scan",
        "description": "Scans marketplaces for new listings",
        "human_cadence": "Every 30 minutes",
        "cron_expr": "*/30 * * * *",
    },
    {
        "command": "send-report",
        "description": "Emails the digest of unreported alerts",
        "human_cadence": "Every day at 9:00 AM",
        "cron_expr": "0 9 * * *",
    },
    {
        "command": "dashboard",
        "description": "Regenerates this page",
        "human_cadence": "Every 10 minutes",
        "cron_expr": "*/10 * * * *",
    },
)

_CADENCE_REFERENCE = (
    ("*/5 * * * *", "Every 5 minutes"),
    ("*/15 * * * *", "Every 15 minutes"),
    ("*/30 * * * *", "Every 30 minutes"),
    ("0 * * * *", "Every hour, on the hour"),
    ("0 9 * * *", "Every day at 9:00 AM"),
    ("0 9,17 * * *", "Every day at 9:00 AM and 5:00 PM"),
    ("0 9 * * 1-5", "Every weekday at 9:00 AM"),
)


def _copy_command(command: str, ids: Iterator[int]) -> str:
    """A <pre> block with its own copy-to-clipboard button.

    Each block gets a fresh id from `ids` rather than deriving one from
    content, since two different commands can render identical text (e.g.
    two "ls" examples) and ids must stay unique on the page. `ids` is
    scoped to one render_dashboard_html() call, not module state, so
    rendering the same data twice still produces identical output.
    """
    block_id = f"copy-{next(ids)}"
    return (
        '<div class="copy-block">'
        f'<pre id="{block_id}">{command}</pre>'
        f'<button class="copy-btn" data-target="{block_id}" '
        'onclick="copyBlock(this)" type="button">Copy</button>'
        "</div>"
    )


def _render_cron_schedule(cwd: str, ids: Iterator[int]) -> str:
    rows = []
    for entry in _CRON_SCHEDULE:
        full_line = (
            f"{entry['cron_expr']} cd {cwd} &amp;&amp; .venv/bin/python -m"
            f" sw_sourcing.cli {entry['command']} &gt;&gt; /dev/null 2&gt;&amp;1"
        )
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(entry['command'])}</code></td>"
            f"<td>{html.escape(entry['description'])}</td>"
            f"<td>{html.escape(entry['human_cadence'])}</td>"
            f"<td>{_copy_command(full_line, ids)}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>Command</th><th>What it does</th><th>Cadence</th>"
        "<th>Crontab line (cadences are independent -- edit one without"
        " the others)</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _render_cadence_reference() -> str:
    rows = "".join(
        f"<tr><td><code>{html.escape(expr)}</code></td><td>{html.escape(meaning)}</td></tr>"
        for expr, meaning in _CADENCE_REFERENCE
    )
    return (
        "<table><thead><tr><th>Cron expression</th><th>Meaning</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )


_LAUNCHD_AGENTS = (
    {
        "label": "com.tractorbeam.scan",
        "command": "scan",
        "description": "Scans marketplaces for new listings",
        "human_cadence": "Every 30 minutes",
        "calendar_intervals": (
            "        <dict><key>Minute</key><integer>0</integer></dict>\n"
            "        <dict><key>Minute</key><integer>30</integer></dict>"
        ),
    },
    {
        "label": "com.tractorbeam.send-report",
        "command": "send-report",
        "description": "Emails the digest of unreported alerts",
        "human_cadence": "Every day at 9:00 AM and 12:00 PM",
        "calendar_intervals": (
            "        <dict><key>Hour</key><integer>9</integer>"
            "<key>Minute</key><integer>0</integer></dict>\n"
            "        <dict><key>Hour</key><integer>12</integer>"
            "<key>Minute</key><integer>0</integer></dict>"
        ),
    },
)


def _launchd_setup_script(agent: dict[str, str], cwd: str, home: str) -> str:
    """A paste-as-is Terminal script: write the plist, then (re)load it.

    The bootout before bootstrap makes this idempotent -- pasting it again
    after editing the plist reloads cleanly instead of erroring on
    "already bootstrapped".
    """
    plist_path = f"{home}/Library/LaunchAgents/{agent['label']}.plist"
    log_dir = f"{home}/Library/Logs/tractor-beam"
    plist_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{agent["label"]}</string>
    <key>WorkingDirectory</key>
    <string>{cwd}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{cwd}/.venv/bin/python</string>
        <string>-m</string>
        <string>sw_sourcing.cli</string>
        <string>{agent["command"]}</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/bin:/bin</string>
    </dict>
    <key>StartCalendarInterval</key>
    <array>
{agent["calendar_intervals"]}
    </array>
    <key>StandardOutPath</key>
    <string>{log_dir}/{agent["command"]}.out.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir}/{agent["command"]}.err.log</string>
</dict>
</plist>"""
    return (
        f"mkdir -p {log_dir}\n"
        f"cat > {plist_path} <<'EOF'\n"
        f"{plist_xml}\n"
        "EOF\n"
        f"launchctl bootout gui/$(id -u)/{agent['label']} 2>/dev/null\n"
        f"launchctl bootstrap gui/$(id -u) {plist_path}"
    )


def _render_launchd_agents(cwd: str, home: str, ids: Iterator[int]) -> str:
    rows = []
    for agent in _LAUNCHD_AGENTS:
        script = html.escape(_launchd_setup_script(agent, cwd, home))
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(agent['label'])}</code></td>"
            f"<td>{html.escape(agent['description'])}</td>"
            f"<td>{html.escape(agent['human_cadence'])}</td>"
            f"<td>{_copy_command(script, ids)}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>Label</th><th>What it does</th><th>Cadence</th>"
        "<th>Create + load (paste as-is in Terminal)</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _render_launchd_manage(home: str, ids: Iterator[int]) -> str:
    label = "com.tractorbeam.scan"
    log_path = f"{home}/Library/Logs/tractor-beam/scan.out.log"
    return f"""
<p>Same commands work for any agent -- swap the label (e.g.
<code>com.tractorbeam.send-report</code>). Shown here for
<code>{html.escape(label)}</code>:</p>
{_copy_command("launchctl list | grep tractorbeam", ids)}
{_copy_command(f"launchctl print gui/$(id -u)/{label}", ids)}
{_copy_command(f"launchctl kickstart -k gui/$(id -u)/{label}", ids)}
{_copy_command(f"tail -f {html.escape(log_path)}", ids)}
{_copy_command(f"launchctl bootout gui/$(id -u)/{label}", ids)}
"""


def _render_runbook(data: DashboardData) -> str:
    db_path = html.escape(data.db_path)
    log_path = html.escape(data.log_path)
    lock_path = html.escape(data.lock_path)
    bug_reports_dir = html.escape(data.bug_reports_dir)
    cwd = html.escape(data.cwd)
    home = html.escape(data.home)
    ids = _counter(1)
    return f"""
<h3>Is it running right now?</h3>
<p>The status tile above reflects the scan lock as of when this page was
generated. To check live -- <code>pgrep</code> excludes its own process,
so unlike <code>ps aux | grep</code> it won't match itself:</p>
{_copy_command('pgrep -f "sw_sourcing.cli scan"', ids)}
{_copy_command(f"lsof {lock_path}", ids)}

<h3>Something looks stuck or needs a restart</h3>
<p>There's no long-running service here to restart -- <code>scan</code>,
<code>send-report</code>, and <code>dashboard</code> are all one-shot
commands fired by whatever scheduler triggers them (cron or launchd, see
below). A wedged scan is safe to kill directly: the lock is a kernel
flock tied to the process, so killing it frees the lock immediately and
there's nothing to clean up by hand.</p>
{_copy_command('pkill -f "sw_sourcing.cli scan"', ids)}
<p>Then see what happened:</p>
{_copy_command(f"tail -100 {log_path}", ids)}
{_copy_command(f"ls {bug_reports_dir}", ids)}

<h3>Scheduling: cron or launchd?</h3>
<p>Either works -- pick based on how <code>claude</code> is authenticated
on this machine. If a scan run from cron logs <code>Not logged in</code>
even though <code>claude</code> works fine in your own terminal, your
login is a keychain-managed session (not a raw <code>ANTHROPIC_API_KEY</code>).
Plain cron runs outside your macOS login session and can't unlock the
keychain to read it -- <code>launchd</code> runs inside that session and
can. If you're on Linux, or your auth is a plain API key, cron just
works and you can ignore the launchd section entirely.</p>

<h4>Cron (portable)</h4>
<p>Each row below is one crontab line: what it runs, in plain English how
often, and a copyable line with the actual cron expression.</p>
{_render_cron_schedule(cwd, ids)}
{_copy_command("crontab -e", ids)}
{_copy_command("crontab -l", ids)}
<p>Not sure what a cadence should look like in cron syntax? Reference:</p>
{_render_cadence_reference()}
<p>On macOS, cron needs Full Disk Access (System Settings &rarr; Privacy
&amp; Security) or these entries silently no-op.</p>

<h4>launchd (macOS, session-based -- needed for keychain-gated logins)</h4>
<p>Each row's last column is a complete script: paste it into Terminal
as-is and it writes the agent's plist to
<code>~/Library/LaunchAgents/</code> and loads it. Pasting it again later
(e.g. after editing the cadence) reloads cleanly.</p>
{_render_launchd_agents(cwd, home, ids)}
<p>Managing an already-loaded agent:</p>
{_render_launchd_manage(home, ids)}
<p>Agents in <code>~/Library/LaunchAgents/</code> auto-load at every
login -- no need to re-run the setup script after a restart. To stop one
for good, run the <code>bootout</code> command above, then delete its
plist file.</p>

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
.status-crashed { color: #ff6b6b; }
footer.runbook { color: #ccc; }
footer.runbook h3 { margin-bottom: 0.3rem; }
footer.runbook pre { background: #1a1d24; padding: 0.75rem 1rem; border-radius: 6px;
       overflow-x: auto; margin: 0; }
footer.runbook code { background: #1a1d24; padding: 0.1rem 0.3rem; border-radius: 3px; }
footer.runbook .paths { color: #888; font-size: 0.85rem; }
.copy-block { position: relative; margin: 0.5rem 0; }
.copy-block pre { padding-right: 4rem; }
.copy-btn { position: absolute; top: 0.5rem; right: 0.5rem; background: #2a2d36;
       color: #e6e6e6; border: none; border-radius: 4px; padding: 0.25rem 0.6rem;
       font-size: 0.75rem; cursor: pointer; }
.copy-btn:hover { background: #3a3d46; }
"""

_COPY_SCRIPT = """
function copyBlock(btn) {
  var target = document.getElementById(btn.getAttribute('data-target'));
  var text = target.innerText;
  var done = function () {
    var original = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(function () { btn.textContent = original; }, 1500);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, function () {
      fallbackCopy(target, done);
    });
  } else {
    fallbackCopy(target, done);
  }
}

function fallbackCopy(target, done) {
  var range = document.createRange();
  range.selectNode(target);
  var selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  document.execCommand('copy');
  selection.removeAllRanges();
  done();
}
"""


def render_dashboard_html(data: DashboardData) -> str:
    if data.scan_running:
        status_html = '<span class="status-running">🔄 Running</span>'
    else:
        status_html = '<span class="status-idle">🟢 Idle</span>'
    last_run_html = _render_last_run_summary(
        data.recent_runs,
        scan_running=data.scan_running,
        generated_at=data.generated_at,
    )
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
{status_html} · {last_run_html}</p>
<div class="tiles">{tiles}</div>
<section>
<h2>Outcomes (all time)</h2>
{_render_outcome_breakdown(data.outcome_counts)}
</section>
<section>
<h2>Recent scans</h2>
{_render_recent_runs(data.recent_runs, scan_running=data.scan_running)}
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
<script>{_COPY_SCRIPT}</script>
</body>
</html>
"""
