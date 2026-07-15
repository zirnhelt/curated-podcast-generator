"""Tests for youtube_upload.py — metadata builder, ledger idempotency, upload flow."""

import json

import pytest

import youtube_upload as yu


@pytest.fixture
def citations():
    return {
        "episode": {
            "date": "2026-07-14",
            "formatted_date": "Tuesday, July 14, 2026",
            "theme": "Working Lands & Industry",
            "title": "Cariboo Signals - Working Lands & Industry",
        },
        "segments": {
            "news_roundup": {
                "articles": [
                    {"title": "Story one", "source": "Tyee", "url": "https://example.com/1"},
                    {"title": "Story <two>", "source": "CBC", "url": "https://example.com/2"},
                ]
            },
            "deep_dive": {
                "articles": [
                    {"title": "Deep story", "source": "Narwhal", "url": "https://example.com/dd"},
                ]
            },
        },
    }


@pytest.fixture
def chapters():
    return [
        {"startTime": 0, "title": "Cold Open"},
        {"startTime": 25.3, "title": "Introduction"},
        {"startTime": 95.0, "title": "News Roundup"},
        {"startTime": 3700.0, "title": "Credits"},
    ]


class TestTimestamps:
    def test_minutes_seconds(self):
        assert yu._fmt_ts(0) == "0:00"
        assert yu._fmt_ts(25.3) == "0:25"
        assert yu._fmt_ts(95) == "1:35"

    def test_hours(self):
        assert yu._fmt_ts(3700) == "1:01:40"


class TestBuildMetadata:
    def test_title_and_privacy(self, citations, chapters):
        meta = yu.build_metadata(citations, chapters, "unlisted", "2026-07-14")
        assert meta["snippet"]["title"].startswith("Cariboo Signals - Working Lands & Industry")
        assert len(meta["snippet"]["title"]) <= yu.MAX_TITLE_LEN
        assert meta["status"]["privacyStatus"] == "unlisted"
        assert meta["status"]["selfDeclaredMadeForKids"] is False
        assert meta["snippet"]["categoryId"] == "28"

    def test_chapter_lines_start_at_zero(self, citations, chapters):
        desc = yu.build_metadata(citations, chapters, "private", "2026-07-14")["snippet"]["description"]
        lines = desc.splitlines()
        chapter_lines = [ln for ln in lines if ln and ln[0].isdigit()]
        assert chapter_lines[0] == "0:00 Cold Open"
        assert "1:35 News Roundup" in chapter_lines

    def test_nonzero_first_chapter_forced_to_zero(self, citations):
        chaps = [{"startTime": 4.0, "title": "Introduction"}]
        desc = yu.build_metadata(citations, chaps, "private", "2026-07-14")["snippet"]["description"]
        assert "0:00 Introduction" in desc

    def test_citation_urls_present_and_angle_brackets_stripped(self, citations, chapters):
        desc = yu.build_metadata(citations, chapters, "private", "2026-07-14")["snippet"]["description"]
        assert "https://example.com/1" in desc
        assert "https://example.com/dd" in desc
        assert "<" not in desc and ">" not in desc

    def test_description_capped(self, citations, chapters):
        citations["segments"]["news_roundup"]["articles"] = [
            {"title": "T" * 300, "source": "S", "url": "https://example.com/" + "x" * 200}
            for _ in range(40)
        ]
        desc = yu.build_metadata(citations, chapters, "private", "2026-07-14")["snippet"]["description"]
        assert len(desc) <= yu.MAX_DESC_LEN

    def test_empty_citations_still_builds(self):
        meta = yu.build_metadata({}, [], "private", "2026-07-14")
        assert meta["snippet"]["title"]
        assert meta["snippet"]["description"]


class TestLedger:
    def test_idempotency(self, tmp_path, monkeypatch):
        ledger_file = tmp_path / "youtube_uploads.json"
        monkeypatch.setattr(yu, "LEDGER_PATH", ledger_file)
        assert not yu.already_uploaded("2026-07-14")
        yu.save_ledger({"2026-07-14": {"video_id": "abc", "url": "u", "uploaded_at": "t"}})
        assert yu.already_uploaded("2026-07-14")

    def test_upload_episode_skips_when_ledgered(self, tmp_path, monkeypatch):
        ledger_file = tmp_path / "youtube_uploads.json"
        monkeypatch.setattr(yu, "LEDGER_PATH", ledger_file)
        entry = {"video_id": "abc", "url": "https://youtube.com/watch?v=abc", "uploaded_at": "t"}
        yu.save_ledger({"2026-07-14": entry})

        def _boom(*a, **k):
            raise AssertionError("should not build a service for an already-uploaded date")

        monkeypatch.setattr(yu, "get_service", _boom)
        result = yu.upload_episode("x.mp4", None, None, None, "2026-07-14")
        assert result == entry


class TestCredentials:
    def test_have_credentials_requires_all_three(self, monkeypatch):
        for key in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"):
            monkeypatch.delenv(key, raising=False)
        assert not yu.have_credentials()
        monkeypatch.setenv("YT_CLIENT_ID", "id")
        monkeypatch.setenv("YT_CLIENT_SECRET", "secret")
        assert not yu.have_credentials()
        monkeypatch.setenv("YT_REFRESH_TOKEN", "token")
        assert yu.have_credentials()


class TestUploadFlow:
    def test_upload_episode_full_flow(self, tmp_path, monkeypatch, citations, chapters):
        ledger_file = tmp_path / "youtube_uploads.json"
        monkeypatch.setattr(yu, "LEDGER_PATH", ledger_file)

        citations_path = tmp_path / "citations.json"
        citations_path.write_text(json.dumps(citations))
        chapters_path = tmp_path / "chapters.json"
        chapters_path.write_text(json.dumps({"chapters": chapters}))
        vtt_path = tmp_path / "transcript.vtt"
        vtt_path.write_text("WEBVTT\n")
        mp4 = tmp_path / "video.mp4"
        mp4.write_bytes(b"\x00")

        captured = {}

        class FakeService:
            pass

        monkeypatch.setattr(yu, "get_service", lambda: FakeService())

        def fake_upload_video(service, mp4_path, metadata):
            captured["metadata"] = metadata
            return "vid123"

        def fake_upload_captions(service, video_id, vtt):
            captured["captions"] = (video_id, vtt)

        monkeypatch.setattr(yu, "upload_video", fake_upload_video)
        monkeypatch.setattr(yu, "upload_captions", fake_upload_captions)

        entry = yu.upload_episode(str(mp4), str(citations_path), str(chapters_path),
                                  str(vtt_path), "2026-07-14", privacy="private")
        assert entry["video_id"] == "vid123"
        assert entry["url"].endswith("vid123")
        assert captured["metadata"]["status"]["privacyStatus"] == "private"
        assert captured["captions"] == ("vid123", str(vtt_path))
        # Ledger persisted
        assert yu.already_uploaded("2026-07-14")
