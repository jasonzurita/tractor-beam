"""Offer math for the negotiate outcome: cutoff and suggested offer."""

from __future__ import annotations


def negotiate_ceiling(
    *, target_per_figure: float, target_grade_count: int, negotiate_band_pct: float
) -> float:
    """Highest total listing cost (price + shipping) still worth negotiating down."""
    return target_per_figure * target_grade_count * (1 + negotiate_band_pct)


def suggested_offer(
    *,
    shipping: float,
    target_grade_count: int,
    target_per_figure: float,
    undercut_pct: float = 0.05,
) -> float:
    """Listing price to offer, aiming slightly under target after shipping."""
    target_total = target_per_figure * target_grade_count * (1 - undercut_pct)
    return max(0.0, round(target_total - shipping, 2))
