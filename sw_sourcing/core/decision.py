"""Buy / negotiate / review / skip decision engine.

Authenticity is checked upstream (`core/authenticity.py`) and handed in here
as a plain boolean — this module never inspects repro risk itself, so it
cannot accidentally weaken the authenticity gate with a price shortcut.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sw_sourcing.core.negotiation import negotiate_ceiling
from sw_sourcing.core.schema import BuyingOption

Outcome = Literal["buy", "negotiate", "review", "skip"]


@dataclass(frozen=True)
class DecisionInput:
    price: float
    shipping: float
    target_grade_count: int
    total_item_count: int
    damaged_or_low_count: int
    confidence: float
    authenticity_clear: bool
    buying_option: BuyingOption
    offers_accepted: bool


@dataclass(frozen=True)
class DecisionConfig:
    target_per_figure: float
    negotiate_band_pct: float
    max_damage_ratio: float
    confidence_floor: float


def decide(input: DecisionInput, config: DecisionConfig) -> Outcome:
    if not input.authenticity_clear:
        return "review"

    if input.confidence < config.confidence_floor:
        return "review"

    if input.total_item_count > 0:
        damage_ratio = input.damaged_or_low_count / input.total_item_count
        if damage_ratio > config.max_damage_ratio:
            return "skip"

    if input.target_grade_count == 0:
        return "skip"

    total_cost = input.price + input.shipping
    cost_per_figure = total_cost / input.target_grade_count

    if cost_per_figure <= config.target_per_figure:
        return "buy"

    ceiling = negotiate_ceiling(
        target_per_figure=config.target_per_figure,
        target_grade_count=input.target_grade_count,
        negotiate_band_pct=config.negotiate_band_pct,
    )
    if (
        total_cost <= ceiling
        and input.offers_accepted
        and input.buying_option != "auction"
    ):
        return "negotiate"

    return "skip"
