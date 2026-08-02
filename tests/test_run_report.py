"""Tests for degrade() and the per-segment run report.

segment() can only downgrade a phase whose exception escapes the block, but the
render and publish paths handle their own failures in place. Before degrade()
existed, that meant a dead TTS provider, a music-less episode, or a completely
skipped R2 sync all reported `ok` and the run went green (2026-08-02). These
tests pin the reporting, not the fallback behaviour — the episode is still
expected to ship.
"""

import re

import pytest

import podcast_generator as pg


@pytest.fixture(autouse=True)
def clean_segments():
    """_RUN_SEGMENTS is a module global that nothing resets between runs."""
    pg._RUN_SEGMENTS.clear()
    yield
    pg._RUN_SEGMENTS.clear()


class TestDegrade:
    def test_records_a_degraded_segment(self):
        pg.degrade("render/tts-provider-fallback", "gemini died")

        assert pg._RUN_SEGMENTS == [{
            "name": "render/tts-provider-fallback",
            "status": "degraded",
            "seconds": 0.0,
            "error": "gemini died",
        }]

    def test_emits_a_workflow_warning_annotation(self, capsys):
        pg.degrade("publish/r2-sync", "no credentials")

        out = capsys.readouterr().out
        assert "::warning::" in out
        assert "publish/r2-sync" in out
        assert "no credentials" in out

    def test_downgrades_the_matching_segment_in_place(self):
        """A surface reporting its own handled failure must not produce a second
        row next to its segment's `ok` one."""
        with pg.segment("publish/r2-sync", critical=False):
            pg.degrade("publish/r2-sync", "no credentials")

        assert [(r["name"], r["status"]) for r in pg._RUN_SEGMENTS] == [
            ("publish/r2-sync", "degraded"),
        ]
        assert pg._RUN_SEGMENTS[0]["error"] == "no credentials"

    def test_repeat_calls_under_one_name_merge(self):
        """A failure inside a per-episode loop is one row, not fifty."""
        pg.degrade("publish/rss", "episode A dropped")
        pg.degrade("publish/rss", "episode B dropped")

        assert len(pg._RUN_SEGMENTS) == 1
        assert pg._RUN_SEGMENTS[0]["error"] == "episode A dropped; episode B dropped"

    def test_sits_alongside_real_segments(self):
        with pg.segment("render/tts"):
            pg.degrade("render/music-fallback", "assembly blew up")

        assert [(r["name"], r["status"]) for r in pg._RUN_SEGMENTS] == [
            ("render/tts", "ok"),
            ("render/music-fallback", "degraded"),
        ]


class TestWriteRunReport:
    def test_degraded_row_reaches_the_step_summary(self, tmp_path, monkeypatch):
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

        pg.degrade("render/tts-provider-fallback", "gemini TTS failed")
        pg.write_run_report("render")

        report = summary.read_text(encoding="utf-8")
        assert "`render/tts-provider-fallback`" in report
        assert "⚠️ degraded" in report
        assert "gemini TTS failed" in report

    def test_pipes_are_escaped_so_the_table_survives(self, tmp_path, monkeypatch):
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

        pg.degrade("publish/rss", "a | b | c")
        pg.write_run_report("publish")

        row = [l for l in summary.read_text().splitlines()
               if "publish/rss" in l][0]
        assert r"a \| b \| c" in row, row
        # Every pipe in the detail cell is escaped, so the row still has exactly
        # 4 cells (5 unescaped delimiters) and the markdown table survives.
        assert len(re.findall(r"(?<!\\)\|", row)) == 5, row


class TestPublishStageDegradation:
    """run_publish_stage decides exit 78 from segment status, so a surface that
    swallowed its own failure used to make that exit code unreachable."""

    def test_degrade_from_inside_a_publish_surface_fails_the_stage(
        self, tmp_path, monkeypatch
    ):
        script = tmp_path / "podcast_script_2026-08-02_science.txt"
        script.write_text("Riley: hi\n", encoding="utf-8")

        monkeypatch.setattr(pg, "resolve_script_for_audio", lambda *a, **k: str(script))
        monkeypatch.setattr(pg, "generate_episode_transcript", lambda *a, **k: None)
        monkeypatch.setattr(pg, "generate_tts_test_feed", lambda *a, **k: None)
        monkeypatch.setattr(pg, "_regenerate_index_html", lambda *a, **k: None)
        monkeypatch.setattr(pg, "generate_podcast_rss_feed", lambda *a, **k: None)
        # Stands in for missing R2 credentials: returns normally, degrades.
        monkeypatch.setattr(
            pg, "sync_site_to_r2",
            lambda *a, **k: pg.degrade("publish/r2-sync", "R2 credentials not configured"),
        )

        assert pg.run_publish_stage(script_path=str(script)) is False

    def test_clean_publish_still_succeeds(self, tmp_path, monkeypatch):
        script = tmp_path / "podcast_script_2026-08-02_science.txt"
        script.write_text("Riley: hi\n", encoding="utf-8")

        monkeypatch.setattr(pg, "resolve_script_for_audio", lambda *a, **k: str(script))
        for name in ("generate_episode_transcript", "generate_tts_test_feed",
                     "_regenerate_index_html", "generate_podcast_rss_feed",
                     "sync_site_to_r2"):
            monkeypatch.setattr(pg, name, lambda *a, **k: None)

        assert pg.run_publish_stage(script_path=str(script)) is True

    def test_a_degraded_render_does_not_fail_publish(self, tmp_path, monkeypatch):
        """The provider fallback must not leak across the stage boundary — the
        episode shipped, it just shipped in a different voice."""
        script = tmp_path / "podcast_script_2026-08-02_science.txt"
        script.write_text("Riley: hi\n", encoding="utf-8")

        monkeypatch.setattr(pg, "resolve_script_for_audio", lambda *a, **k: str(script))
        for name in ("generate_episode_transcript", "generate_tts_test_feed",
                     "_regenerate_index_html", "generate_podcast_rss_feed",
                     "sync_site_to_r2"):
            monkeypatch.setattr(pg, name, lambda *a, **k: None)

        pg.degrade("render/tts-provider-fallback", "gemini died")

        assert pg.run_publish_stage(script_path=str(script)) is True


class TestProviderFallbackIsReported:
    def test_failed_provider_still_returns_audio_and_records_the_fallback(
        self, tmp_path, monkeypatch
    ):
        out = tmp_path / "podcast_audio_2026-08-02_science.mp3"
        calls = []

        real = pg.generate_audio_tts_only

        def fake_openai_client():
            return object()

        def tts_only(script, output_filename, _force_openai=False):
            calls.append(_force_openai)
            if not _force_openai:
                # Exercise the real handler's fallback branch.
                return real(script, output_filename, _force_openai=False)
            out.write_bytes(b"mp3")
            return str(out)

        monkeypatch.setattr(pg, "get_openai_client", fake_openai_client)
        monkeypatch.setattr(pg, "get_active_tts_provider", lambda: "gemini")
        monkeypatch.setattr(pg, "get_gemini_api_key", lambda: "test-key")
        monkeypatch.setattr(pg, "parse_script_into_segments",
                            lambda s: (_ for _ in ()).throw(RuntimeError("gemini 500")))
        monkeypatch.setattr(pg, "generate_audio_tts_only", tts_only)

        result = tts_only("Riley: hi", str(out))

        assert result == str(out), "the episode must still ship"
        names = [r["name"] for r in pg._RUN_SEGMENTS]
        assert "render/tts-provider-fallback" in names
        assert all(r["status"] == "degraded" for r in pg._RUN_SEGMENTS)
