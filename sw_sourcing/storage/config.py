"""Typed accessor over the `config` table.

Thresholds and cadences live here (or their documented default), never as
literals in `core/`.
"""

from __future__ import annotations

import json
from typing import Any

from sw_sourcing.storage.db import Database

DEFAULTS: dict[str, Any] = {
    "target_per_figure": 5.00,
    "target_per_weapon": None,
    "grade_floor": "mid",
    "authenticity_required": True,
    "repro_keyword_blocklist": [
        "repro",
        "reproduction",
        "replacement",
        "restored",
        "custom",
        "aftermarket",
        "not original",
    ],
    "max_repro_risk_for_autobuy": "low",
    "prefer_returnable": True,
    "negotiate_band_pct": 0.35,
    "max_damage_ratio": 0.20,
    "confidence_floor": 0.5,
    "sources_enabled": ["ebay", "facebook", "mercari"],
}


class Config:
    """Reads config values from SQLite, falling back to the documented default."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, key: str) -> Any:
        if key not in DEFAULTS:
            raise KeyError(f"unknown config key: {key}")
        raw = self._db.get_config(key)
        if raw is None:
            return DEFAULTS[key]
        return json.loads(raw)

    def set(self, key: str, value: Any) -> None:
        if key not in DEFAULTS:
            raise KeyError(f"unknown config key: {key}")
        self._db.set_config(key, json.dumps(value))
