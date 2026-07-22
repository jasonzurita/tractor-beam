from sw_sourcing.alerts.discord import DiscordAlerts, format_alert, format_heartbeat
from tests.unit.factories import FakeHttpxClient, make_listing


def test_format_alert_includes_title_url_and_outcome() -> None:
    listing = make_listing(title="Vintage lot of 12", url="https://example.com/1")

    payload = format_alert(listing, "buy")

    assert "Vintage lot of 12" in payload["content"]
    assert "https://example.com/1" in payload["content"]
    assert "BUY" in payload["content"]


def test_format_alert_includes_optional_figures_when_given() -> None:
    listing = make_listing()

    payload = format_alert(
        listing,
        "negotiate",
        cost_per_figure=6.5,
        target_grade_count=10,
        suggested_offer=45.0,
        max_repro_risk="low",
    )

    assert "6.50" in payload["content"]
    assert "10" in payload["content"]
    assert "45.00" in payload["content"]
    assert "low" in payload["content"]


def test_format_alert_shows_cost_per_weapon_when_given() -> None:
    listing = make_listing()

    payload = format_alert(listing, "buy", cost_per_weapon=6.5)

    assert "6.50" in payload["content"]
    assert "weapon" in payload["content"].lower()


def test_format_alert_shows_returns_accepted_when_given() -> None:
    listing = make_listing()

    accepted = format_alert(listing, "buy", returns_accepted=True)
    not_accepted = format_alert(listing, "buy", returns_accepted=False)

    assert "Returns accepted: yes" in accepted["content"]
    assert "Returns accepted: no" in not_accepted["content"]


def test_format_alert_flags_a_price_change_since_last_alert() -> None:
    listing = make_listing(price=9.0)

    payload = format_alert(listing, "review", previous_price=13.0)

    assert "13.00" in payload["content"]
    assert "9.00" in payload["content"]
    assert "Price changed" in payload["content"]


def test_format_alert_omits_price_change_note_when_price_is_unchanged() -> None:
    listing = make_listing(price=9.0)

    payload = format_alert(listing, "review", previous_price=9.0)

    assert "Price changed" not in payload["content"]


def test_format_alert_omits_price_change_note_when_no_previous_alert() -> None:
    listing = make_listing(price=9.0)

    payload = format_alert(listing, "review")

    assert "Price changed" not in payload["content"]


def test_format_alert_omits_optional_figures_when_not_given() -> None:
    listing = make_listing()

    payload = format_alert(listing, "review")

    assert "Cost/figure" not in payload["content"]


def test_format_heartbeat_reports_counts_and_sources() -> None:
    payload = format_heartbeat(
        sources_ok=["ebay"], sources_failed=[], listings_seen=20, alerts_sent=3
    )

    assert "20" in payload["content"]
    assert "3" in payload["content"]
    assert "ebay" in payload["content"]
    assert "✅" in payload["content"]


def test_format_heartbeat_flags_failed_sources() -> None:
    payload = format_heartbeat(
        sources_ok=["ebay"],
        sources_failed=["mercari"],
        listings_seen=5,
        alerts_sent=0,
    )

    assert "mercari" in payload["content"]
    assert "⚠️" in payload["content"]


def test_format_heartbeat_flags_bug_reports_when_any_were_written() -> None:
    payload = format_heartbeat(
        sources_ok=["ebay"],
        sources_failed=[],
        listings_seen=5,
        alerts_sent=1,
        bug_reports_written=2,
    )

    assert "🐛" in payload["content"]
    assert "2" in payload["content"]


def test_format_heartbeat_omits_bug_report_line_when_none_written() -> None:
    payload = format_heartbeat(
        sources_ok=["ebay"], sources_failed=[], listings_seen=5, alerts_sent=1
    )

    assert "🐛" not in payload["content"]


def test_send_posts_the_payload_to_the_webhook_url() -> None:
    client = FakeHttpxClient()
    alerts = DiscordAlerts("https://discord.example/webhook", client=client)  # type: ignore[arg-type]

    alerts.send({"content": "hello"})

    assert client.calls == [("https://discord.example/webhook", {"content": "hello"})]
