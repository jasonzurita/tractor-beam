"""Repro-text screen and vision-risk routing.

This module flags authenticity risk; it never certifies authenticity.
Final authenticity is always an in-hand human step outside this codebase.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

ReproRisk = Literal["low", "elevated", "high"]

_RISK_ORDER: dict[ReproRisk, int] = {"low": 0, "elevated": 1, "high": 2}


def _contains_any(text: str, blocklist: Sequence[str]) -> bool:
    if not blocklist:
        return False

    haystack = text.lower()
    return any(term.lower() in haystack for term in blocklist)


def is_disclosed_repro(text: str, *, blocklist: Sequence[str]) -> bool:
    """True if the listing text discloses reproduction/restoration itself."""
    return _contains_any(text, blocklist)


def is_disclosed_era_mismatch(text: str, *, blocklist: Sequence[str]) -> bool:
    """True if the listing text itself signals a later, non-original-era
    line (e.g. a 90's Power of the Force reissue or a modern Vintage
    Collection figure) rather than the 1977-1985 original Kenner run.

    Self-disclosure here is treated as decisive, the same way
    `is_disclosed_repro` treats a disclosed repro/restoration -- unlike a
    listing merely *claiming* "vintage" (never evidence on its own), a
    seller naming a specific later line is a positive admission.
    """
    return _contains_any(text, blocklist)


def clear_repro_risk(
    *,
    max_repro_risk: ReproRisk,
    has_uncertain_grade: bool,
    has_rare_candidate: bool,
    max_repro_risk_for_autobuy: ReproRisk,
) -> bool:
    """True if the listing may proceed to price-based decisioning.

    False routes to manual review — never a skip, since nothing here was
    disclosed as reproduction; it's only a vision-flagged risk, an
    unresolvable grade, or a possible rare/valuable variant. Rare pieces
    are the ones counterfeiters target most, so a rare_candidate flag
    always routes to review regardless of its own repro_risk score --
    rarity is never a shortcut around authenticity.
    """
    if has_uncertain_grade or has_rare_candidate:
        return False
    return _RISK_ORDER[max_repro_risk] <= _RISK_ORDER[max_repro_risk_for_autobuy]
