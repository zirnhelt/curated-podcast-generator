"""Tests for config_loader module."""

import pytest
from config_loader import (
    load_podcast_config,
    load_hosts_config,
    load_themes_config,
    load_credits_config,
    load_interests,
    load_prompts_config,
    get_voice_for_host,
    get_theme_for_day,
    get_all_config,
)


class TestConfigLoader:
    def test_load_podcast_config(self):
        config = load_podcast_config()
        assert config["title"] == "Cariboo Signals"
        assert "url" in config
        assert "language" in config

    def test_load_hosts_config(self):
        hosts = load_hosts_config()
        assert "riley" in hosts
        assert "casey" in hosts
        assert hosts["riley"]["voice"] == "nova"
        assert hosts["casey"]["voice"] == "echo"

    def test_load_themes_config(self):
        themes = load_themes_config()
        assert len(themes) == 7
        for day in range(7):
            assert str(day) in themes
            assert "name" in themes[str(day)]

    def test_load_credits_config(self):
        credits = load_credits_config()
        assert "text" in credits
        assert "structured" in credits

    def test_load_interests(self):
        interests = load_interests()
        assert isinstance(interests, str)
        assert len(interests) > 0

    def test_load_prompts_config(self):
        prompts = load_prompts_config()
        assert "script_generation" in prompts
        assert "script_polish" in prompts
        assert "template" in prompts["script_generation"]

    def test_get_voice_for_host(self):
        assert get_voice_for_host("riley") == "nova"
        assert get_voice_for_host("casey") == "echo"

    def test_get_theme_for_day(self):
        theme = get_theme_for_day(0)
        assert isinstance(theme, str)
        assert len(theme) > 0

    def test_get_all_config(self):
        config = get_all_config()
        assert set(config.keys()) == {"podcast", "hosts", "themes", "credits", "interests", "prompts"}

    def test_configs_are_cached(self):
        """Verify lru_cache returns the same object on repeated calls."""
        a = load_podcast_config()
        b = load_podcast_config()
        assert a is b
