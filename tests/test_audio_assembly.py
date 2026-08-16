"""Tests for the music→speech overlap primitive (_append_with_gap).

Uses a fake AudioSegment that mimics pydub's length semantics — in
particular, overlay() never extends the base segment — so the tests
verify the canvas-extension math that makes negative gaps (overlaps)
produce the right episode length.
"""

import json

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

    # Peak level of a normal take; the silent-take guard reads this off every
    # whole-section render, so the default has to be audible.
    max_dBFS = -3.0

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

    def _setup(self, monkeypatch, tmp_path, canary_model="gemini-2.5-flash-preview-tts"):
        pg = podcast_generator
        monkeypatch.setattr(pg, "AudioSegment", RichFakeSegment)
        monkeypatch.setattr(pg, "USE_GEMINI_TTS", True)
        # The pre-flight canary decides the provider before any section renders.
        # Default: it passes, so these tests exercise the *per-section* fallback
        # that still has to work when Gemini dies mid-episode.
        monkeypatch.setattr(pg, "gemini_canary", lambda: canary_model)
        monkeypatch.setattr(pg, "gemini_set_render_deadline", lambda s: None)
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

    def test_failed_canary_routes_the_whole_episode_to_openai(self, monkeypatch, tmp_path, capsys):
        """The mixed-voice episode this prevents: three of seven episodes in the
        week of 2026-08-01 shipped a Gemini cold open and an OpenAI show, because
        the provider fallback fires per-section mid-render. A canary that cannot
        get a note out of Gemini pins OpenAI before a single section exists."""
        pg = podcast_generator
        openai_calls, tts_only_calls = self._setup(monkeypatch, tmp_path, canary_model=None)
        gemini_calls = []
        monkeypatch.setattr(pg, "generate_gemini_tts_for_section",
                            lambda *a, **k: gemini_calls.append(True))
        out = str(tmp_path / "episode.mp3")

        assert pg.generate_audio_from_script("script", out, theme_name="Test Theme") == out

        assert gemini_calls == [], "no section may reach Gemini after a failed canary"
        assert openai_calls, "the whole episode should render on OpenAI"
        assert pg._tts_provider_used == "openai"
        assert tts_only_calls == [], "music and credits must survive the canary decision"
        assert "Degraded 'render/gemini-canary'" in capsys.readouterr().out

    def test_passing_canary_lets_sections_reach_gemini(self, monkeypatch, tmp_path):
        """The canary must not become a second way to lose Gemini."""
        pg = podcast_generator
        self._setup(monkeypatch, tmp_path)
        gemini_calls = []

        def _fake_gemini(seg_list, output_file, context_tail=""):
            gemini_calls.append(len(seg_list))
            with open(output_file, "wb") as f:
                f.write(b"\x00")
            return "tail"

        monkeypatch.setattr(pg, "generate_gemini_tts_for_section", _fake_gemini)
        out = str(tmp_path / "episode.mp3")

        assert pg.generate_audio_from_script("script", out, theme_name="Test Theme") == out
        assert gemini_calls, "sections should render on Gemini when the canary passes"
        assert pg.get_active_tts_provider() == "gemini"

    def test_silent_gemini_section_falls_back_to_openai(self, monkeypatch, tmp_path, capsys):
        """A whole-section render that succeeds and contains no speech is the
        same failure as a silent per-turn take, minutes wide. It must route to
        the OpenAI re-render rather than appending dead air."""
        pg = podcast_generator
        openai_calls, tts_only_calls = self._setup(monkeypatch, tmp_path)

        class SilentSegment(RichFakeSegment):
            max_dBFS = -90.0

        def _silent_gemini(seg_list, output_file, context_tail=""):
            with open(output_file, "wb") as f:
                f.write(b"\x00")
            return "tail"

        monkeypatch.setattr(pg, "generate_gemini_tts_for_section", _silent_gemini)
        monkeypatch.setattr(RichFakeSegment, "from_file",
                            staticmethod(lambda *a, **k: SilentSegment(5000)))
        out = str(tmp_path / "episode.mp3")

        assert pg.generate_audio_from_script("script", out, theme_name="Test Theme") == out
        assert openai_calls, "the silent section must be re-rendered on OpenAI"
        assert pg._tts_provider_used == "openai"
        assert tts_only_calls == [], "music and credits must survive the fallback"
        assert "degrading to OpenAI" in capsys.readouterr().out


def _openai_only_setup(monkeypatch, tmp_path, silent_texts=()):
    """Patch generate_audio_from_script down to the OpenAI per-turn path.

    Returns the list of texts that actually reached TTS — including the spoken
    credits, which render through the same call.
    """
    pg = podcast_generator
    monkeypatch.setattr(pg, "AudioSegment", RichFakeSegment)
    monkeypatch.setattr(pg, "USE_GEMINI_TTS", False)
    monkeypatch.setattr(pg, "USE_AZURE_TTS", False)
    monkeypatch.setattr(pg, "USE_AZURE_PARALLEL", False)
    monkeypatch.setattr(pg, "_tts_provider_used", None)
    monkeypatch.setattr(pg, "get_openai_client", lambda: object())
    monkeypatch.setattr(pg, "normalize_segment", lambda seg, *a, **k: seg)
    monkeypatch.setattr(pg, "trim_tts_silence", lambda seg, *a, **k: seg)
    monkeypatch.setattr(pg, "heuristic_gap_ms", lambda *a, **k: 300)
    monkeypatch.setattr(pg, "get_ambient_transition", lambda *a, **k: RichFakeSegment(1000))
    monkeypatch.setattr(pg, "_log_api_call", lambda *a, **k: None)
    monkeypatch.setattr(pg, "_split_at_sentences", lambda text, **k: [text])
    for attr in ("INTRO_MUSIC", "INTERVAL_MUSIC", "OUTRO_MUSIC"):
        monkeypatch.setattr(pg, attr, _FakeMusicPath(attr.lower()))
    monkeypatch.setattr(pg, "derive_episode_sidecar_path",
                        lambda audio, prefix: str(tmp_path / f"{prefix}.json"))
    monkeypatch.setattr(pg, "parse_script_into_segments", lambda script: {
        "preamble": [],
        "welcome": _turns("Welcome to the show everyone."),
        "news": _turns("First headline of the day."),
        "meta_moment": [],
        "community_spotlight": [],
        "deep_dive": _turns("Opening the debate here.", "The dead turn.", "Closing it out."),
    })

    rendered = []

    def _fake_openai_segment(text, speaker, output_file):
        if text in silent_texts:
            raise pg.SilentTakeError(f"two consecutive silent takes for {speaker}")
        rendered.append(text)
        with open(output_file, "wb") as f:
            f.write(b"\x00")

    monkeypatch.setattr(pg, "generate_tts_for_segment", _fake_openai_segment)
    return rendered


class TestSpokenCreditsNameTheirSources:
    """The spoken credits name only what the episode actually used. Brave and
    the weather sweep are both optional and both skipped on failure, so each is
    gated rather than always read."""

    def _credits(self, monkeypatch, tmp_path, **kwargs):
        rendered = _openai_only_setup(monkeypatch, tmp_path)
        podcast_generator.generate_audio_from_script(
            "script", str(tmp_path / "episode.mp3"), theme_name="Test Theme", **kwargs
        )
        return next(t for t in rendered if "is produced by" in t)

    def test_weather_source_is_named_when_the_sweep_ran(self, monkeypatch, tmp_path):
        text = self._credits(monkeypatch, tmp_path, weather_used=True)
        assert "Open-Meteo" in text

    def test_weather_source_is_omitted_when_the_sweep_did_not_run(self, monkeypatch, tmp_path):
        text = self._credits(monkeypatch, tmp_path, weather_used=False)
        assert "Open-Meteo" not in text
        assert "Regional weather" not in text

    def test_weather_and_brave_credits_are_independent(self, monkeypatch, tmp_path):
        text = self._credits(monkeypatch, tmp_path, weather_used=True, brave_used=False)
        assert "Open-Meteo" in text
        assert "Brave Search" not in text

    def test_weather_source_comes_from_config(self, monkeypatch, tmp_path):
        monkeypatch.setitem(podcast_generator.CONFIG['credits']['structured'],
                            'weather_data', 'Some Other Service')
        text = self._credits(monkeypatch, tmp_path, weather_used=True)
        assert "Some Other Service" in text


class TestSilentTakeIsDroppedNotShipped:
    """Regression for 2026-08-16: OpenAI returned a full-length, all-zero take
    for one deep-dive turn. trim_tts_silence passes an entirely silent clip
    through at full length by design and the duration check saw a correct
    length, so 27s of dead air went out mid-debate. A turn that cannot be
    rendered is cut — keeping it ships silence of exactly the same length."""

    def _setup(self, monkeypatch, tmp_path, silent_texts):
        return _openai_only_setup(monkeypatch, tmp_path, silent_texts)

    def _timeline(self, tmp_path):
        return json.loads((tmp_path / "video_timeline.json").read_text())["turns"]

    def test_silent_turn_is_cut_and_the_rest_of_the_episode_survives(
        self, monkeypatch, tmp_path, capsys
    ):
        pg = podcast_generator
        rendered = self._setup(monkeypatch, tmp_path, {"The dead turn."})
        out = str(tmp_path / "episode.mp3")

        assert pg.generate_audio_from_script("script", out, theme_name="Test Theme") == out

        assert "The dead turn." not in rendered
        assert "Opening the debate here." in rendered
        assert "Closing it out." in rendered
        deep = [t for t in self._timeline(tmp_path) if t["section"] == "deep"]
        assert len(deep) == 2, "the silent turn must not occupy a slot in the timeline"
        assert "Dropping silent chunk" in capsys.readouterr().out

    def test_dropped_turn_is_recorded_as_a_degradation(self, monkeypatch, tmp_path):
        pg = podcast_generator
        monkeypatch.setattr(pg, "_RUN_SEGMENTS", [])
        self._setup(monkeypatch, tmp_path, {"The dead turn."})

        pg.generate_audio_from_script("script", str(tmp_path / "episode.mp3"),
                                      theme_name="Test Theme")

        rows = [r for r in pg._RUN_SEGMENTS if r["name"] == "render/silent-take"]
        assert len(rows) == 1, "a silent take must reach the run report, and only once"
        assert rows[0]["status"] == "degraded"

    def test_silent_opening_turn_hands_the_music_overlap_to_the_next_one(
        self, monkeypatch, tmp_path
    ):
        # Dropping the turn that would have started the section must not leave
        # the intro music fading out into a gap.
        pg = podcast_generator
        self._setup(monkeypatch, tmp_path, {"Opening the debate here."})
        out = str(tmp_path / "episode.mp3")

        assert pg.generate_audio_from_script("script", out, theme_name="Test Theme") == out

        deep = [t for t in self._timeline(tmp_path) if t["section"] == "deep"]
        assert len(deep) == 2
        chapters = json.loads((tmp_path / "podcast_chapters.json").read_text())["chapters"]
        deep_start_ms = next(c for c in chapters if c["title"] == "Deep Dive")["startTime"] * 1000
        # The surviving first turn starts at the chapter mark (inside the music
        # fade), not a full heuristic gap after the music ended.
        assert deep[0]["start_ms"] == pytest.approx(deep_start_ms, abs=100)


class TestTtsOnlyEmitsSidecars:
    """Regression: the bare TTS-only fallback must still write chapters + timeline
    sidecars with real section boundaries, else the video renderer collapses the
    whole episode into one 'Introduction' chapter and parks the weather slide at
    the mid-point (the ~10-min bug)."""

    def test_per_segment_path_writes_chapters_and_timeline(self, monkeypatch, tmp_path):
        pg = podcast_generator
        monkeypatch.setattr(pg, "AudioSegment", RichFakeSegment)
        monkeypatch.setattr(pg, "normalize_segment", lambda seg, *a, **k: seg)
        monkeypatch.setattr(pg, "trim_tts_silence", lambda seg, *a, **k: seg)
        monkeypatch.setattr(pg, "heuristic_gap_ms", lambda *a, **k: 0)
        monkeypatch.setattr(pg, "get_openai_client", lambda: object())
        monkeypatch.setattr(pg, "OUTRO_MUSIC", _FakeMusicPath("outro"))
        monkeypatch.setattr(pg, "derive_episode_sidecar_path",
                            lambda audio, prefix: str(tmp_path / f"{prefix}.json"))
        monkeypatch.setattr(pg, "parse_script_into_segments", lambda script: {
            "preamble": [],
            "welcome": _turns("Welcome to the show everyone.", "Great to be here today."),
            "news": _turns("First headline of the day.", "An interesting development indeed."),
            "meta_moment": [],
            "community_spotlight": [],
            "deep_dive": _turns("Let's dig into the main topic.", "Plenty to unpack here."),
        })
        monkeypatch.setattr(pg, "generate_tts_for_segment",
                            lambda text, speaker, out: open(out, "wb").write(b"\x00"))

        out = str(tmp_path / "episode.mp3")
        result = pg.generate_audio_tts_only("script", out, _force_openai=True)
        assert result == out

        chapters = json.load(open(tmp_path / "podcast_chapters.json"))["chapters"]
        assert [c["title"] for c in chapters] == ["Introduction", "News Roundup", "Deep Dive"]
        starts = [c["startTime"] for c in chapters]
        # Real, monotonically advancing boundaries — not one whole-episode chapter
        assert starts[0] == 0
        assert starts == sorted(starts)
        assert starts[1] > 0 and starts[2] > starts[1]

        turns = json.load(open(tmp_path / "video_timeline.json"))["turns"]
        assert len(turns) == 6
        assert {t["speaker"] for t in turns} == {"riley", "casey"}
        assert [t["section"] for t in turns[:2]] == ["Introduction", "Introduction"]

    def test_meta_moment_included_in_fallback_sidecars(self, monkeypatch, tmp_path):
        # Regression: raw_sections used to omit meta_moment, silently dropping
        # that speech from TTS-only episodes.
        pg = podcast_generator
        monkeypatch.setattr(pg, "AudioSegment", RichFakeSegment)
        monkeypatch.setattr(pg, "normalize_segment", lambda seg, *a, **k: seg)
        monkeypatch.setattr(pg, "trim_tts_silence", lambda seg, *a, **k: seg)
        monkeypatch.setattr(pg, "heuristic_gap_ms", lambda *a, **k: 0)
        monkeypatch.setattr(pg, "get_openai_client", lambda: object())
        monkeypatch.setattr(pg, "OUTRO_MUSIC", _FakeMusicPath("outro"))
        monkeypatch.setattr(pg, "derive_episode_sidecar_path",
                            lambda audio, prefix: str(tmp_path / f"{prefix}.json"))
        monkeypatch.setattr(pg, "parse_script_into_segments", lambda script: {
            "preamble": [],
            "welcome": _turns("Welcome to the show everyone.", "Great to be here today."),
            "news": _turns("First headline of the day.", "An interesting development indeed."),
            "meta_moment": _turns("A word about how this show is made.", "Full details in the notes."),
            "community_spotlight": [],
            "deep_dive": _turns("Let's dig into the main topic.", "Plenty to unpack here."),
        })
        monkeypatch.setattr(pg, "generate_tts_for_segment",
                            lambda text, speaker, out: open(out, "wb").write(b"\x00"))

        out = str(tmp_path / "episode.mp3")
        assert pg.generate_audio_tts_only("script", out, _force_openai=True) == out

        chapters = json.load(open(tmp_path / "podcast_chapters.json"))["chapters"]
        assert [c["title"] for c in chapters] == [
            "Introduction", "News Roundup", "Meta Moment", "Deep Dive"]
        starts = [c["startTime"] for c in chapters]
        assert starts == sorted(starts) and starts[2] > starts[1]

        turns = json.load(open(tmp_path / "video_timeline.json"))["turns"]
        meta_idx = [i for i, t in enumerate(turns) if t["section"] == "Meta Moment"]
        assert len(meta_idx) == 2
        # Meta Moment turns sit between the news and deep-dive turns
        assert turns[meta_idx[0] - 1]["section"] == "News Roundup"
        assert turns[meta_idx[-1] + 1]["section"] == "Deep Dive"
