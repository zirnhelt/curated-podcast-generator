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
