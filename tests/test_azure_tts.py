"""Unit tests for azure_tts.py — pure-function tests that require no API keys."""

import xml.etree.ElementTree as ET
import pytest

from azure_tts import (
    apply_pronunciation,
    build_section_ssml,
    pacing_tag_to_ssml,
    PRONUNCIATION_DICT,
    AZURE_VOICE_MAP,
)


class TestApplyPronunciation:
    def test_quesnel_substituted(self):
        result = apply_pronunciation("We're broadcasting from Quesnel today.")
        assert '<phoneme alphabet="ipa" ph="kwɛˈnɛl">Quesnel</phoneme>' in result

    def test_plain_text_xml_escaped(self):
        result = apply_pronunciation("A & B < C > D")
        assert "&amp;" in result
        assert "&lt;" in result
        assert "&gt;" in result
        assert "<phoneme" not in result  # no place names, no phoneme tags

    def test_ampersand_before_phoneme_injection(self):
        # Ampersand in text must be escaped even when a place name also appears
        result = apply_pronunciation("Quesnel & area news")
        assert '<phoneme alphabet="ipa" ph="kwɛˈnɛl">Quesnel</phoneme>' in result
        assert "&amp;" in result

    def test_no_match_unchanged_except_escaping(self):
        original = "Hello there, Williams Lake."
        result = apply_pronunciation(original)
        # No place names from IPA_DICT — no <phoneme> tags
        assert "<phoneme" not in result

    def test_100_mile_house(self):
        result = apply_pronunciation("Meeting in 100 Mile House tonight.")
        assert '<phoneme alphabet="ipa" ph="wʌn ˈhʌndrəd maɪl haʊs">100 Mile House</phoneme>' in result

    def test_cariboo_substituted(self):
        result = apply_pronunciation("Welcome to Cariboo Signals.")
        assert '<phoneme alphabet="ipa" ph="ˈkærɪbuː">Cariboo</phoneme>' in result

    def test_multiple_places_in_one_string(self):
        result = apply_pronunciation("Quesnel and Lac la Hache weather update.")
        assert "Quesnel" in result
        assert "Lac la Hache" in result
        assert result.count("<phoneme") == 2


class TestPacingTagToSsml:
    def test_none_returns_empty(self):
        assert pacing_tag_to_ssml(None) == ""

    def test_positive_returns_break(self):
        assert pacing_tag_to_ssml(400) == '<break time="400ms"/>'

    def test_zero_returns_empty(self):
        assert pacing_tag_to_ssml(0) == ""

    def test_negative_overlap_returns_empty(self):
        assert pacing_tag_to_ssml(-150) == ""

    def test_large_pause(self):
        assert pacing_tag_to_ssml(1200) == '<break time="1200ms"/>'


class TestBuildSectionSsml:
    def _two_turn_segments(self):
        return [
            {"speaker": "riley", "text": "Welcome to Cariboo Signals.", "gap_ms": None},
            {"speaker": "casey", "text": "Thanks for joining us.", "gap_ms": None},
        ]

    def test_output_is_valid_xml(self):
        ssml = build_section_ssml(self._two_turn_segments())
        # Should not raise
        root = ET.fromstring(ssml)
        assert root.tag.endswith("speak")

    def test_contains_both_voice_elements(self):
        ssml = build_section_ssml(self._two_turn_segments())
        assert "en-US-Ava:DragonHDLatestNeural" in ssml
        assert "en-US-Andrew:DragonHDLatestNeural" in ssml

    def test_contains_mstts_namespace(self):
        ssml = build_section_ssml(self._two_turn_segments())
        assert 'xmlns:mstts="http://www.w3.org/2001/mstts"' in ssml

    def test_explicit_pause_tag_becomes_break(self):
        segments = [
            {"speaker": "riley", "text": "Good morning.", "gap_ms": None},
            {"speaker": "casey", "text": "Good morning to you.", "gap_ms": 500},
        ]
        ssml = build_section_ssml(segments)
        assert '<break time="500ms"/>' in ssml

    def test_no_break_when_gap_is_none(self):
        ssml = build_section_ssml(self._two_turn_segments())
        assert "<break" not in ssml

    def test_no_break_for_first_segment(self):
        segments = [
            {"speaker": "riley", "text": "First line.", "gap_ms": 300},
        ]
        ssml = build_section_ssml(segments)
        # gap_ms on the first segment should not produce a break (no preceding turn)
        assert "<break" not in ssml

    def test_overlap_tag_dropped(self):
        segments = [
            {"speaker": "riley", "text": "Point A.", "gap_ms": None},
            {"speaker": "casey", "text": "Exactly!", "gap_ms": -150},
        ]
        ssml = build_section_ssml(segments)
        assert "<break" not in ssml

    def test_pronunciation_applied_inside_ssml(self):
        segments = [
            {"speaker": "riley", "text": "Coming from Quesnel today.", "gap_ms": None},
        ]
        ssml = build_section_ssml(segments)
        assert '<phoneme alphabet="ipa" ph="kwɛˈnɛl">Quesnel</phoneme>' in ssml

    def test_custom_voice_map(self):
        custom_map = {
            "riley": "en-US-Custom-Voice",
            "casey": "en-US-Other-Voice",
        }
        ssml = build_section_ssml(self._two_turn_segments(), voice_map=custom_map)
        assert "en-US-Custom-Voice" in ssml
        assert "en-US-Other-Voice" in ssml
