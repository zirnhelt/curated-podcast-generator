"""Tests for deterministic functions in podcast_generator.

Since podcast_generator imports anthropic/openai/pydub at module level and exits
if missing, we mock those imports before importing the module.
"""

import sys
import types
import pytest


@pytest.fixture(autouse=True, scope="module")
def mock_dependencies():
    """Provide stubs for heavy third-party packages so the module can load."""
    modules = {}
    for mod_name in ("anthropic", "openai", "pydub"):
        if mod_name not in sys.modules:
            modules[mod_name] = sys.modules[mod_name] = types.ModuleType(mod_name)

    # Provide the classes the module references at import time
    sys.modules["anthropic"].Anthropic = type("Anthropic", (), {})
    sys.modules["openai"].OpenAI = type("OpenAI", (), {})
    sys.modules["pydub"].AudioSegment = type("AudioSegment", (), {
        "from_mp3": staticmethod(lambda *a, **k: None),
        "silent": staticmethod(lambda *a, **k: None),
        "empty": staticmethod(lambda *a, **k: None),
    })

    yield

    for mod_name, mod in modules.items():
        if sys.modules.get(mod_name) is mod:
            del sys.modules[mod_name]


# Import after mocks are installed
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
