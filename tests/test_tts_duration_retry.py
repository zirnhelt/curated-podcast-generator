"""Tests for the OpenAI TTS duration-ratio retry in generate_tts_for_segment.

A truncated take (OpenAI silently dropping words) used to only print a
warning — the published transcript is built from the segment's *real*
measured duration, so a caption window still gets sized to whatever audio
actually rendered, but the text shown is the full (untruncated) script line.
The listener hears a shorter clip than the caption implies actually got
spoken. One retry usually recovers the full-length take.
"""

import io

import pytest

import podcast_generator as pg


class FakeAudio:
    """Length-only stand-in for an mp3 AudioSegment, keyed off the content bytes."""

    def __init__(self, duration_ms):
        self.duration_ms = duration_ms

    def __len__(self):
        return self.duration_ms


def _content(duration_ms: int) -> bytes:
    return str(duration_ms).encode()


class FakeAudioSegmentCls:
    """Decodes duration from the fake TTS response bytes written to disk / passed in."""

    @staticmethod
    def from_mp3(path):
        with open(path, "rb") as f:
            return FakeAudio(int(f.read()))

    @staticmethod
    def from_file(fileobj, format=None):
        return FakeAudio(int(fileobj.read()))


class FakeResponse:
    def __init__(self, content):
        self.content = content


class FakeSpeech:
    def __init__(self, durations_ms):
        self._durations = list(durations_ms)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        duration_ms = self._durations[min(self.calls, len(self._durations)) - 1]
        return FakeResponse(_content(duration_ms))


class FakeAudioNamespace:
    def __init__(self, durations_ms):
        self.speech = FakeSpeech(durations_ms)


class FakeClient:
    def __init__(self, durations_ms):
        self.audio = FakeAudioNamespace(durations_ms)


TEXT = " ".join(["word"] * 20)  # 20 words, well above the 10-word floor


@pytest.fixture(autouse=True)
def _patch_tts_deps(monkeypatch):
    monkeypatch.setattr(pg, "AudioSegment", FakeAudioSegmentCls)
    monkeypatch.setattr(pg, "get_voice_for_host", lambda host: "nova")
    monkeypatch.setattr(pg, "get_speed_for_host", lambda host: 1.0)


class TestTTSDurationRetry:
    def test_full_length_take_does_not_retry(self, monkeypatch, tmp_path):
        # 20 words * 400ms/word = 8000ms expected; deliver exactly that.
        client = FakeClient([8000])
        monkeypatch.setattr(pg, "get_openai_client", lambda: client)

        out = tmp_path / "seg.mp3"
        pg.generate_tts_for_segment(TEXT, "riley", str(out))

        assert client.audio.speech.calls == 1
        assert int(out.read_bytes()) == 8000

    def test_short_take_retries_and_keeps_longer_result(self, monkeypatch, tmp_path, capsys):
        # First take is 50% of expected (well under the 0.80 threshold);
        # retry comes back full-length and should be what's on disk after.
        client = FakeClient([4000, 8000])
        monkeypatch.setattr(pg, "get_openai_client", lambda: client)

        out = tmp_path / "seg.mp3"
        pg.generate_tts_for_segment(TEXT, "riley", str(out))

        assert client.audio.speech.calls == 2
        assert int(out.read_bytes()) == 8000
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
        assert int(out.read_bytes()) == 4500
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
        assert int(out.read_bytes()) == 4000

    def test_speed_multiplier_scales_expected_duration(self, monkeypatch, tmp_path):
        # speed=1.1 means ~10% shorter audio is expected and should not
        # itself trip the "possible word omission" retry.
        monkeypatch.setattr(pg, "get_speed_for_host", lambda host: 1.1)
        client = FakeClient([int(8000 / 1.1)])
        monkeypatch.setattr(pg, "get_openai_client", lambda: client)

        out = tmp_path / "seg.mp3"
        pg.generate_tts_for_segment(TEXT, "casey", str(out))

        assert client.audio.speech.calls == 1
