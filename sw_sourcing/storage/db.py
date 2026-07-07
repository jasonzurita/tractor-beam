"""SQLite access: schema + typed reads/writes.

No decision logic lives here — callers pass already-decided values in.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS vision_cache (
    image_set_hash TEXT PRIMARY KEY,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
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
);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    sources_ok TEXT,
    sources_failed TEXT,
    listings_seen INTEGER,
    alerts_sent INTEGER
);

CREATE TABLE IF NOT EXISTS failure_reports (
    key TEXT PRIMARY KEY,
    last_reported_at TEXT NOT NULL
);
"""

# CREATE TABLE IF NOT EXISTS won't retroactively add a new column to a
# table that already existed on disk -- new columns on an existing table
# need an explicit ALTER TABLE here, checked against what's actually there.
_ALERT_COLUMN_MIGRATIONS: list[tuple[str, str]] = [
    ("vision_notes", "ALTER TABLE alerts ADD COLUMN vision_notes TEXT"),
    ("price", "ALTER TABLE alerts ADD COLUMN price REAL"),
]


@dataclass(frozen=True)
class AlertRecord:
    id: int
    source: str
    listing_id: str
    title: str
    url: str
    image_url: str | None
    outcome: str
    cost_per_figure: float | None
    target_grade_count: int | None
    max_repro_risk: str | None
    returns_accepted: bool
    suggested_offer: float | None
    vision_notes: str | None
    price: float | None
    alerted_at: str
    reported_at: str | None


_ALERT_COLUMNS = (
    "id, source, listing_id, title, url, image_url, outcome, cost_per_figure,"
    " target_grade_count, max_repro_risk, returns_accepted, suggested_offer,"
    " vision_notes, price, alerted_at, reported_at"
)


def _row_to_alert_record(row: tuple[object, ...]) -> AlertRecord:
    return AlertRecord(
        id=row[0],  # type: ignore[arg-type]
        source=row[1],  # type: ignore[arg-type]
        listing_id=row[2],  # type: ignore[arg-type]
        title=row[3],  # type: ignore[arg-type]
        url=row[4],  # type: ignore[arg-type]
        image_url=row[5],  # type: ignore[arg-type]
        outcome=row[6],  # type: ignore[arg-type]
        cost_per_figure=row[7],  # type: ignore[arg-type]
        target_grade_count=row[8],  # type: ignore[arg-type]
        max_repro_risk=row[9],  # type: ignore[arg-type]
        returns_accepted=bool(row[10]),
        suggested_offer=row[11],  # type: ignore[arg-type]
        vision_notes=row[12],  # type: ignore[arg-type]
        price=row[13],  # type: ignore[arg-type]
        alerted_at=row[14],  # type: ignore[arg-type]
        reported_at=row[15],  # type: ignore[arg-type]
    )


class Database:
    """Thin typed wrapper around one SQLite file."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(alerts)")}
        for column, ddl in _ALERT_COLUMN_MIGRATIONS:
            if column not in existing_columns:
                conn.execute(ddl)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def has_alerted(
        self, source: str, listing_id: str, *, outcome: str, price: float
    ) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM alerts
                WHERE source = ? AND listing_id = ? AND outcome = ? AND price = ?
                """,
                (source, listing_id, outcome, price),
            ).fetchone()
        return row is not None

    def get_vision_cache(self, image_set_hash: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM vision_cache WHERE image_set_hash = ?",
                (image_set_hash,),
            ).fetchone()
        return row[0] if row else None

    def put_vision_cache(
        self, image_set_hash: str, result_json: str, *, created_at: str
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO vision_cache (image_set_hash, result_json, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT (image_set_hash)
                DO UPDATE SET result_json = excluded.result_json
                """,
                (image_set_hash, result_json, created_at),
            )

    def get_config(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM config WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row else None

    def set_config(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO config (key, value) VALUES (?, ?)
                ON CONFLICT (key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def record_alert(
        self,
        *,
        source: str,
        listing_id: str,
        title: str,
        url: str,
        image_url: str | None,
        outcome: str,
        cost_per_figure: float | None,
        target_grade_count: int | None,
        max_repro_risk: str | None,
        returns_accepted: bool,
        suggested_offer: float | None,
        vision_notes: str | None,
        price: float | None,
        alerted_at: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO alerts (
                    source, listing_id, title, url, image_url, outcome,
                    cost_per_figure, target_grade_count, max_repro_risk,
                    returns_accepted, suggested_offer, vision_notes, price,
                    alerted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source,
                    listing_id,
                    title,
                    url,
                    image_url,
                    outcome,
                    cost_per_figure,
                    target_grade_count,
                    max_repro_risk,
                    int(returns_accepted),
                    suggested_offer,
                    vision_notes,
                    price,
                    alerted_at,
                ),
            )

    def get_unreported_alerts(self) -> list[AlertRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_ALERT_COLUMNS} FROM alerts WHERE reported_at IS NULL"
                " ORDER BY alerted_at"
            ).fetchall()
        return [_row_to_alert_record(row) for row in rows]

    def mark_alerts_reported(self, alert_ids: list[int], *, reported_at: str) -> None:
        if not alert_ids:
            return
        placeholders = ", ".join("?" for _ in alert_ids)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE alerts SET reported_at = ? WHERE id IN ({placeholders})",
                (reported_at, *alert_ids),
            )

    def get_last_failure_report(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_reported_at FROM failure_reports WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row else None

    def record_failure_report(self, key: str, *, reported_at: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO failure_reports (key, last_reported_at) VALUES (?, ?)
                ON CONFLICT (key)
                DO UPDATE SET last_reported_at = excluded.last_reported_at
                """,
                (key, reported_at),
            )

    def record_run(
        self,
        *,
        started_at: str,
        sources_ok: list[str],
        sources_failed: list[str],
        listings_seen: int,
        alerts_sent: int,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    started_at, sources_ok, sources_failed, listings_seen, alerts_sent
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    started_at,
                    json.dumps(sources_ok),
                    json.dumps(sources_failed),
                    listings_seen,
                    alerts_sent,
                ),
            )
