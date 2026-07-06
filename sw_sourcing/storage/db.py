"""SQLite access: schema + typed reads/writes.

No decision logic lives here — callers pass already-decided values in.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
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
    outcome TEXT NOT NULL,
    cost_per_figure REAL,
    target_grade_count INTEGER,
    max_repro_risk TEXT,
    returns_accepted INTEGER,
    suggested_offer REAL,
    alerted_at TEXT NOT NULL
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
                    source, listing_id, outcome, cost_per_figure,
                    target_grade_count, max_repro_risk, returns_accepted,
                    suggested_offer, alerted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source,
                    listing_id,
                    outcome,
                    cost_per_figure,
                    target_grade_count,
                    max_repro_risk,
                    int(returns_accepted),
                    suggested_offer,
                    alerted_at,
                ),
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
