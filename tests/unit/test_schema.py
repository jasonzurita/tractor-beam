from sw_sourcing.core.schema import Listing


def test_listing_parses_the_common_shape() -> None:
    listing = Listing(
        source="ebay",
        listing_id="1234567890",
        url="https://example.com/listing/1234567890",
        title="Vintage Kenner Star Wars loose figure lot of 12 w/ weapons",
        description="full text for the repro screen",
        price=60.00,
        shipping=8.50,
        buying_option="best_offer",
        offers_accepted=True,
        returns_accepted=True,
        seller_feedback=0.995,
        location="OH, USA",
        images=["https://example.com/img1.jpg", "https://example.com/img2.jpg"],
        fetched_at="2026-07-06T14:00:00Z",
    )

    assert listing.source == "ebay"
    assert listing.buying_option == "best_offer"
    assert len(listing.images) == 2
