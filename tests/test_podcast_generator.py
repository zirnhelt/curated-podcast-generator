"""Tests for deterministic functions in podcast_generator.

Heavy third-party dependencies (anthropic, openai, pydub) are stubbed in
tests/conftest.py at import time so podcast_generator can be imported safely.
"""

import pytest

from podcast_generator import (
    get_article_scores,
    extract_topics_and_themes,
    parse_script_into_segments,
    select_welcome_host,
    _extract_pacing_tag,
    heuristic_gap_ms,
)


class TestGetArticleScores:
    def test_matches_by_title(self):
        articles = [
            {"title": "AI Boom", "url": "https://a.com"},
            {"title": "Climate Fix", "url": "https://b.com"},
        ]
        scoring_data = {
            "key1": {"title": "AI Boom", "score": 90},
            "key2": {"title": "Climate Fix", "score": 40},
        }
        result = get_article_scores(articles, scoring_data)
        assert result[0]["ai_score"] == 90
        assert result[1]["ai_score"] == 40

    def test_unscored_article_gets_zero(self):
        articles = [{"title": "Unknown Story", "url": "https://c.com"}]
        result = get_article_scores(articles, {})
        assert result[0]["ai_score"] == 0

    def test_sorted_descending(self):
        articles = [
            {"title": "Low", "url": "https://a.com"},
            {"title": "High", "url": "https://b.com"},
        ]
        scoring_data = {
            "k1": {"title": "Low", "score": 10},
            "k2": {"title": "High", "score": 95},
        }
        result = get_article_scores(articles, scoring_data)
        assert result[0]["title"] == "High"


class TestParseScriptIntoSegments:
    SAMPLE_SCRIPT = """
**RILEY:** Welcome to the show, it's Monday.
**CASEY:** Good to be here, let's get started.

**SEGMENT 1: THE WEEK'S TECH**
**RILEY:** First up, a big story about AI regulation in Canada.
**CASEY:** That's an important development.
**RILEY:** Next, solar panels are getting cheaper in rural areas.

**SEGMENT 2: CARIBOO CONNECTIONS - Community Infrastructure**
**CASEY:** Let's talk about community broadband projects.
**RILEY:** Great topic. Several communities have launched co-ops.
**CASEY:** We'd love to hear your thoughts. Have a great day.
"""

    SCRIPT_WITH_SPOTLIGHT = """
**RILEY:** Welcome to the show, it's Friday.
**CASEY:** Good to be here, let's get started.

**NEWS ROUNDUP**
**RILEY:** First up, a big story about AI regulation in Canada.
**CASEY:** That's an important development for rural communities.

**COMMUNITY SPOTLIGHT**
**CASEY:** Before we dive deeper, a quick shout-out to Scout Island Nature Centre, a volunteer-run gem right here in Williams Lake.
**RILEY:** They do fantastic work with kids and nature education.

**DEEP DIVE: CARIBOO CONNECTIONS - Wild Spaces & Outdoor Life**
**CASEY:** Let's talk about trail infrastructure in the Cariboo.
**RILEY:** Great topic. Several communities have launched new trail projects.
**CASEY:** We'd love to hear your thoughts. Have a great weekend.
"""

    def test_welcome_section(self):
        segments = parse_script_into_segments(self.SAMPLE_SCRIPT)
        assert len(segments["welcome"]) == 2
        assert segments["welcome"][0]["speaker"] == "riley"
        assert segments["welcome"][1]["speaker"] == "casey"

    def test_news_section(self):
        segments = parse_script_into_segments(self.SAMPLE_SCRIPT)
        assert len(segments["news"]) >= 2
        assert segments["news"][0]["speaker"] == "riley"

    def test_deep_dive_section(self):
        segments = parse_script_into_segments(self.SAMPLE_SCRIPT)
        assert len(segments["deep_dive"]) >= 2

    def test_community_spotlight_section(self):
        segments = parse_script_into_segments(self.SCRIPT_WITH_SPOTLIGHT)
        assert len(segments["community_spotlight"]) == 2
        assert segments["community_spotlight"][0]["speaker"] == "casey"
        assert "Scout Island" in segments["community_spotlight"][0]["text"]

    def test_spotlight_does_not_leak_into_news(self):
        segments = parse_script_into_segments(self.SCRIPT_WITH_SPOTLIGHT)
        for seg in segments["news"]:
            assert "Scout Island" not in seg["text"]

    def test_empty_spotlight_when_absent(self):
        segments = parse_script_into_segments(self.SAMPLE_SCRIPT)
        assert segments["community_spotlight"] == []

    def test_filters_short_text(self):
        """Segments with <= 10 chars of text should be dropped."""
        segments = parse_script_into_segments("**RILEY:** Hi\n**CASEY:** Ok")
        total = sum(len(v) for v in segments.values())
        assert total == 0


class TestExtractTopicsAndThemes:
    def test_extracts_keywords(self):
        script = "Today we discuss AI and machine learning in rural broadband."
        topics, themes = extract_topics_and_themes(script)
        assert "AI" in topics
        assert "machine learning" in topics
        assert "rural broadband" in topics

    def test_extracts_themes(self):
        script = "Rural community innovation and sustainability efforts."
        topics, themes = extract_topics_and_themes(script)
        assert "rural development" in themes
        assert "technology adoption" in themes

    def test_empty_script(self):
        topics, themes = extract_topics_and_themes("")
        assert topics == []
        assert themes == []

    def test_with_articles(self):
        script = "Today we talk about technology."
        articles = [{"title": "Big Solar Farm Opens - Reuters", "url": "x"}]
        topics, _ = extract_topics_and_themes(script, news_articles=articles)
        assert any("Solar Farm" in t for t in topics)


class TestExtractPacingTag:
    def test_overlap_tag(self):
        gap, text = _extract_pacing_tag("[overlap:-150] Ha! That tracks.")
        assert gap == -150
        assert text == "Ha! That tracks."

    def test_pause_tag(self):
        gap, text = _extract_pacing_tag("[pause:400] But here's the thing...")
        assert gap == 400
        assert text == "But here's the thing..."

    def test_no_tag(self):
        gap, text = _extract_pacing_tag("Just a normal line.")
        assert gap is None
        assert text == "Just a normal line."

    def test_negative_pause(self):
        gap, text = _extract_pacing_tag("[pause:-50] Quick.")
        assert gap == -50

    def test_zero(self):
        gap, text = _extract_pacing_tag("[pause:0] Immediate.")
        assert gap == 0
        assert text == "Immediate."


class TestHeuristicGapMs:
    # --- default (deep_dive) pacing ---
    def test_short_interjection(self):
        gap = heuristic_gap_ms("Ha!", "riley", "casey")
        assert gap <= 50

    def test_medium_reaction(self):
        gap = heuristic_gap_ms("That's an important development for rural areas.", "riley", "casey")
        assert 50 < gap <= 200

    def test_normal_speaker_change(self):
        gap = heuristic_gap_ms("Let me tell you about a big story that just broke about AI regulation in Canada and its impact.", "riley", "casey")
        assert gap >= 300

    def test_same_speaker_zero(self):
        gap = heuristic_gap_ms("Continuing my thought here with more detail.", "riley", "riley")
        assert gap == 0

    # --- news section: slower, more measured pacing ---
    def test_news_short_interjection(self):
        gap = heuristic_gap_ms("Ha!", "riley", "casey", section="news")
        assert gap >= 100  # noticeably wider than deep_dive

    def test_news_medium_reaction(self):
        gap = heuristic_gap_ms("That's an important development for rural areas.", "riley", "casey", section="news")
        assert gap >= 300

    def test_news_normal_speaker_change(self):
        gap = heuristic_gap_ms("Let me tell you about a big story that just broke about AI regulation in Canada and its impact.", "riley", "casey", section="news")
        assert gap >= 500

    def test_news_same_speaker_short_continuation(self):
        gap = heuristic_gap_ms("Continuing my thought here with more detail.", "riley", "riley", section="news")
        assert gap == 350


class TestParseScriptPacingTags:
    def test_overlap_tag_extracted(self):
        script = """
**RILEY:** First, a big story about AI regulation in Canada.
**CASEY:** [overlap:-100] Ha! That tracks.

**SEGMENT 2: CARIBOO CONNECTIONS**
**RILEY:** Let's dive into broadband projects in the region.
**CASEY:** [pause:400] But here's the real question about funding and sustainability.
"""
        segments = parse_script_into_segments(script)
        # Casey's welcome/news reaction should have the overlap tag
        news_casey = [s for s in segments['welcome'] if s['speaker'] == 'casey']
        assert len(news_casey) > 0
        assert news_casey[0]['gap_ms'] == -100
        assert "Ha! That tracks." in news_casey[0]['text']
        # Deep dive Casey should have the pause tag
        dd_casey = [s for s in segments['deep_dive'] if s['speaker'] == 'casey']
        assert len(dd_casey) > 0
        assert dd_casey[0]['gap_ms'] == 400

    def test_no_tag_gives_none(self):
        script = "**RILEY:** Just a normal line of dialogue for testing."
        segments = parse_script_into_segments(script)
        for section in segments.values():
            for seg in section:
                assert seg['gap_ms'] is None


class TestSelectWelcomeHost:
    def test_returns_valid_host(self):
        for _ in range(20):
            host = select_welcome_host()
            assert host in ("riley", "casey")
