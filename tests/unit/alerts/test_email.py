from sw_sourcing.alerts.email import EmailSender, format_report
from sw_sourcing.storage.db import AlertRecord
from tests.unit.factories import FakeSmtp


def make_alert(**overrides: object) -> AlertRecord:
    defaults: dict[str, object] = {
        "id": 1,
        "source": "ebay",
        "listing_id": "1",
        "title": "Vintage Kenner Star Wars lot",
        "url": "https://example.com/1",
        "image_url": "https://example.com/1.jpg",
        "outcome": "buy",
        "cost_per_figure": 4.5,
        "target_grade_count": 10,
        "max_repro_risk": "low",
        "returns_accepted": True,
        "suggested_offer": None,
        "vision_notes": None,
        "alerted_at": "2026-07-07T12:00:00Z",
        "reported_at": None,
    }
    defaults.update(overrides)
    return AlertRecord(**defaults)  # type: ignore[arg-type]


def test_format_report_subject_includes_the_count() -> None:
    subject, _ = format_report([make_alert(), make_alert(id=2, listing_id="2")])

    assert "2" in subject


def test_format_report_html_includes_title_link_and_cost() -> None:
    _, html = format_report([make_alert()])

    assert "Vintage Kenner Star Wars lot" in html
    assert "https://example.com/1" in html
    assert "4.50" in html
    assert "https://example.com/1.jpg" in html


def test_format_report_groups_by_outcome_with_a_heading() -> None:
    alerts = [
        make_alert(id=1, outcome="buy", listing_id="1"),
        make_alert(id=2, outcome="review", listing_id="2"),
    ]

    _, html = format_report(alerts)

    assert "Buy" in html
    assert "Review" in html


def test_format_report_shows_suggested_offer_when_present() -> None:
    _, html = format_report([make_alert(outcome="negotiate", suggested_offer=42.5)])

    assert "42.50" in html


def test_format_report_omits_image_when_none() -> None:
    _, html = format_report([make_alert(image_url=None)])

    assert "<img" not in html


def test_format_report_includes_vision_notes_when_present() -> None:
    _, html = format_report(
        [make_alert(vision_notes="Two droids lack a visible backstamp.")]
    )

    assert "Two droids lack a visible backstamp." in html


def test_format_report_omits_notes_block_when_absent() -> None:
    _, html = format_report([make_alert(vision_notes=None)])

    assert "Notes" not in html


def test_format_report_escapes_html_in_vision_notes() -> None:
    _, html = format_report([make_alert(vision_notes="<script>alert('x')</script>")])

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_email_sender_logs_in_and_sends_the_message() -> None:
    smtp = FakeSmtp()
    sender = EmailSender(
        host="smtp.example.com",
        port=465,
        username="from@example.com",
        password="app-password",
        smtp_factory=lambda: smtp,
    )

    sender.send(to_addr="to@example.com", subject="Test subject", html_body="<p>hi</p>")

    assert smtp.login_calls == [("from@example.com", "app-password")]
    assert len(smtp.sent_messages) == 1
    message = smtp.sent_messages[0]
    assert message["Subject"] == "Test subject"
    assert message["From"] == "from@example.com"
    assert message["To"] == "to@example.com"
    html_part = message.get_body(preferencelist=("html",))
    assert html_part is not None
    assert "<p>hi</p>" in html_part.get_content()
