"""Tests for dedup_articles module."""

import json
import pytest
from unittest.mock import MagicMock
from dedup_articles import normalize_title, title_similarity, format_evolving_story_context, cluster_and_rescore_corpus


class TestNormalizeTitle:
    def test_removes_source_tags(self):
        assert normalize_title("[CNN] Breaking News Story") == "breaking news story"

    def test_strips_whitespace(self):
        assert normalize_title("  Hello World  ") == "hello world"

    def test_lowercases(self):
        assert normalize_title("ALL CAPS TITLE") == "all caps title"

    def test_empty_string(self):
        assert normalize_title("") == ""


class TestTitleSimilarity:
    def test_identical_titles(self):
        assert title_similarity("Breaking News", "Breaking News") == 1.0

    def test_completely_different(self):
        assert title_similarity("abc xyz", "123 456") < 0.3

    def test_similar_titles(self):
        sim = title_similarity(
            "Tesla Announces New Battery Tech",
            "Tesla Reveals New Battery Technology"
        )
        assert sim > 0.6

    def test_source_tags_ignored(self):
        sim = title_similarity(
            "[Reuters] AI Breakthrough",
            "[AP News] AI Breakthrough"
        )
        assert sim > 0.8


class TestFormatEvolvingStoryContext:
    def test_empty_list(self):
        assert format_evolving_story_context([]) == ""

    def test_formats_stories(self):
        stories = [{
            "article": {"title": "New Update on AI Law"},
            "original_date": "2026-01-30",
            "original_title": "AI Law Proposed",
            "similarity": 0.75,
        }]
        result = format_evolving_story_context(stories)
        assert "EVOLVING STORIES" in result
        assert "New Update on AI Law" in result
        assert "2026-01-30" in result


def _make_client(clusters):
    """Return a mock Anthropic client whose messages.create returns the given clusters."""
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps({"clusters": clusters}))]
    client = MagicMock()
    client.messages.create.return_value = response
    return client


class TestClusterAndRescore:
    def _articles(self):
        return [
            {"title": "EFF quits X", "summary": "EFF leaves X platform", "_boosted_score": 80, "ai_score": 80},
            {"title": "EFF leaves Twitter", "summary": "EFF departs X", "_boosted_score": 60, "ai_score": 60},
            {"title": "EFF drops X account", "summary": "EFF no longer on X", "_boosted_score": 50, "ai_score": 50},
            {"title": "Unrelated tech story", "summary": "Something else", "_boosted_score": 75, "ai_score": 75},
        ]

    def test_no_client_returns_unchanged(self):
        articles = self._articles()
        result = cluster_and_rescore_corpus(articles, "Tech", client=None)
        assert result is articles

    def test_empty_articles_returns_unchanged(self):
        result = cluster_and_rescore_corpus([], "Tech", client=_make_client([]))
        assert result == []

    def test_no_clusters_returns_unchanged_scores(self):
        articles = self._articles()
        client = _make_client([])
        result = cluster_and_rescore_corpus(articles, "Tech", client=client)
        for orig, res in zip(articles, result):
            assert res["_boosted_score"] == orig["_boosted_score"]

    def test_canonical_article_score_unchanged(self):
        articles = self._articles()
        # Indices 0, 1, 2 form a cluster; article 0 has highest score (80)
        client = _make_client([{"label": "EFF quitting X", "indices": [0, 1, 2]}])
        result = cluster_and_rescore_corpus(articles, "Tech", client=client)
        assert result[0]["_boosted_score"] == 80
        assert not result[0].get("_cluster_suppressed")

    def test_duplicates_are_penalized(self):
        articles = self._articles()
        client = _make_client([{"label": "EFF quitting X", "indices": [0, 1, 2]}])
        result = cluster_and_rescore_corpus(articles, "Tech", client=client)
        # Indices 1 and 2 should be penalized to 30% of original
        assert result[1]["_boosted_score"] == max(1, int(60 * 0.3))
        assert result[2]["_boosted_score"] == max(1, int(50 * 0.3))
        assert result[1]["_cluster_suppressed"] is True
        assert result[2]["_cluster_suppressed"] is True

    def test_topic_cluster_tag_added_to_all_in_cluster(self):
        articles = self._articles()
        client = _make_client([{"label": "EFF quitting X", "indices": [0, 1, 2]}])
        result = cluster_and_rescore_corpus(articles, "Tech", client=client)
        assert result[0]["_topic_cluster"] == "EFF quitting X"
        assert result[1]["_topic_cluster"] == "EFF quitting X"
        assert result[2]["_topic_cluster"] == "EFF quitting X"

    def test_singleton_articles_unaffected(self):
        articles = self._articles()
        client = _make_client([{"label": "EFF quitting X", "indices": [0, 1, 2]}])
        result = cluster_and_rescore_corpus(articles, "Tech", client=client)
        # Index 3 is not in any cluster
        assert result[3]["_boosted_score"] == 75
        assert not result[3].get("_cluster_suppressed")
        assert not result[3].get("_topic_cluster")

    def test_invalid_json_fallback_returns_unchanged(self):
        response = MagicMock()
        response.content = [MagicMock(text="this is not json {{{{")]
        client = MagicMock()
        client.messages.create.return_value = response
        articles = self._articles()
        result = cluster_and_rescore_corpus(articles, "Tech", client=client)
        # Should fall back and return originals unchanged
        for orig, res in zip(articles, result):
            assert res["_boosted_score"] == orig["_boosted_score"]

    def test_claude_exception_fallback_returns_unchanged(self):
        client = MagicMock()
        client.messages.create.side_effect = Exception("API error")
        articles = self._articles()
        result = cluster_and_rescore_corpus(articles, "Tech", client=client)
        assert result is articles

    def test_single_index_cluster_ignored(self):
        articles = self._articles()
        # A "cluster" with only one article should be a no-op
        client = _make_client([{"label": "Lone story", "indices": [0]}])
        result = cluster_and_rescore_corpus(articles, "Tech", client=client)
        for orig, res in zip(articles, result):
            assert res["_boosted_score"] == orig["_boosted_score"]

    def test_original_articles_not_mutated(self):
        articles = self._articles()
        client = _make_client([{"label": "EFF quitting X", "indices": [0, 1, 2]}])
        cluster_and_rescore_corpus(articles, "Tech", client=client)
        # Original list items must not have been modified
        assert articles[1]["_boosted_score"] == 60
        assert not articles[1].get("_cluster_suppressed")
