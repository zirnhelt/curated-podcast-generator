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


class TestSelectWelcomeHost:
    def test_returns_valid_host(self):
        for _ in range(20):
            host = select_welcome_host()
            assert host in ("riley", "casey")
