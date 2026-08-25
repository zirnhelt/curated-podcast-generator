"""Tests for the two per-take checksums in generate_tts_for_segment.

Duration: a truncated take (OpenAI silently dropping words) used to only
print a warning — the published transcript is built from the segment's
*real* measured duration, so a caption window still gets sized to whatever
audio actually rendered, but the text shown is the full (untruncated)
script line. The listener hears a shorter clip than the caption implies
actually got spoken. One retry usually recovers the full-length take.

Amplitude: a take can also come back the right length and completely
silent, which the duration check by construction cannot see. 2026-08-16
shipped 27s of digital silence in the middle of the deep dive that way.
"""

import io

import pytest

import podcast_generator as pg

AUDIBLE_DBFS = -3.0   # what a real tts-1 take peaks at
SILENT_DBFS = -90.0   # what digital silence reads as


class FakeAudio:
    """Length+peak stand-in for an mp3 AudioSegment, keyed off the content bytes."""

    def __init__(self, duration_ms, max_dBFS=AUDIBLE_DBFS):
        self.duration_ms = duration_ms
        self.max_dBFS = max_dBFS

    def __len__(self):
        return self.duration_ms


def _content(duration_ms: int, peak_dbfs: float = AUDIBLE_DBFS) -> bytes:
    return f"{duration_ms}|{peak_dbfs}".encode()


def _decode(raw: bytes) -> FakeAudio:
    duration, _, peak = raw.decode().partition("|")
    return FakeAudio(int(duration), float(peak))


def _duration_of(path) -> int:
    return _decode(path.read_bytes()).duration_ms


class FakeAudioSegmentCls:
    """Decodes duration+peak from the fake TTS response bytes on disk / passed in."""

    @staticmethod
    def from_mp3(path):
        with open(path, "rb") as f:
            return _decode(f.read())

    @staticmethod
    def from_file(fileobj, format=None):
        return _decode(fileobj.read())


class FakeResponse:
    def __init__(self, content):
        self.content = content


class FakeSpeech:
    def __init__(self, takes):
        # Each take is either a duration, or a (duration, peak_dbfs) pair.
        self._takes = [t if isinstance(t, tuple) else (t, AUDIBLE_DBFS) for t in takes]
        self.calls = 0
        self.last_kwargs = {}

    def create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        duration_ms, peak = self._takes[min(self.calls, len(self._takes)) - 1]
        return FakeResponse(_content(duration_ms, peak))


class FakeAudioNamespace:
    def __init__(self, takes):
        self.speech = FakeSpeech(takes)


class FakeClient:
    def __init__(self, takes):
        self.audio = FakeAudioNamespace(takes)


TEXT = " ".join(["word"] * 20)  # 20 words, well above the 10-word floor


@pytest.fixture(autouse=True)
def _patch_tts_deps(monkeypatch):
    monkeypatch.setattr(pg, "AudioSegment", FakeAudioSegmentCls)
    monkeypatch.setattr(pg, "get_voice_for_host", lambda host: "nova")
    monkeypatch.setattr(pg, "get_speed_for_host", lambda host: 1.0)


class TestTTSDurationRetry:
    def test_full_length_take_does_not_retry(self, monkeypatch, tmp_path):
        # Comfortably above _expected_speech_ms for 20 words; no retry.
        client = FakeClient([8000])
        monkeypatch.setattr(pg, "get_openai_client", lambda: client)

        out = tmp_path / "seg.mp3"
        pg.generate_tts_for_segment(TEXT, "riley", str(out))

        assert client.audio.speech.calls == 1
        assert _duration_of(out) == 8000

    def test_short_take_retries_and_keeps_longer_result(self, monkeypatch, tmp_path, capsys):
        # First take is 50% of expected (well under the 0.80 threshold);
        # retry comes back full-length and should be what's on disk after.
        client = FakeClient([4000, 8000])
        monkeypatch.setattr(pg, "get_openai_client", lambda: client)

        out = tmp_path / "seg.mp3"
        pg.generate_tts_for_segment(TEXT, "riley", str(out))

        assert client.audio.speech.calls == 2
        assert _duration_of(out) == 8000
        err = capsys.readouterr().out
        assert "possible word omission, retrying once" in err
        assert "Retry didn't recover" not in err

    def test_retry_still_short_keeps_longer_of_the_two_and_warns(self, monkeypatch, tmp_path, capsys):
        # Neither take clears the 0.80 threshold; keep whichever is longer
        # (the retry, at 4500ms) and warn that the retry didn't fully recover.
        client = FakeClient([4000, 4500])
        monkeypatch.setattr(pg, "get_openai_client", lambda: client)

        out = tmp_path / "seg.mp3"
        pg.generate_tts_for_segment(TEXT, "riley", str(out))

        assert client.audio.speech.calls == 2
        assert _duration_of(out) == 4500
        out_text = capsys.readouterr().out
        assert "Retry didn't recover the missing words either" in out_text

    def test_retry_worse_than_original_keeps_original(self, monkeypatch, tmp_path):
        # Retry regresses (3000ms < the original 4000ms) — the original,
        # still-short take should be kept rather than the worse retry.
        client = FakeClient([4000, 3000])
        monkeypatch.setattr(pg, "get_openai_client", lambda: client)

        out = tmp_path / "seg.mp3"
        pg.generate_tts_for_segment(TEXT, "riley", str(out))

        assert client.audio.speech.calls == 2
        assert _duration_of(out) == 4000

    def test_speed_multiplier_scales_expected_duration(self, monkeypatch, tmp_path):
        # speed=1.1 means ~10% shorter audio is expected and should not
        # itself trip the "possible word omission" retry.
        monkeypatch.setattr(pg, "get_speed_for_host", lambda host: 1.1)
        client = FakeClient([int(8000 / 1.1)])
        monkeypatch.setattr(pg, "get_openai_client", lambda: client)

        out = tmp_path / "seg.mp3"
        pg.generate_tts_for_segment(TEXT, "casey", str(out))

        assert client.audio.speech.calls == 1


class TestTTSSilentTake:
    def test_audible_take_does_not_retry(self, monkeypatch, tmp_path):
        client = FakeClient([(8000, AUDIBLE_DBFS)])
        monkeypatch.setattr(pg, "get_openai_client", lambda: client)

        out = tmp_path / "seg.mp3"
        pg.generate_tts_for_segment(TEXT, "riley", str(out))

        assert client.audio.speech.calls == 1

    def test_silent_take_retries_and_keeps_audible_result(self, monkeypatch, tmp_path, capsys):
        # The silent take is the *right length* for its text, so the duration
        # ratio is 1.0 — only the peak level can catch it.
        client = FakeClient([(8000, SILENT_DBFS), (8000, AUDIBLE_DBFS)])
        monkeypatch.setattr(pg, "get_openai_client", lambda: client)

        out = tmp_path / "seg.mp3"
        pg.generate_tts_for_segment(TEXT, "riley", str(out))

        assert client.audio.speech.calls == 2
        assert FakeAudioSegmentCls.from_mp3(str(out)).max_dBFS == AUDIBLE_DBFS
        assert "of silence for riley" in capsys.readouterr().out

    def test_two_silent_takes_raise(self, monkeypatch, tmp_path):
        client = FakeClient([(8000, SILENT_DBFS), (8000, SILENT_DBFS)])
        monkeypatch.setattr(pg, "get_openai_client", lambda: client)

        out = tmp_path / "seg.mp3"
        with pytest.raises(pg.SilentTakeError):
            pg.generate_tts_for_segment(TEXT, "riley", str(out))

        assert client.audio.speech.calls == 2

    def test_silence_is_caught_even_when_duration_check_would_pass(self, monkeypatch, tmp_path):
        # Regression for 2026-08-16: a full-length silent take sailed through
        # the duration ratio (~1.0) and shipped 27s of dead air on air.
        client = FakeClient([(8000, SILENT_DBFS), (8000, SILENT_DBFS)])
        monkeypatch.setattr(pg, "get_openai_client", lambda: client)

        with pytest.raises(pg.SilentTakeError):
            pg.generate_tts_for_segment(TEXT, "riley", str(tmp_path / "seg.mp3"))

    def test_quiet_but_audible_take_is_not_treated_as_silent(self, monkeypatch, tmp_path):
        # A genuinely soft delivery must not be thrown away — the threshold
        # sits in dead space well below any real speech.
        client = FakeClient([(8000, -35.0)])
        monkeypatch.setattr(pg, "get_openai_client", lambda: client)

        pg.generate_tts_for_segment(TEXT, "riley", str(tmp_path / "seg.mp3"))

        assert client.audio.speech.calls == 1

    def test_empty_take_counts_as_silent(self, monkeypatch, tmp_path):
        # A zero-length clip has no peak to measure; _is_silent_take must not
        # depend on max_dBFS being meaningful for it.
        assert pg._is_silent_take(FakeAudio(0, AUDIBLE_DBFS))


class TestOpenAITTSModelSelection:
    """tts-1 takes a speed multiplier; the steerable models take direction.

    Sending `speed` to a model that ignores it would leave _expected_speech_ms
    dividing by a multiplier the audio never had, quietly moving the 0.80 floor
    on every segment.
    """

    def test_legacy_model_sends_speed_and_no_instructions(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pg, "OPENAI_TTS_MODEL", "tts-1")
        monkeypatch.setattr(pg, "get_speed_for_host", lambda host: 1.1)
        client = FakeClient([8000])
        monkeypatch.setattr(pg, "get_openai_client", lambda: client)

        pg.generate_tts_for_segment(TEXT, "casey", str(tmp_path / "seg.mp3"))

        sent = client.audio.speech.last_kwargs
        assert sent["model"] == "tts-1"
        assert sent["speed"] == 1.1
        assert "instructions" not in sent

    def test_steerable_model_sends_instructions_and_no_speed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pg, "OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
        monkeypatch.setattr(pg, "get_speed_for_host", lambda host: 1.1)
        monkeypatch.setattr(pg, "get_voice_instructions_for_host", lambda host: "Voice: dry.")
        client = FakeClient([8000])
        monkeypatch.setattr(pg, "get_openai_client", lambda: client)

        pg.generate_tts_for_segment(TEXT, "casey", str(tmp_path / "seg.mp3"))

        sent = client.audio.speech.last_kwargs
        assert sent["model"] == "gpt-4o-mini-tts"
        assert sent["instructions"] == "Voice: dry."
        assert "speed" not in sent

    def test_steerable_model_estimates_duration_at_unit_speed(self, monkeypatch, tmp_path):
        """The host's 1.1 multiplier is direction now, so it must not divide."""
        monkeypatch.setattr(pg, "OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
        monkeypatch.setattr(pg, "get_speed_for_host", lambda host: 2.0)
        monkeypatch.setattr(pg, "get_voice_instructions_for_host", lambda host: "Voice: dry.")
        # 20 words at unit speed expects ~6738ms; 0.80 of that is ~5390ms.
        # A 5000ms take is short against 1.0 and would look fine against 2.0.
        client = FakeClient([5000, 8000])
        monkeypatch.setattr(pg, "get_openai_client", lambda: client)

        pg.generate_tts_for_segment(TEXT, "casey", str(tmp_path / "seg.mp3"))

        assert client.audio.speech.calls == 2


class TestSpeechRateFit:
    """The 369ms/word fit is tts-1's. Borrowing it is allowed, silently isn't."""

    def test_fitted_model_uses_its_own_row_and_stays_quiet(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(pg, "degrade", lambda name, detail: recorded.append(name))
        monkeypatch.setattr(pg, "OPENAI_TTS_MODEL", "tts-1")
        monkeypatch.setattr(pg, "_borrowed_fit_reported", False)

        assert pg._speech_rate_fit() == (369, 642)
        assert recorded == []

    def test_steerable_model_uses_its_own_measured_row(self, monkeypatch):
        """Measured off 2026-08-23..25's sidecars, so it must not borrow."""
        recorded = []
        monkeypatch.setattr(pg, "degrade", lambda name, detail: recorded.append(name))
        monkeypatch.setattr(pg, "OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
        monkeypatch.setattr(pg, "_borrowed_fit_reported", False)

        assert pg._speech_rate_fit() == (372, 660)
        assert recorded == []

    def test_unfitted_model_borrows_and_says_so_once(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(pg, "degrade", lambda name, detail: recorded.append(name))
        # A model with no row of its own — gpt-4o-mini-tts has one now.
        monkeypatch.setattr(pg, "OPENAI_TTS_MODEL", "gpt-4o-mini-tts-preview")
        monkeypatch.setattr(pg, "_borrowed_fit_reported", False)

        assert pg._speech_rate_fit() == (369, 642)
        assert pg._speech_rate_fit() == (369, 642)
        # Once, not once per segment — degrade() concatenates repeat details.
        assert recorded == ["render/borrowed-speech-rate"]
