"""Tests for episode_review.py, against a slice of the real 2026-08-19 job log.

The parser reads the pipeline's own stdout, which moves whenever a print
statement does. These tests pin the extractions that the review's prose depends
on, and — more importantly — pin the two behaviours that protect it from
publishing something false: echoed workflow source must never be read as an
emitted warning, and a missing line must never raise.
"""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import episode_review

FIXTURE = Path(__file__).parent / "fixtures" / "run_2026-08-19.log"


@pytest.fixture(scope="module")
def facts():
    return episode_review.scan_log(FIXTURE.read_text("utf-8"))


class TestScanLog:
    def test_theme_override_keeps_both_names(self, facts):
        # The feed can rename the day; the review needs to be able to say so.
        assert facts["theme_configured"] == "Gear, Gadgets & Practical Tech"
        assert facts["theme_feed"] == "Repair Culture & Practical Tech"

    def test_focus_and_fallback(self, facts):
        assert facts["focus"] == [1, 4, "Maker & Repair"]
        assert "only 1 article(s) matched focus" in facts["focus_fallback"]

    def test_curation_numbers(self, facts):
        assert facts["articles_loaded"] == 83
        assert facts["dedup_filtered"] == 77
        assert facts["focus_routing"] == [2, 15]
        assert facts["roundup_pool"][0] == 15
        assert facts["roundup_dropped"] == 46

    def test_script_and_quality(self, facts):
        assert facts["short_script"] == [3092, 3400]
        assert facts["quality"] == [1, 1.16, 3068]
        assert facts["citation_alignment"] == [5, 15, 3, 3]

    def test_degradation_is_captured_with_its_reason(self, facts):
        name, reason = facts["degradations"][0]
        assert name == "render/gemini-canary"
        assert "OpenAI" in reason

    def test_repeated_warnings_collapse_to_a_count(self, facts):
        # Nine short segments is one observation, not nine paragraphs.
        assert facts["tts_short_segments"] == 9
        assert facts["tts_retry_failed"] == 9

    def test_multi_extractions(self, facts):
        assert len(facts["canary_failures"]) == 2
        assert len(facts["brave_failures"]) == 3
        assert len(facts["clusters"]) == 3


class TestFalsehoodGuards:
    def test_echoed_workflow_source_is_not_a_warning(self, facts):
        """The runner prints each step's script before running it, and that
        source contains warning strings for failures that never happened."""
        blob = json.dumps(facts)
        assert "Anthropic usage limit reached" not in blob
        assert "Upstream feed had no usable articles" not in blob
        assert facts["other_warnings"] == []

    def test_unrecognised_warning_still_reaches_the_review(self):
        # A new failure mode has no regex until it has happened once.
        log = "⚠️  R2 bucket credential missing — site not synced\n"
        assert episode_review.scan_log(log)["other_warnings"] == [
            "⚠️  R2 bucket credential missing — site not synced"]

    def test_empty_log_does_not_raise(self):
        assert episode_review.scan_log("")["other_warnings"] == []

    def test_partial_log_yields_partial_facts(self):
        facts = episode_review.scan_log("✅ Loaded 40 articles from podcast feed\n")
        assert facts["articles_loaded"] == 40
        assert "quality" not in facts


class TestTriggerLabels:
    @pytest.mark.parametrize("created,expected,late", [
        ("2026-08-19T08:48:12Z", "Primary (1:05 AM Pacific)", 43),
        ("2026-08-19T09:33:32Z", "Fallback 1 (2:05 AM Pacific)", 28),
        ("2026-08-19T10:35:15Z", "Fallback 2 (3:05 AM Pacific)", 30),
        ("2026-08-19T08:05:00Z", "Primary (1:05 AM Pacific)", 0),
    ])
    def test_run_is_matched_to_its_cron_with_drift(self, created, expected, late):
        label, minutes = episode_review._trigger_label(episode_review._parse(created))
        assert (label, minutes) == (expected, late)


class TestRendering:
    def test_numbers_table_omits_absent_facts(self):
        html = episode_review.render_numbers_table({"articles_loaded": 83})
        assert "83" in html
        assert "Citations matched" not in html

    def test_numbers_table_is_empty_without_facts(self):
        assert episode_review.render_numbers_table({}) == ""

    def test_run_table_omitted_when_no_runs(self):
        assert episode_review.render_run_table([]) == ""

    def test_feed_is_valid_rss(self, tmp_path):
        index = [{
            "date": "2026-08-19",
            "title": "Episode Review — Cariboo Signals, August 19, 2026",
            "url": "https://example.invalid/podcasts/reviews/episode-review-2026-08-19.html",
            "content_html": "<p>Body &amp; more</p>",
            "published": "2026-08-19T13:48:21+00:00",
        }]
        root = ET.fromstring(episode_review.build_feed(index))
        items = root.findall(".//item")
        assert len(items) == 1
        assert items[0].find("title").text.startswith("Episode Review")
        assert items[0].find("pubDate").text == "Wed, 19 Aug 2026 13:48:21 +0000"

    def test_page_escapes_the_title(self):
        _title, _content, page = episode_review.build_review(
            "2026-08-19", [], {"theme_feed": "Repair & Practical Tech"}, "")
        assert "Repair &amp; Practical Tech" in page
        assert "<h1>Episode Review — Cariboo Signals, August 19, 2026</h1>" in page


class TestIndex:
    def test_rerun_replaces_the_same_date(self, tmp_path, monkeypatch):
        # A re-render must not append a second review for one day.
        monkeypatch.setattr(episode_review, "REVIEWS_DIR", tmp_path)
        monkeypatch.setattr(episode_review, "INDEX_FILE", tmp_path / "index.json")
        episode_review.update_index("2026-08-19", "First", "<p>one</p>")
        index = episode_review.update_index("2026-08-19", "Second", "<p>two</p>")
        assert len(index) == 1
        assert index[0]["title"] == "Second"

    def test_index_is_capped_and_newest_first(self, tmp_path, monkeypatch):
        monkeypatch.setattr(episode_review, "REVIEWS_DIR", tmp_path)
        monkeypatch.setattr(episode_review, "INDEX_FILE", tmp_path / "index.json")
        monkeypatch.setattr(episode_review, "FEED_LIMIT", 3)
        for day in range(1, 6):
            index = episode_review.update_index(f"2026-08-0{day}", f"Day {day}", "<p></p>")
        assert [e["date"] for e in index] == ["2026-08-05", "2026-08-04", "2026-08-03"]


class TestNarrativeScrub:
    """The narrative is injected as raw HTML into a published page."""

    @pytest.mark.parametrize("dangerous", [
        "<p>ok</p><script>alert(1)</script>",
        "<p onclick=\"steal()\">ok</p>",
        "<iframe src='evil'></iframe><p>ok</p>",
        "<object data='x'></object><p>ok</p>",
    ])
    def test_executable_content_is_removed(self, dangerous):
        cleaned = episode_review._scrub_html(dangerous)
        assert "<p>ok</p>" in cleaned
        for token in ("script", "onclick", "iframe", "object"):
            assert token not in cleaned.lower()

    def test_prose_markup_survives(self):
        fragment = "<h3>Head</h3>\n<p>Body <em>with</em> <code>code</code></p>"
        assert episode_review._scrub_html(fragment) == fragment
