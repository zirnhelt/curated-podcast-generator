"""Tests for dedup_articles module."""

import pytest
from dedup_articles import normalize_title, title_similarity, format_evolving_story_context


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
