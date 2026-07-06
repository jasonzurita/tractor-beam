"""Repro-text screen and vision-risk routing.

This module flags authenticity risk; it never certifies authenticity.
Final authenticity is always an in-hand human step outside this codebase.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

ReproRisk = Literal["low", "elevated", "high"]

_RISK_ORDER: dict[ReproRisk, int] = {"low": 0, "elevated": 1, "high": 2}


def is_disclosed_repro(text: str, *, blocklist: Sequence[str]) -> bool:
    """True if the listing text discloses reproduction/restoration itself."""
    if not blocklist:
        return False

    haystack = text.lower()
    return any(term.lower() in haystack for term in blocklist)


def clear_repro_risk(
    *,
    max_repro_risk: ReproRisk,
    has_uncertain_grade: bool,
    max_repro_risk_for_autobuy: ReproRisk,
) -> bool:
    """True if the listing may proceed to price-based decisioning.

    False routes to manual review — never a skip, since nothing here was
    disclosed as reproduction; it's only a vision-flagged risk or an
    unresolvable grade.
    """
    if has_uncertain_grade:
        return False
    return _RISK_ORDER[max_repro_risk] <= _RISK_ORDER[max_repro_risk_for_autobuy]
