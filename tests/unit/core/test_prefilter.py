from sw_sourcing.core.prefilter import passes_prefilter
from tests.unit.factories import make_listing


def test_relevant_listing_within_price_ceiling_passes() -> None:
    listing = make_listing(price=10.0, shipping=5.0)

    assert passes_prefilter(
        listing, required_keywords=["kenner", "star wars"], max_listing_price=50.0
    )


def test_listing_missing_all_required_keywords_is_rejected() -> None:
    listing = make_listing(title="Modern action figure", description="brand new toy")

    assert not passes_prefilter(
        listing, required_keywords=["kenner", "vintage"], max_listing_price=50.0
    )


def test_keyword_match_is_case_insensitive_and_checks_description_too() -> None:
    listing = make_listing(
        title="Loose figure lot", description="Genuine KENNER vintage piece"
    )

    assert passes_prefilter(
        listing, required_keywords=["kenner"], max_listing_price=50.0
    )


def test_listing_over_price_ceiling_is_rejected() -> None:
    listing = make_listing(price=100.0, shipping=10.0)

    assert not passes_prefilter(listing, required_keywords=[], max_listing_price=50.0)


def test_price_exactly_at_ceiling_passes() -> None:
    listing = make_listing(price=45.0, shipping=5.0)

    assert passes_prefilter(listing, required_keywords=[], max_listing_price=50.0)


def test_empty_required_keywords_means_no_topic_restriction() -> None:
    listing = make_listing(title="anything at all", description="no relevant terms")

    assert passes_prefilter(listing, required_keywords=[], max_listing_price=50.0)
