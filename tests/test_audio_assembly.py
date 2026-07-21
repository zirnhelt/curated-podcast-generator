"""Tests for the music→speech overlap primitive (_append_with_gap).

Uses a fake AudioSegment that mimics pydub's length semantics — in
particular, overlay() never extends the base segment — so the tests
verify the canvas-extension math that makes negative gaps (overlaps)
produce the right episode length.
"""

import pytest

import generate_bespoke
import podcast_generator


class FakeSegment:
    """Length-only stand-in for pydub.AudioSegment."""

    def __init__(self, length=0):
        self.length = length

    def __len__(self):
        return self.length

    def __add__(self, other):
        return FakeSegment(self.length + len(other))

    def overlay(self, other, position=0):
        # pydub semantics: overlay never extends the base segment
        return FakeSegment(self.length)

    @staticmethod
    def silent(duration=0):
        return FakeSegment(duration)


@pytest.fixture(params=[podcast_generator, generate_bespoke], ids=["daily", "bespoke"])
def append_with_gap(request, monkeypatch):
    monkeypatch.setattr(request.param, "AudioSegment", FakeSegment)
    return request.param._append_with_gap


class TestAppendWithGap:
    def test_positive_gap_inserts_silence(self, append_with_gap):
        combined = append_with_gap(FakeSegment(1000), FakeSegment(300), 400)
        assert len(combined) == 1700

    def test_zero_gap_butt_joins(self, append_with_gap):
        combined = append_with_gap(FakeSegment(1000), FakeSegment(300), 0)
        assert len(combined) == 1300

    def test_negative_gap_overlaps_music_tail(self, append_with_gap):
        # Speech starts 500ms before the music ends; nothing is truncated.
        combined = append_with_gap(FakeSegment(2000), FakeSegment(3000), -500)
        assert len(combined) == 2000 - 500 + 3000

    def test_negative_gap_shorter_speech_keeps_tail(self, append_with_gap):
        # Speech fits entirely within the overlap window — length unchanged.
        combined = append_with_gap(FakeSegment(2000), FakeSegment(300), -500)
        assert len(combined) == 2000

    def test_negative_gap_clamps_to_start(self, append_with_gap):
        # Overlap larger than the existing audio starts at position 0.
        combined = append_with_gap(FakeSegment(200), FakeSegment(3000), -500)
        assert len(combined) == 3000


def test_overlap_constants_match():
    assert podcast_generator.MUSIC_SPEECH_OVERLAP_MS == generate_bespoke.MUSIC_SPEECH_OVERLAP_MS
    # Interval chime fade window covers the whole speech overlap
    assert podcast_generator.INTERVAL_FADE_OUT_MS >= podcast_generator.MUSIC_SPEECH_OVERLAP_MS


class RichFakeSegment(FakeSegment):
    """FakeSegment extended with the methods generate_audio_from_script uses."""

    def __getitem__(self, key):
        if isinstance(key, slice):
            start, stop, _ = key.indices(self.length)
            return RichFakeSegment(max(stop - start, 0))
        return RichFakeSegment(1)

    def fade_out(self, ms=0):
        return self

    def fade_in(self, ms=0):
        return self

    def overlay(self, other, position=0):
        return RichFakeSegment(self.length)

    def __add__(self, other):
        return RichFakeSegment(self.length + len(other))

    def export(self, path, format=None):
        with open(path, "wb") as f:
            f.write(b"\x00" * max(self.length, 1))
        return path

    @staticmethod
    def empty():
        return RichFakeSegment(0)

    @staticmethod
    def silent(duration=0):
        return RichFakeSegment(duration)

    @staticmethod
    def from_mp3(*a, **k):
        return RichFakeSegment(5000)

    @staticmethod
    def from_file(*a, **k):
        return RichFakeSegment(5000)


class _FakeMusicPath:
    """Stand-in for the INTRO/INTERVAL/OUTRO Path constants."""

    def __init__(self, name):
        self._name = name

    def exists(self):
        return True

    def stat(self):
        return type("St", (), {"st_size": 12345})()

    def __str__(self):
        return self._name

    def __fspath__(self):
        return self._name


def _turns(*texts):
    return [{"speaker": ("riley" if i % 2 == 0 else "casey"),
             "text": t, "gap_ms": None} for i, t in enumerate(texts)]


class TestGeminiFailoverKeepsMusicAndCredits:
    """Regression: a Gemini section failure must degrade to OpenAI in place —
    keeping intro music, interstitials, and spoken credits — instead of falling
    back to the bare TTS-only path (which dropped all three)."""

    def _setup(self, monkeypatch, tmp_path):
        pg = podcast_generator
        monkeypatch.setattr(pg, "AudioSegment", RichFakeSegment)
        monkeypatch.setattr(pg, "USE_GEMINI_TTS", True)
        monkeypatch.setattr(pg, "USE_AZURE_PARALLEL", False)
        monkeypatch.setattr(pg, "_tts_provider_used", None)
        monkeypatch.setattr(pg, "get_gemini_api_key", lambda: "key")
        monkeypatch.setattr(pg, "get_openai_client", lambda: object())
        monkeypatch.setattr(pg, "normalize_segment", lambda seg, *a, **k: seg)
        monkeypatch.setattr(pg, "trim_tts_silence", lambda seg, *a, **k: seg)
        monkeypatch.setattr(pg, "get_ambient_transition", lambda *a, **k: RichFakeSegment(1000))
        monkeypatch.setattr(pg, "_log_api_call", lambda *a, **k: None)
        for attr in ("INTRO_MUSIC", "INTERVAL_MUSIC", "OUTRO_MUSIC"):
            monkeypatch.setattr(pg, attr, _FakeMusicPath(attr.lower()))
        monkeypatch.setattr(pg, "derive_episode_sidecar_path",
                            lambda audio, prefix: str(tmp_path / f"{prefix}.json"))
        monkeypatch.setattr(pg, "parse_script_into_segments", lambda script: {
            "preamble": [],
            "welcome": _turns("Welcome to the show everyone.", "Great to be here today."),
            "news": _turns("First headline of the day.", "Interesting development indeed."),
            "meta_moment": [],
            "community_spotlight": [],
            "deep_dive": _turns("Let's dig into the main topic.", "Plenty to unpack here."),
        })
        # Gemini always fails; OpenAI per-segment records calls
        monkeypatch.setattr(pg, "generate_gemini_tts_for_section",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gemini down")))
        openai_calls = []

        def _fake_openai_segment(text, speaker, output_file):
            openai_calls.append((speaker, text))
            with open(output_file, "wb") as f:
                f.write(b"\x00")

        monkeypatch.setattr(pg, "generate_tts_for_segment", _fake_openai_segment)

        # generate_audio_tts_only must NOT be reached in this scenario
        tts_only_calls = []
        real_tts_only = pg.generate_audio_tts_only

        def _spy_tts_only(*a, **k):
            tts_only_calls.append(True)
            return real_tts_only(*a, **k)

        monkeypatch.setattr(pg, "generate_audio_tts_only", _spy_tts_only)
        return openai_calls, tts_only_calls

    def test_degrades_in_place_keeping_structure(self, monkeypatch, tmp_path, capsys):
        pg = podcast_generator
        openai_calls, tts_only_calls = self._setup(monkeypatch, tmp_path)
        out = str(tmp_path / "episode.mp3")

        result = pg.generate_audio_from_script("script", out, theme_name="Test Theme")

        # Episode still produced, via the music path — not the TTS-only fallback
        assert result == out
        assert tts_only_calls == [], "must not fall back to bare TTS-only path"
        # Degraded to OpenAI voices, and credited as OpenAI (spoken + written)
        assert pg._tts_provider_used == "openai"
        assert pg.get_active_tts_provider() == "openai"
        assert openai_calls, "OpenAI per-segment path should have rendered the sections"
        # Music/credits assembly ran to completion
        logs = capsys.readouterr().out
        assert "degrading to OpenAI" in logs
        assert "Added spoken credits" in logs
