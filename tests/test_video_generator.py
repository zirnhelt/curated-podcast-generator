"""Tests for video_generator.py — slide rendering, timing math, ffmpeg assembly."""

import json
import os

import pytest

pytest.importorskip("PIL", reason="Pillow required for video generator tests")
from PIL import Image

import video_generator as vg


@pytest.fixture
def sample_turns():
    return [
        {"speaker": "riley", "section": "welcome", "start_ms": 0, "dur_ms": 5000},
        {"speaker": "casey", "section": "welcome", "start_ms": 5500, "dur_ms": 4000},
        # Adjacent riley turns separated by <1.5s gap — should merge
        {"speaker": "riley", "section": "news", "start_ms": 10000, "dur_ms": 3000},
        {"speaker": "riley", "section": "news", "start_ms": 13400, "dur_ms": 2000},
        # Far-apart riley turn — separate span
        {"speaker": "riley", "section": "deep", "start_ms": 60000, "dur_ms": 1000},
        # Azure fallback marker
        {"speaker": None, "section": "spotlight", "start_ms": 70000, "dur_ms": 8000},
    ]


@pytest.fixture
def sample_chapters():
    return [
        {"startTime": 0, "title": "Cold Open"},
        {"startTime": 15.0, "title": "Introduction"},
        {"startTime": 60.0, "title": "News Roundup"},
        {"startTime": 300.0, "title": "Deep Dive"},
        {"startTime": 500.0, "title": "Credits"},
    ]


@pytest.fixture
def sample_citations():
    return {
        "episode": {
            "date": "2026-07-14",
            "formatted_date": "Tuesday, July 14, 2026",
            "theme": "Working Lands & Industry",
            "title": "Cariboo Signals - Working Lands & Industry",
        },
        "segments": {
            "news_roundup": {
                "title": "News",
                "articles": [
                    {"title": f"Headline number {i} about a rural technology story", "source": f"Source {i}", "url": f"https://example.com/{i}"}
                    for i in range(6)
                ],
            },
            "deep_dive": {
                "title": "Deep Dive",
                "articles": [
                    {"title": "Farmland ownership and technology", "source": "The Narwhal", "url": "https://example.com/dd"}
                ],
                "discussion": {"central_question": "Does ownership structure matter more than technology?"},
            },
        },
    }


class TestSpeakerSpans:
    def test_merges_adjacent_same_speaker_turns(self, sample_turns):
        spans = vg.merge_speaker_spans(sample_turns, "riley")
        # welcome turn, merged news pair, distant deep-dive turn
        assert spans == [(0.0, 5.0), (10.0, 15.4), (60.0, 61.0)]

    def test_casey_single_span(self, sample_turns):
        assert vg.merge_speaker_spans(sample_turns, "casey") == [(5.5, 9.5)]

    def test_none_speaker_never_matches(self, sample_turns):
        assert vg.merge_speaker_spans(sample_turns, None) == []

    def test_enable_expr_format(self, sample_turns):
        expr = vg.build_enable_expr(sample_turns, "casey")
        assert expr == "between(t,5.5,9.5)"

    def test_enable_expr_empty_for_unknown_speaker(self, sample_turns):
        assert vg.build_enable_expr(sample_turns, "nobody") == ""


class TestSlides:
    def test_renders_one_slide_per_chapter(self, sample_chapters, sample_citations, tmp_path):
        slides = vg.render_slides(sample_chapters, sample_citations, 600.0,
                                  vg.ASSETS_DIR / "cariboo-signals.png", str(tmp_path))
        assert len(slides) == len(sample_chapters)
        # Full coverage: first starts at 0, last ends at audio duration, contiguous
        assert slides[0][1] == 0
        assert slides[-1][2] == 600.0
        for (_, _, prev_end), (_, start, _) in zip(slides, slides[1:]):
            assert prev_end == start

    def test_slides_are_720p_pngs(self, sample_chapters, sample_citations, tmp_path):
        slides = vg.render_slides(sample_chapters, sample_citations, 600.0,
                                  vg.ASSETS_DIR / "cariboo-signals.png", str(tmp_path))
        for png, _, _ in slides:
            with Image.open(png) as img:
                assert img.size == (vg.WIDTH, vg.HEIGHT)

    def test_no_chapters_falls_back_to_single_slide(self, sample_citations, tmp_path):
        slides = vg.render_slides([], sample_citations, 120.0,
                                  vg.ASSETS_DIR / "cariboo-signals.png", str(tmp_path))
        assert len(slides) == 1
        assert slides[0][1:] == (0.0, 120.0)

    def test_missing_cover_art_tolerated(self, sample_chapters, sample_citations, tmp_path):
        slides = vg.render_slides(sample_chapters, sample_citations, 600.0,
                                  tmp_path / "nonexistent.png", str(tmp_path))
        assert len(slides) == len(sample_chapters)

    def test_badges_rendered_per_host(self, tmp_path):
        badges = vg.render_speaker_badges(str(tmp_path))
        assert set(badges) == {"riley", "casey"}
        for png in badges.values():
            assert os.path.exists(png)


class TestConcatFile:
    def test_durations_and_trailing_repeat(self, tmp_path):
        slides = [("a.png", 0.0, 10.0), ("b.png", 10.0, 25.5)]
        path = vg.write_concat_file(slides, str(tmp_path))
        lines = open(path).read().strip().splitlines()
        assert lines == [
            "file 'a.png'", "duration 10.000",
            "file 'b.png'", "duration 15.500",
            "file 'b.png'",
        ]


class TestFfmpegCommand:
    def test_command_assembly(self, sample_turns, tmp_path):
        badges = {"riley": "badge_riley.png", "casey": "badge_casey.png"}
        cmd = vg.build_ffmpeg_command("ep.mp3", "slides.txt", badges,
                                      sample_turns, "out.mp4", str(tmp_path))
        assert cmd[0] == "ffmpeg"
        assert "ep.mp3" in cmd and "slides.txt" in cmd and cmd[-1] == "out.mp4"
        assert "libx264" in cmd and "-filter_complex_script" in cmd
        script = open(os.path.join(str(tmp_path), "filters.txt")).read()
        assert "showwaves" in script
        assert script.count("between(t,") >= 3
        assert script.rstrip().endswith("[vout]")

    def test_format_conversion_precedes_fps(self, tmp_path):
        # Regression: without format= before fps, the fps filter's duplicate-
        # frame bursts get materialized per-frame downstream and OOM the CI
        # runner (2026-07-15/16 runner deaths, exit 143).
        vg.build_ffmpeg_command("ep.mp3", "slides.txt", {}, [], "out.mp4", str(tmp_path))
        script = open(os.path.join(str(tmp_path), "filters.txt")).read()
        slides_chain = script.splitlines()[0]
        assert "format=yuv420p" in slides_chain
        assert slides_chain.index("format=yuv420p") < slides_chain.index("fps=")

    def test_no_badges_still_produces_vout(self, tmp_path):
        cmd = vg.build_ffmpeg_command("ep.mp3", "slides.txt", {}, [], "out.mp4", str(tmp_path))
        script = open(os.path.join(str(tmp_path), "filters.txt")).read()
        assert script.rstrip().endswith("[vout]")
        assert "-map" in cmd and "[vout]" in cmd

    def test_speaker_none_timeline_yields_no_badge_overlays(self, tmp_path):
        turns = [{"speaker": None, "section": "welcome", "start_ms": 0, "dur_ms": 9000}]
        badges = {"riley": "badge_riley.png", "casey": "badge_casey.png"}
        vg.build_ffmpeg_command("ep.mp3", "slides.txt", badges, turns, "out.mp4", str(tmp_path))
        script = open(os.path.join(str(tmp_path), "filters.txt")).read()
        assert "badge" not in script
        assert "between(t," not in script


class TestArtifacts:
    def test_load_episode_artifacts_missing_audio_raises(self):
        with pytest.raises(FileNotFoundError):
            vg.load_episode_artifacts("1999-01-01")

    def test_pick_cover_weekday_map(self):
        # 2026-07-14 is a Tuesday, 2026-07-18 Saturday, 2026-07-19 Sunday
        assert vg.pick_cover_image("2026-07-14").name == "cariboo-signals.png"
        assert vg.pick_cover_image("2026-07-18").name == "cariboo-saturday.png"
        assert vg.pick_cover_image("2026-07-19").name == "cariboo-sunday.png"
