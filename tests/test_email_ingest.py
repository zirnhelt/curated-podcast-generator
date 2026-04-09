"""Tests for email_ingest module — sender blocklist, theme scoring, and full ingest pipeline."""

import base64
import json
import sys
import uuid
from pathlib import Path
from email.mime.text import MIMEText
from unittest.mock import MagicMock

import pytest

# email_ingest only uses stdlib at import time; no stubs needed
sys.path.insert(0, str(Path(__file__).parent.parent))
from email_ingest import _is_blocked_sender, _is_blocked_subject, _score_themes, ingest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def themes():
    """Load the real themes config so tests reflect actual keyword lists."""
    themes_file = Path(__file__).parent.parent / "config" / "themes.json"
    with open(themes_file) as f:
        return json.load(f)


SAMPLE_BLOCKLIST = {
    "domains": [
        "podmatch.com",
        "podseo.com",
        "truefans.fm",
        "cloudflare.com",
        "em1.cloudflare.com",
        "notify.cloudflare.com",
        "paypal.com",
        "intl.paypal.com",
        "cira.ca",
    ],
    "patterns": [
        "mailer-daemon",
        "mail delivery subsystem",
        "postmaster@",
    ],
}


# ---------------------------------------------------------------------------
# _is_blocked_sender
# ---------------------------------------------------------------------------

class TestIsBlockedSender:
    def test_blocks_exact_domain(self):
        assert _is_blocked_sender("PodMatch Team <team@podmatch.com>", SAMPLE_BLOCKLIST)

    def test_blocks_subdomain(self):
        assert _is_blocked_sender("Cloudflare <em@em1.cloudflare.com>", SAMPLE_BLOCKLIST)

    def test_blocks_truefans(self):
        assert _is_blocked_sender("TrueFans <support@truefans.fm>", SAMPLE_BLOCKLIST)

    def test_blocks_paypal(self):
        assert _is_blocked_sender('"service@intl.paypal.com" <service@intl.paypal.com>', SAMPLE_BLOCKLIST)

    def test_blocks_cloudflare_registrar(self):
        assert _is_blocked_sender("Cloudflare Registrar <noreply@notify.cloudflare.com>", SAMPLE_BLOCKLIST)

    def test_blocks_cira(self):
        assert _is_blocked_sender("Registry Support <info@cira.ca>", SAMPLE_BLOCKLIST)

    def test_blocks_mailer_daemon_pattern(self):
        assert _is_blocked_sender(
            "Mail Delivery Subsystem <mailer-daemon@googlemail.com>", SAMPLE_BLOCKLIST
        )

    def test_blocks_mail_delivery_subsystem_pattern(self):
        assert _is_blocked_sender(
            "Mail Delivery Subsystem <mailer-daemon@googlemail.com>", SAMPLE_BLOCKLIST
        )

    def test_blocks_pattern_case_insensitive(self):
        assert _is_blocked_sender("MAILER-DAEMON <mailer-daemon@example.com>", SAMPLE_BLOCKLIST)

    def test_allows_editorial_newsletter(self):
        assert not _is_blocked_sender(
            "Animikii Indigenous Technology <news@animikii.com>", SAMPLE_BLOCKLIST
        )

    def test_allows_listener_feedback(self):
        assert not _is_blocked_sender(
            "Erich Zirnhelt <zirnhelt@gmail.com>", SAMPLE_BLOCKLIST
        )

    def test_empty_blocklist_allows_all(self):
        assert not _is_blocked_sender("anyone@anything.com", {})

    def test_no_email_address_in_from(self):
        # No @ — domain extraction returns nothing; pattern check still works
        assert not _is_blocked_sender("No Email Here", SAMPLE_BLOCKLIST)

    def test_blocks_podseo(self):
        assert _is_blocked_sender(
            "Andrea De Marsi <demars@podseo.com>", SAMPLE_BLOCKLIST
        )


# ---------------------------------------------------------------------------
# _is_blocked_subject
# ---------------------------------------------------------------------------

SAMPLE_SUBJECT_BLOCKLIST = ["test"]


class TestIsBlockedSubject:
    def test_blocks_bare_test(self):
        assert _is_blocked_subject("Test", SAMPLE_SUBJECT_BLOCKLIST)

    def test_blocks_test_with_number(self):
        assert _is_blocked_subject("Test 4", SAMPLE_SUBJECT_BLOCKLIST)

    def test_blocks_test_with_word(self):
        assert _is_blocked_subject("Test two", SAMPLE_SUBJECT_BLOCKLIST)

    def test_case_insensitive(self):
        assert _is_blocked_subject("TEST 5", SAMPLE_SUBJECT_BLOCKLIST)

    def test_does_not_block_mid_word(self):
        # "testing" starts with "test" but is a different word
        assert not _is_blocked_subject("Testing new tech", SAMPLE_SUBJECT_BLOCKLIST)

    def test_does_not_block_unrelated_subject(self):
        assert not _is_blocked_subject("Cariboo community update", SAMPLE_SUBJECT_BLOCKLIST)

    def test_empty_blocklist_blocks_nothing(self):
        assert not _is_blocked_subject("Test", [])


# ---------------------------------------------------------------------------
# _score_themes — from_address included in scoring text
# ---------------------------------------------------------------------------

class TestScoreThemes:
    def test_animikii_scores_indigenous_not_wild_spaces(self, themes):
        """Sender org 'Animikii Indigenous Technology' should tip theme to day 3."""
        from_address = "Animikii Indigenous Technology <news@animikii.com>"
        subject = "From wildfires to clam gardens: decolonizing data"
        body = ""  # real body is image alt-text with no useful keywords
        text = f"{from_address} {subject} {body}"
        tag, day = _score_themes(text, themes)
        assert tag == "Indigenous Lands & Innovation"
        assert day == 3

    def test_subject_only_would_score_wild_spaces(self, themes):
        """Without from_address, 'wildfire' in subject scores day 4 — the old bug."""
        subject = "From wildfires to clam gardens: decolonizing data"
        tag, day = _score_themes(subject, themes)
        assert day == 4

    def test_indigenous_keywords_score_day_3(self, themes):
        text = "First Nations reconciliation traditional knowledge land rights"
        tag, day = _score_themes(text, themes)
        assert day == 3

    def test_no_keywords_returns_none(self, themes):
        tag, day = _score_themes("hello world nothing relevant here", themes)
        assert tag is None
        assert day is None

    def test_empty_text_returns_none(self, themes):
        tag, day = _score_themes("", themes)
        assert tag is None
        assert day is None

    def test_arts_culture_keywords(self, themes):
        text = "local arts festival storytelling media podcast"
        tag, day = _score_themes(text, themes)
        assert day == 0

    def test_cariboo_local_keywords(self, themes):
        text = "Williams Lake community rural Quesnel local news"
        tag, day = _score_themes(text, themes)
        assert day == 5

    def test_higher_score_wins_over_single_match(self, themes):
        """Multiple keyword hits for one theme beats a single hit for another."""
        # science/ecology keywords strongly favour day 6
        text = "science research ecology biodiversity watershed field research citizen science"
        tag, day = _score_themes(text, themes)
        assert day == 6


# ---------------------------------------------------------------------------
# ingest() — full pipeline with mocked Gmail service
# ---------------------------------------------------------------------------

def _make_raw_email(subject, from_addr, body, message_id=None):
    """Build a base64url-encoded raw email dict as returned by the Gmail API."""
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = "podcast@example.com"
    msg["Date"] = "Thu, 01 Jan 2026 00:00:00 +0000"
    msg["Message-ID"] = message_id or f"<{uuid.uuid4().hex}@example.com>"
    return {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}


def _mock_gmail_service(raw_email_dicts):
    """Return a MagicMock Gmail service that yields the given raw email dicts."""
    svc = MagicMock()
    svc.users().messages().list().execute.return_value = {
        "messages": [{"id": str(i)} for i in range(len(raw_email_dicts))]
    }
    svc.users().messages().get().execute.side_effect = raw_email_dicts
    svc.users().messages().modify().execute.return_value = {}
    return svc


class TestIngest:
    def test_themed_feedback_email_is_added(self, tmp_path, monkeypatch):
        """A feedback email whose body matches a theme keyword is queued."""
        raw = _make_raw_email(
            subject="Love the show",
            from_addr="listener@example.com",
            body="Great coverage of Williams Lake and Cariboo rural communities.",
        )
        svc = _mock_gmail_service([raw])

        queue_file = tmp_path / "email_queue.json"
        monkeypatch.setenv("GMAIL_LABEL", "podcast")
        monkeypatch.setattr("email_ingest._build_gmail_service", lambda: svc)
        monkeypatch.setattr("email_ingest.QUEUE_FILE", queue_file)

        added = ingest(dry_run=False)

        assert added == 1
        queue = json.loads(queue_file.read_text())
        assert len(queue["items"]) == 1
        item = queue["items"][0]
        assert item["type"] == "feedback"
        assert item["theme_tag"] == "Cariboo Voices & Local News"
        assert item["status"] == "pending"
        assert item["subject"] == "Love the show"

    def test_unthemed_email_is_skipped(self, tmp_path, monkeypatch):
        """An email with no theme keyword match is not added to the queue."""
        raw = _make_raw_email(
            subject="Test",
            from_addr="sender@example.com",
            body="Hello there, just a generic message with no matching keywords.",
        )
        svc = _mock_gmail_service([raw])

        queue_file = tmp_path / "email_queue.json"
        monkeypatch.setenv("GMAIL_LABEL", "podcast")
        monkeypatch.setattr("email_ingest._build_gmail_service", lambda: svc)
        monkeypatch.setattr("email_ingest.QUEUE_FILE", queue_file)

        added = ingest(dry_run=False)

        assert added == 0
        assert not queue_file.exists()

    def test_duplicate_email_is_skipped(self, tmp_path, monkeypatch):
        """An email whose Message-ID is already in the queue is not re-added."""
        mid = "<already-seen@example.com>"
        raw = _make_raw_email(
            subject="Cariboo community update",
            from_addr="sender@example.com",
            body="Williams Lake local news and community stories.",
            message_id=mid,
        )
        svc = _mock_gmail_service([raw])

        queue_file = tmp_path / "email_queue.json"
        queue_file.write_text(json.dumps({
            "version": 1,
            "items": [{"message_id": mid, "status": "pending"}],
        }))

        monkeypatch.setenv("GMAIL_LABEL", "podcast")
        monkeypatch.setattr("email_ingest._build_gmail_service", lambda: svc)
        monkeypatch.setattr("email_ingest.QUEUE_FILE", queue_file)

        added = ingest(dry_run=False)

        assert added == 0

    def test_subject_blocked_email_is_skipped(self, tmp_path, monkeypatch):
        """An email whose subject starts with 'test' is skipped even if it would match a theme."""
        raw = _make_raw_email(
            subject="Test 4",
            from_addr="listener@example.com",
            body="Williams Lake and Cariboo community stories.",  # would match a theme
        )
        svc = _mock_gmail_service([raw])

        queue_file = tmp_path / "email_queue.json"
        monkeypatch.setenv("GMAIL_LABEL", "podcast")
        monkeypatch.setattr("email_ingest._build_gmail_service", lambda: svc)
        monkeypatch.setattr("email_ingest.QUEUE_FILE", queue_file)

        added = ingest(dry_run=False)

        assert added == 0
        assert not queue_file.exists()

    def test_dry_run_does_not_write_queue(self, tmp_path, monkeypatch):
        """dry_run=True parses emails but never writes the queue file."""
        raw = _make_raw_email(
            subject="Science in Cariboo",
            from_addr="researcher@example.com",
            body="New citizen science research on watershed ecology and biodiversity.",
        )
        svc = _mock_gmail_service([raw])

        queue_file = tmp_path / "email_queue.json"
        monkeypatch.setenv("GMAIL_LABEL", "podcast")
        monkeypatch.setattr("email_ingest._build_gmail_service", lambda: svc)
        monkeypatch.setattr("email_ingest.QUEUE_FILE", queue_file)

        added = ingest(dry_run=True)

        assert added == 1
        assert not queue_file.exists()
