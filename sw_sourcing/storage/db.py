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
CREATE TABLE IF NOT EXISTS seen_listings (
    source TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (source, listing_id)
);

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
"""


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
    alerted_at: str
    reported_at: str | None


_ALERT_COLUMNS = (
    "id, source, listing_id, title, url, image_url, outcome, cost_per_figure,"
    " target_grade_count, max_repro_risk, returns_accepted, suggested_offer,"
    " alerted_at, reported_at"
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
        alerted_at=row[12],  # type: ignore[arg-type]
        reported_at=row[13],  # type: ignore[arg-type]
    )


class Database:
    """Thin typed wrapper around one SQLite file."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def has_seen(self, source: str, listing_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM seen_listings WHERE source = ? AND listing_id = ?",
                (source, listing_id),
            ).fetchone()
        return row is not None

    def mark_seen(self, source: str, listing_id: str, *, seen_at: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO seen_listings (source, listing_id, first_seen, last_seen)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (source, listing_id)
                DO UPDATE SET last_seen = excluded.last_seen
                """,
                (source, listing_id, seen_at, seen_at),
            )

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
        alerted_at: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO alerts (
                    source, listing_id, title, url, image_url, outcome,
                    cost_per_figure, target_grade_count, max_repro_risk,
                    returns_accepted, suggested_offer, alerted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
