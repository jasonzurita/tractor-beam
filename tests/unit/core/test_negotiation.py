from sw_sourcing.core.negotiation import negotiate_ceiling, suggested_offer


def test_negotiate_ceiling_scales_with_target_grade_count() -> None:
    ceiling = negotiate_ceiling(
        target_per_figure=5.0, target_grade_count=10, negotiate_band_pct=0.35
    )

    assert ceiling == 5.0 * 10 * 1.35


def test_negotiate_ceiling_is_zero_when_no_target_grade_figures() -> None:
    ceiling = negotiate_ceiling(
        target_per_figure=5.0, target_grade_count=0, negotiate_band_pct=0.35
    )

    assert ceiling == 0.0


def test_suggested_offer_undercuts_target_by_default_margin() -> None:
    offer = suggested_offer(shipping=8.0, target_grade_count=10, target_per_figure=5.0)

    assert offer == round(5.0 * 10 * 0.95 - 8.0, 2)


def test_suggested_offer_never_goes_negative() -> None:
    offer = suggested_offer(shipping=500.0, target_grade_count=1, target_per_figure=5.0)

    assert offer == 0.0


def test_suggested_offer_respects_custom_undercut_pct() -> None:
    offer = suggested_offer(
        shipping=0.0, target_grade_count=4, target_per_figure=5.0, undercut_pct=0.10
    )

    assert offer == round(5.0 * 4 * 0.90, 2)
