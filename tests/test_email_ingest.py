"""Tests for email_ingest module — sender blocklist and theme scoring."""

import json
import sys
from pathlib import Path

import pytest

# email_ingest only uses stdlib at import time; no stubs needed
sys.path.insert(0, str(Path(__file__).parent.parent))
from email_ingest import _is_blocked_sender, _score_themes


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
