from sw_sourcing.core.authenticity import clear_repro_risk, is_disclosed_repro

BLOCKLIST = [
    "repro",
    "reproduction",
    "replacement",
    "restored",
    "custom",
    "aftermarket",
    "not original",
]


def test_disclosed_repro_term_is_flagged() -> None:
    text = "Vintage lot, one weapon is a reproduction"

    assert is_disclosed_repro(text, blocklist=BLOCKLIST)


def test_disclosed_repro_match_is_case_insensitive() -> None:
    text = "Blaster is a RESTORED original"

    assert is_disclosed_repro(text, blocklist=BLOCKLIST)


def test_clean_text_is_not_flagged() -> None:
    text = "All original vintage Kenner figures, great condition"

    assert not is_disclosed_repro(text, blocklist=BLOCKLIST)


def test_empty_blocklist_never_flags() -> None:
    text = "reproduction reproduction reproduction"

    assert not is_disclosed_repro(text, blocklist=[])


def test_low_risk_and_no_uncertain_grade_clears() -> None:
    assert clear_repro_risk(
        max_repro_risk="low",
        has_uncertain_grade=False,
        has_rare_candidate=False,
        max_repro_risk_for_autobuy="low",
    )


def test_elevated_risk_above_ceiling_does_not_clear() -> None:
    assert not clear_repro_risk(
        max_repro_risk="elevated",
        has_uncertain_grade=False,
        has_rare_candidate=False,
        max_repro_risk_for_autobuy="low",
    )


def test_risk_exactly_at_ceiling_clears() -> None:
    assert clear_repro_risk(
        max_repro_risk="elevated",
        has_uncertain_grade=False,
        has_rare_candidate=False,
        max_repro_risk_for_autobuy="elevated",
    )


def test_uncertain_grade_never_clears_even_with_low_risk() -> None:
    assert not clear_repro_risk(
        max_repro_risk="low",
        has_uncertain_grade=True,
        has_rare_candidate=False,
        max_repro_risk_for_autobuy="high",
    )


def test_rare_candidate_never_clears_even_with_low_risk() -> None:
    """A possible rare/valuable variant always routes to review, regardless
    of its own repro_risk score -- rare pieces are the ones counterfeiters
    target most, so rarity is never a shortcut around authenticity."""
    assert not clear_repro_risk(
        max_repro_risk="low",
        has_uncertain_grade=False,
        has_rare_candidate=True,
        max_repro_risk_for_autobuy="high",
    )
