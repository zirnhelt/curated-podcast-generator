"""Unit tests for gemini_tts.py and TTS provider/credits resolution — no API keys."""

import wave

import pytest

import gemini_tts
from gemini_tts import (
    build_transcript,
    _build_payload,
    _synthesize_chunk,
    generate_gemini_tts_for_section,
    TRANSCRIPT_CHAR_LIMIT,
)
from config_loader import strip_stage_directions, render_credits_text


SEGS = [
    {"speaker": "riley", "text": "Welcome back to the show.", "gap_ms": None},
    {"speaker": "casey", "text": "Sure. Another banner day in Quesnel.", "gap_ms": None},
]


class TestBuildTranscript:
    def test_speaker_labels_use_display_names(self):
        transcript = build_transcript(SEGS)
        assert transcript.startswith("Riley: ")
        assert "\nCasey: " in transcript

    def test_pronunciation_applied(self):
        transcript = build_transcript(SEGS)
        assert "Kwenell" in transcript
        assert "Quesnel" not in transcript

    def test_stage_directions_pass_through(self):
        segs = [{"speaker": "casey", "text": "(wry) Sure it will.", "gap_ms": None}]
        assert "(wry)" in build_transcript(segs)


class TestBuildPayload:
    def test_two_speaker_config(self):
        payload = _build_payload(SEGS)
        cfg = payload["generationConfig"]["speechConfig"]["multiSpeakerVoiceConfig"]
        voices = {
            c["speaker"]: c["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"]
            for c in cfg["speakerVoiceConfigs"]
        }
        assert voices == {"Riley": "Sulafat", "Casey": "Orus"}
        assert payload["generationConfig"]["responseModalities"] == ["AUDIO"]

    def test_single_speaker_uses_plain_voice_config(self):
        payload = _build_payload([SEGS[0]])
        speech = payload["generationConfig"]["speechConfig"]
        assert "multiSpeakerVoiceConfig" not in speech
        assert speech["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Sulafat"

    def test_three_speakers_raises(self):
        segs = SEGS + [{"speaker": "guest", "text": "Hi.", "gap_ms": None}]
        with pytest.raises(ValueError):
            _build_payload(segs)

    def test_style_prompt_prefixed(self):
        prompt = _build_payload(SEGS)["contents"][0]["parts"][0]["text"]
        # Style prompt from config/prompts.json leads, transcript follows
        assert prompt.index("community-radio") < prompt.index("Riley: ")


class TestSynthesizeGuards:
    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="GEMINI_API_KEY"):
            _synthesize_chunk(SEGS)

    def test_runaway_request_raises_before_spending(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setattr(
            gemini_tts.requests, "post",
            lambda *a, **k: pytest.fail("must not reach the network"),
        )
        huge = [{"speaker": "riley", "text": "x" * 50_000, "gap_ms": None}]
        with pytest.raises(RuntimeError, match="refusing to spend"):
            _synthesize_chunk(huge)


class TestSectionGeneration:
    def test_writes_wav_and_chunks_long_sections(self, monkeypatch, tmp_path):
        calls = []

        def fake_synthesize(chunk):
            calls.append(chunk)
            return b"\x00\x00" * 2400, 24_000  # 100 ms of silence

        monkeypatch.setattr(gemini_tts, "_synthesize_chunk", fake_synthesize)

        long_text = "word " * 400  # ~2000 chars per turn
        segments = [
            {"speaker": "riley" if i % 2 == 0 else "casey", "text": long_text, "gap_ms": None}
            for i in range(6)
        ]  # ~12k chars total > TRANSCRIPT_CHAR_LIMIT → multiple chunks
        assert sum(len(s["text"]) for s in segments) > TRANSCRIPT_CHAR_LIMIT

        out = tmp_path / "section.wav"
        generate_gemini_tts_for_section(segments, out)

        assert len(calls) > 1
        assert sum(len(c) for c in calls) == len(segments)
        with wave.open(str(out), "rb") as wav:
            assert wav.getnchannels() == 1
            assert wav.getsampwidth() == 2
            assert wav.getframerate() == 24_000
            assert wav.getnframes() > 0


class TestEvaluateScriptLoader:
    def test_prefers_podcast_data_json(self, tmp_path):
        from evaluate_tts import _find_latest_script
        (tmp_path / "podcast_data_2026-07-01.json").write_text('{"script": "from json"}')
        (tmp_path / "podcast_script_2026-07-16_theme.txt").write_text("from txt")
        assert _find_latest_script(tmp_path) == {"script": "from json"}

    def test_falls_back_to_committed_script_txt(self, tmp_path):
        from evaluate_tts import _find_latest_script
        (tmp_path / "podcast_script_2026-07-15_theme.txt").write_text("older")
        (tmp_path / "podcast_script_2026-07-16_theme.txt").write_text("**RILEY:** hi")
        assert _find_latest_script(tmp_path) == {"script": "**RILEY:** hi"}

    def test_none_when_empty(self, tmp_path):
        from evaluate_tts import _find_latest_script
        assert _find_latest_script(tmp_path) is None


class TestStripStageDirections:
    def test_whitelisted_cue_removed(self):
        result = strip_stage_directions("(wry) Sure it will.")
        assert "(wry)" not in result
        assert "Sure it will." in result

    def test_cue_mid_sentence_removed(self):
        result = strip_stage_directions("Fine. (sighs) Let's hear it.")
        assert "(sighs)" not in result
        assert "Fine." in result and "Let's hear it." in result

    def test_case_insensitive(self):
        assert "(Chuckles)" not in strip_stage_directions("(Chuckles) Right.")

    def test_real_parenthetical_dialog_untouched(self):
        text = "The grant (about forty thousand dollars) closed last week."
        assert strip_stage_directions(text) == text


class TestProviderResolution:
    def _fresh(self, monkeypatch, gemini=False, azure=False, used=None):
        import podcast_generator as pg
        monkeypatch.setattr(pg, "USE_GEMINI_TTS", gemini)
        monkeypatch.setattr(pg, "USE_AZURE_TTS", azure)
        monkeypatch.setattr(pg, "_tts_provider_used", used)
        return pg

    def test_default_is_openai(self, monkeypatch):
        pg = self._fresh(monkeypatch)
        assert pg.get_active_tts_provider() == "openai"
        assert "OpenAI" in pg.get_tts_credit()

    def test_azure_flag(self, monkeypatch):
        pg = self._fresh(monkeypatch, azure=True)
        assert pg.get_active_tts_provider() == "azure"
        assert "Azure" in pg.get_tts_credit()

    def test_gemini_flag_wins_over_azure(self, monkeypatch):
        pg = self._fresh(monkeypatch, gemini=True, azure=True)
        assert pg.get_active_tts_provider() == "gemini"
        assert pg.get_tts_credit() == "Gemini TTS (Sulafat · Orus)"

    def test_rendered_provider_beats_flags(self, monkeypatch):
        # Gemini requested, but the run fell back to OpenAI — credit OpenAI
        pg = self._fresh(monkeypatch, gemini=True, used="openai")
        assert pg.get_active_tts_provider() == "openai"
        assert "OpenAI" in pg.get_tts_credit()

    def test_plain_text_credits_reflect_provider(self, monkeypatch):
        pg = self._fresh(monkeypatch, gemini=True)
        text = render_credits_text(pg.get_tts_credit())
        assert "TTS Voices: Gemini TTS (Sulafat · Orus)" in text
        assert "{tts_credit}" not in text


class TestStageDirectionAddendum:
    def test_disabled_without_gemini(self, monkeypatch):
        import podcast_generator as pg
        monkeypatch.setattr(pg, "USE_GEMINI_TTS", False)
        assert pg._stage_direction_addendum() == ""

    def test_enabled_with_gemini_lists_cues(self, monkeypatch):
        import podcast_generator as pg
        monkeypatch.setattr(pg, "USE_GEMINI_TTS", True)
        addendum = pg._stage_direction_addendum()
        assert "STAGE DIRECTIONS" in addendum
        assert "(wry)" in addendum
        assert "{cue_list}" not in addendum
