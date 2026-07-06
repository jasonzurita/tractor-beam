from sw_sourcing.core.decision import DecisionConfig, DecisionInput, decide

CONFIG = DecisionConfig(
    target_per_figure=5.0,
    negotiate_band_pct=0.35,
    max_damage_ratio=0.20,
    confidence_floor=0.5,
)


def base_input(**overrides: object) -> DecisionInput:
    defaults: dict[str, object] = {
        "price": 40.0,
        "shipping": 10.0,
        "target_grade_count": 10,
        "total_item_count": 10,
        "damaged_or_low_count": 0,
        "confidence": 0.9,
        "authenticity_clear": True,
        "buying_option": "fixed_price",
        "offers_accepted": True,
    }
    defaults.update(overrides)
    return DecisionInput(**defaults)  # type: ignore[arg-type]


def test_at_or_under_target_is_buy() -> None:
    # (40 + 10) / 10 = 5.00 == target_per_figure exactly
    assert decide(base_input(), CONFIG) == "buy"


def test_over_target_but_within_band_with_offers_is_negotiate() -> None:
    # (60 + 10) / 10 = 7.00, ceiling = 5 * 10 * 1.35 = 67.5, total cost 70... adjust
    input_ = base_input(price=57.5, shipping=10.0)  # total 67.5 == ceiling exactly
    assert decide(input_, CONFIG) == "negotiate"


def test_over_band_is_skip() -> None:
    input_ = base_input(price=100.0, shipping=10.0)  # total 110 > ceiling 67.5
    assert decide(input_, CONFIG) == "skip"


def test_negotiate_band_never_applies_to_auctions() -> None:
    input_ = base_input(price=57.5, shipping=10.0, buying_option="auction")
    assert decide(input_, CONFIG) == "skip"


def test_negotiate_band_requires_offers_accepted() -> None:
    input_ = base_input(price=57.5, shipping=10.0, offers_accepted=False)
    assert decide(input_, CONFIG) == "skip"


def test_zero_target_grade_count_is_skip_not_a_crash() -> None:
    input_ = base_input(target_grade_count=0)
    assert decide(input_, CONFIG) == "skip"


def test_authenticity_not_clear_is_always_review_regardless_of_price() -> None:
    input_ = base_input(price=1.0, shipping=0.0, authenticity_clear=False)
    assert decide(input_, CONFIG) == "review"


def test_low_confidence_is_review() -> None:
    input_ = base_input(confidence=0.4)
    assert decide(input_, CONFIG) == "review"


def test_confidence_exactly_at_floor_is_not_review() -> None:
    input_ = base_input(confidence=0.5)
    assert decide(input_, CONFIG) == "buy"


def test_damage_ratio_over_threshold_is_skip() -> None:
    input_ = base_input(total_item_count=10, damaged_or_low_count=3)  # 30% > 20%
    assert decide(input_, CONFIG) == "skip"


def test_damage_ratio_exactly_at_threshold_is_not_skip() -> None:
    input_ = base_input(total_item_count=10, damaged_or_low_count=2)  # exactly 20%
    assert decide(input_, CONFIG) == "buy"
