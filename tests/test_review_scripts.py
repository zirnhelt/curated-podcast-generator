"""The weekly script review must fail loudly rather than save an empty report.

From 2026-07-04 the review ran on Sonnet 5, which thinks when `thinking` is
omitted; thinking shares max_tokens with the report, and every review from
2026-07-12 to 2026-09-20 was committed as a 132-byte header with nothing under it.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import review_scripts as rs


class _FakeClient:
    def __init__(self, text, stop_reason):
        self.kwargs = {}
        self.messages = self
        self._text, self._stop = text, stop_reason

    def create(self, **kwargs):
        self.kwargs = kwargs
        blocks = [SimpleNamespace(type="thinking", thinking="")]
        if self._text:
            blocks.append(SimpleNamespace(type="text", text=self._text))
        return SimpleNamespace(content=blocks, stop_reason=self._stop)


@pytest.fixture
def review_env(monkeypatch):
    monkeypatch.setattr(rs, "find_recent_scripts", lambda days: [("podcast_script_x.txt", "RILEY: hi")])
    monkeypatch.setattr(rs, "summarize_recent_changes", lambda days: "")
    monkeypatch.setattr(rs, "build_review_prompt", lambda *a, **k: "prompt")

    def install(text, stop_reason):
        client = _FakeClient(text, stop_reason)
        monkeypatch.setattr(rs.anthropic, "Anthropic", lambda *a, **k: client, raising=False)
        return client
    return install


def test_thinking_is_off_and_the_budget_fits_a_report(review_env):
    client = review_env("## Review\nFine.", "end_turn")
    assert rs.run_review(7).startswith("## Review")
    assert client.kwargs["thinking"] == {"type": "disabled"}
    assert client.kwargs["max_tokens"] >= 16000


def test_empty_report_fails_instead_of_saving_a_header(review_env):
    review_env("", "max_tokens")
    with pytest.raises(SystemExit):
        rs.run_review(7)


def test_truncated_report_says_so(review_env):
    review_env("## Review\nPartial", "max_tokens")
    assert "truncated" in rs.run_review(7)
