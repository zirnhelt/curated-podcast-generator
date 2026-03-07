"""Tests for the ambient transition module."""

import json
from pathlib import Path
from unittest.mock import patch

from ambient import load_ambient_config, get_ambient_transition


class TestLoadAmbientConfig:
    def test_loads_valid_config(self):
        config = load_ambient_config()
        assert config is not None
        assert "themes" in config
        assert "duration_ms" in config

    def test_has_all_themes(self):
        config = load_ambient_config()
        themes = config["themes"]
        expected = [
            "Arts, Culture & Digital Storytelling",
            "Working Lands & Industry",
            "Community Tech & Governance",
            "Indigenous Lands & Innovation",
            "Wild Spaces & Outdoor Life",
            "Cariboo Voices & Local News",
            "Resilient Rural Futures",
        ]
        for theme in expected:
            assert theme in themes, f"Missing theme: {theme}"
            assert "file" in themes[theme]
            assert "description" in themes[theme]


class TestGetAmbientTransition:
    def test_returns_fallback_when_no_file(self):
        sentinel = object()
        result = get_ambient_transition("Wild Spaces & Outdoor Life", fallback_segment=sentinel)
        # No ambient MP3 files exist in the repo, so should return fallback
        assert result is sentinel

    def test_returns_fallback_for_unknown_theme(self):
        sentinel = object()
        result = get_ambient_transition("Nonexistent Theme", fallback_segment=sentinel)
        assert result is sentinel

    def test_returns_fallback_when_config_missing(self):
        sentinel = object()
        with patch("ambient.AMBIENT_CONFIG", Path("/nonexistent/config.json")):
            result = get_ambient_transition("Wild Spaces & Outdoor Life", fallback_segment=sentinel)
        assert result is sentinel
