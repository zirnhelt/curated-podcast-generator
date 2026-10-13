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


def test_sonnet_5_5_gets_between_tools_not_disabled(review_env, monkeypatch):
    """Sonnet 5.5 answers thinking={"type": "disabled"} with a 400."""
    monkeypatch.setattr(rs, "REVIEW_MODEL", "claude-sonnet-5-5")
    client = review_env("## Review\nFine.", "end_turn")
    rs.run_review(7)
    assert client.kwargs["model"] == "claude-sonnet-5-5"
    assert client.kwargs["thinking"] == {"type": "between_tools"}


# ── The reader's own episode verdicts, by polish arm ──────────────────────────
#
# review.html's "How was the episode?" card saves rating 'episode' rows into the feed repo's
# feedback files. The polish never changes which stories air, so the story-choice ratings cannot
# grade the writing; this table can, once each episode's polish outcome is on record.

import json
from datetime import date

import requests


def _resp(status=200, body=None):
    return SimpleNamespace(
        status_code=status, json=lambda: body,
        raise_for_status=lambda: (_ for _ in ()).throw(requests.HTTPError(str(status))) if status >= 400 else None)


def _episode(when, verdict, reasons=(), rated="2026-10-14T10:00:00Z"):
    return {"rating": "episode", "episode_date": when, "episode_verdict": verdict,
            "episode_reasons": list(reasons), "rated_at": rated}


TODAY = date(2026, 10, 20)


class TestFetchEpisodeVerdicts:
    def _get(self, files, calls=None):
        def get(url, headers=None, timeout=None):
            day = url.rsplit("/", 1)[1].removesuffix(".json")
            if calls is not None:
                calls.append(day)
            if day in files:
                return files[day] if isinstance(files[day], SimpleNamespace) else _resp(200, {"ratings": files[day]})
            return _resp(404)
        return get

    def test_reads_episode_rows_only_and_skips_days_without_a_file(self):
        files = {
            "2026-10-19": [_episode("2026-10-18", "keep", ["Writing"]),
                           {"url": "https://x.test/a", "rating": "good"},       # an article row
                           {"rating": "episode", "episode_date": "2026-10-17", "episode_verdict": "great"},  # bad value
                           "not a dict"],
        }
        verdicts, problem = rs.fetch_episode_verdicts(TODAY, get=self._get(files))
        assert problem is None
        assert list(verdicts) == ["2026-10-18"]

    def test_the_latest_answer_for_an_episode_wins_across_files(self):
        files = {"2026-10-19": [_episode("2026-10-18", "keep", rated="2026-10-19T08:00:00Z")],
                 "2026-10-20": [_episode("2026-10-18", "meh", rated="2026-10-20T08:00:00Z")]}
        verdicts, _ = rs.fetch_episode_verdicts(TODAY, get=self._get(files))
        assert verdicts["2026-10-18"]["episode_verdict"] == "meh"
        # And the other way round: an older file read later must not overwrite a newer answer.
        files = {"2026-10-19": [_episode("2026-10-18", "meh", rated="2026-10-19T08:00:00Z")],
                 "2026-10-20": [_episode("2026-10-18", "skip", rated="2026-10-20T08:00:00Z")],
                 "2026-10-18": [_episode("2026-10-18", "keep", rated="2026-10-18T20:00:00Z")]}
        verdicts, _ = rs.fetch_episode_verdicts(TODAY, get=self._get(files))
        assert verdicts["2026-10-18"]["episode_verdict"] == "skip"

    def test_reads_the_window_and_no_further(self):
        calls = []
        rs.fetch_episode_verdicts(TODAY, days=3, get=self._get({}, calls))
        assert calls == ["2026-10-20", "2026-10-19", "2026-10-18", "2026-10-17"]

    def test_a_failure_stops_the_read_and_is_reported_with_what_was_read(self):
        files = {"2026-10-20": [_episode("2026-10-19", "keep")], "2026-10-19": _resp(403)}
        verdicts, problem = rs.fetch_episode_verdicts(TODAY, get=self._get(files))
        assert list(verdicts) == ["2026-10-19"]
        assert "HTTPError" in problem and "2026-10-19" in problem

    def test_a_network_error_is_a_problem_not_a_crash(self):
        def get(*a, **k):
            raise requests.ConnectionError("down")
        verdicts, problem = rs.fetch_episode_verdicts(TODAY, get=get)
        assert verdicts == {} and "ConnectionError" in problem

    def test_a_malformed_file_is_a_problem_not_a_crash(self):
        files = {"2026-10-20": SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                                               json=lambda: (_ for _ in ()).throw(ValueError("bad json")))}
        _, problem = rs.fetch_episode_verdicts(TODAY, get=self._get(files))
        assert "ValueError" in problem

    def test_sends_the_token_when_there_is_one(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        monkeypatch.delenv("GH_TOKEN", raising=False)
        seen = {}

        def get(url, headers=None, timeout=None):
            seen.update(headers)
            return _resp(404)
        rs.fetch_episode_verdicts(TODAY, days=0, get=get)
        assert seen["Authorization"] == "Bearer tok"


MODELS = {
    "2026-10-15": {"review": "claude-sonnet-5-5", "polish": "batch"},
    "2026-10-16": {"review": "claude-sonnet-5-5", "polish": "original"},
    "2026-10-17": {"review": "claude-sonnet-5-5", "polish": "original"},
    "2026-10-18": {"review": "claude-haiku-5-5", "polish": "realtime"},
    "2026-10-12": {"review": "claude-sonnet-5-5"},     # before the outcome was logged
}


class TestVerdictTable:
    def _table(self, verdicts, models=MODELS, **kw):
        return rs.verdict_table(verdicts, models, TODAY, **kw)

    def test_groups_by_review_model_and_polish_outcome(self):
        verdicts = {
            "2026-10-15": _episode("2026-10-15", "keep", ["Writing", "Stories"]),
            "2026-10-16": _episode("2026-10-16", "skip", ["Writing"]),
            "2026-10-17": _episode("2026-10-17", "meh", ["Writing", "Repetitive"]),
            "2026-10-18": _episode("2026-10-18", "keep"),
        }
        out = self._table(verdicts)
        assert "| claude-sonnet-5-5 | batch | 1 | 1 | 1 | 0 | 0 | 100% | Writing ×1, Stories ×1 |" in out
        assert "| claude-haiku-5-5 | realtime | 1 | 1 | 1 | 0 | 0 | 100% | — |" in out
        # Two `original` episodes, one meh and one skip: the keep rate is 0%, reasons are tallied.
        assert "| claude-sonnet-5-5 | original | 2 | 2 | 0 | 1 | 1 | 0% | Writing ×2, Repetitive ×1 |" in out

    def test_an_episode_without_a_logged_outcome_is_its_own_row(self):
        out = self._table({"2026-10-12": _episode("2026-10-12", "keep")})
        assert "| claude-sonnet-5-5 | not recorded | 1 | 1 | 1 | 0 | 0 | 100% | — |" in out

    def test_unrated_episodes_count_and_unmatched_verdicts_are_named(self):
        out = self._table({"2026-10-15": _episode("2026-10-15", "keep"),
                           "2026-09-01": _episode("2026-09-01", "meh")})
        assert "Rated 1 of 5 episodes with citations in the window." in out
        assert "2026-09-01" in out.split("not in the table")[1].splitlines()[0]
        # An arm with nothing rated still appears, so a missing rating is visible.
        assert "| claude-sonnet-5-5 | original | 2 | 0 | 0 | 0 | 0 | — | — |" in out

    def test_arms_are_ordered_batch_realtime_skipped_original_then_unrecorded(self):
        verdicts = {d: _episode(d, "keep") for d in MODELS}
        rows = [l.split("|")[2].strip() for l in self._table(verdicts).splitlines()
                if l.startswith("| claude-")]
        assert rows == ["batch", "realtime", "original", "not recorded"]

    def test_no_verdicts_says_so_and_where_to_rate(self):
        out = self._table({})
        assert "No episode verdicts in the last 28 days" in out and "review.html" in out
        assert "|" not in out

    def test_a_partial_read_says_it_is_partial(self):
        out = self._table({"2026-10-15": _episode("2026-10-15", "keep")}, problem="HTTPError reading feedback/2026-10-10.json")
        assert "could not be read in full" in out and "partial" in out

    def test_the_small_sample_caveat_matches_the_arithmetic(self):
        """6 keeps against 12 of 14 each is p = 0.046 (two-sided Fisher); 7 against 11 is 0.24."""
        from math import comb

        def fisher(a, b, n=14):
            k, total = a + b, 2 * n

            def p(x):
                return comb(n, x) * comb(n, k - x) / comb(total, k)
            return sum(p(x) for x in range(max(0, k - n), min(n, k) + 1) if p(x) <= p(a) + 1e-12)
        assert round(fisher(6, 12), 2) == 0.05 and fisher(7, 11) > 0.2
        assert "6 keeps against 12 is about p = 0.05; 7 against 11 is not" in self._table({"2026-10-15": _episode("2026-10-15", "keep")})


class TestLoadEpisodeModels:
    def test_reads_models_from_the_citations_in_the_window(self, tmp_path, monkeypatch):
        def write(name, when, models):
            (tmp_path / name).write_text(json.dumps({"episode": {"date": when, "models": models}}))
        write("citations_2026-10-19_x.json", "2026-10-19", {"review": "m", "polish": "batch"})
        write("citations_2026-08-01_old.json", "2026-08-01", {"review": "m"})          # outside the window
        (tmp_path / "citations_2026-10-18_bad.json").write_text("{not json")             # skipped, not fatal
        monkeypatch.setattr(rs, "SCRIPTS_DIR", tmp_path)

        assert rs.load_episode_models(TODAY) == {"2026-10-19": {"review": "m", "polish": "batch"}}


class TestBuildVerdictSection:
    def test_a_failure_becomes_a_line_not_an_exception(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("x")
        monkeypatch.setattr(rs, "fetch_episode_verdicts", boom)
        out = rs.build_verdict_section(TODAY)
        assert "could not be built (RuntimeError)" in out

    def test_main_appends_it_to_the_saved_report(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(rs, "run_review", lambda days: "MODEL REPORT")
        monkeypatch.setattr(rs, "build_verdict_section", lambda: "## Listener verdicts by polish arm\n\nTABLE\n")
        monkeypatch.setattr(sys, "argv", ["review_scripts.py", "--output", "file"])
        rs.main()
        saved = next((tmp_path / "reviews").glob("review_*.md")).read_text()
        assert saved.index("MODEL REPORT") < saved.index("## Listener verdicts by polish arm")
