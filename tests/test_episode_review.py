"""Tests for episode_review.py, against a slice of the real 2026-08-19 job log.

The parser reads the pipeline's own stdout, which moves whenever a print
statement does. These tests pin the extractions that the review's prose depends
on, and — more importantly — pin the two behaviours that protect it from
publishing something false: echoed workflow source must never be read as an
emitted warning, and a missing line must never raise.
"""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import episode_review

FIXTURE = Path(__file__).parent / "fixtures" / "run_2026-08-19.log"


@pytest.fixture(scope="module")
def facts():
    return episode_review.scan_log(FIXTURE.read_text("utf-8"))


class TestScanLog:
    def test_theme_override_keeps_both_names(self, facts):
        # The feed can rename the day; the review needs to be able to say so.
        assert facts["theme_configured"] == "Gear, Gadgets & Practical Tech"
        assert facts["theme_feed"] == "Repair Culture & Practical Tech"

    def test_focus_and_fallback(self, facts):
        assert facts["focus"] == [1, 4, "Maker & Repair"]
        assert "only 1 article(s) matched focus" in facts["focus_fallback"]

    def test_curation_numbers(self, facts):
        assert facts["articles_loaded"] == 83
        assert facts["dedup_filtered"] == 77
        assert facts["focus_routing"] == [2, 15]
        assert facts["roundup_pool"][0] == 15
        assert facts["roundup_dropped"] == 46

    def test_script_and_quality(self, facts):
        assert facts["short_script"] == [3092, 3400]
        assert facts["quality"] == [1, 1.16, 3068]
        assert facts["citation_alignment"] == [5, 15, 3, 3]

    def test_degradation_is_captured_with_its_reason(self, facts):
        name, reason = facts["degradations"][0]
        assert name == "render/gemini-canary"
        assert "OpenAI" in reason

    def test_repeated_warnings_collapse_to_a_count(self, facts):
        # Nine short segments is one observation, not nine paragraphs.
        assert facts["tts_short_segments"] == 9
        assert facts["tts_retry_failed"] == 9

    def test_multi_extractions(self, facts):
        assert len(facts["canary_failures"]) == 2
        assert len(facts["brave_failures"]) == 3
        assert len(facts["clusters"]) == 3


class TestFalsehoodGuards:
    def test_echoed_workflow_source_is_not_a_warning(self, facts):
        """The runner prints each step's script before running it, and that
        source contains warning strings for failures that never happened."""
        blob = json.dumps(facts)
        assert "Anthropic usage limit reached" not in blob
        assert "Upstream feed had no usable articles" not in blob
        assert facts["other_warnings"] == []

    def test_unrecognised_warning_still_reaches_the_review(self):
        # A new failure mode has no regex until it has happened once.
        log = "⚠️  R2 bucket credential missing — site not synced\n"
        assert episode_review.scan_log(log)["other_warnings"] == [
            "⚠️  R2 bucket credential missing — site not synced"]

    def test_empty_log_does_not_raise(self):
        assert episode_review.scan_log("")["other_warnings"] == []

    def test_partial_log_yields_partial_facts(self):
        facts = episode_review.scan_log("✅ Loaded 40 articles from podcast feed\n")
        assert facts["articles_loaded"] == 40
        assert "quality" not in facts


class TestTriggerLabels:
    @pytest.mark.parametrize("created,event,expected,late", [
        # The Worker's ladder: dispatches on the minute.
        ("2026-09-23T08:05:09Z", "workflow_dispatch",
         "Primary (1:05 AM Pacific) — Cloudflare scheduler", 0),
        ("2026-09-23T10:06:40Z", "workflow_dispatch",
         "Fallback 2 (3:05 AM Pacific) — Cloudflare scheduler", 1),
        # The GitHub backstop, fired late — 2026-09-03's "291 minutes late".
        ("2026-09-03T15:56:00Z", "schedule", "Backstop (4:05 AM Pacific) — GitHub cron", 291),
        ("2026-09-03T11:05:00Z", "schedule", "Backstop (4:05 AM Pacific) — GitHub cron", 0),
        # A person, with no schedule to be late for.
        ("2026-09-23T14:42:00Z", "workflow_dispatch", "Manual (workflow_dispatch)", None),
        ("2026-09-23T08:48:00Z", "workflow_dispatch", "Manual (workflow_dispatch)", None),
    ])
    def test_run_is_matched_to_its_trigger_with_drift(self, created, event, expected, late):
        label, minutes = episode_review._trigger_label(episode_review._parse(created), event)
        assert (label, minutes) == (expected, late)

    def test_the_review_s_own_run_is_not_an_unfinished_trigger(self, monkeypatch):
        """Every review to 2026-09-23 ended on its own run, "in_progress"."""
        monkeypatch.setenv("GITHUB_RUN_ID", "42")
        runs = episode_review.summarize_runs([
            {"id": 41, "event": "workflow_dispatch", "created_at": "2026-09-23T08:05:05Z",
             "status": "completed", "conclusion": "success"},
            {"id": 42, "event": "schedule", "created_at": "2026-09-23T11:40:00Z",
             "status": "in_progress", "conclusion": None},
        ])
        assert runs[0]["status"] == "completed"
        assert runs[1]["status"] == "running this review"
        assert runs[1]["trigger"].endswith("this review's own run")


class TestRendering:
    def test_numbers_table_omits_absent_facts(self):
        html = episode_review.render_numbers_table({"articles_loaded": 83})
        assert "83" in html
        assert "Citations matched" not in html

    def test_numbers_table_is_empty_without_facts(self):
        assert episode_review.render_numbers_table({}) == ""

    def test_run_table_omitted_when_no_runs(self):
        assert episode_review.render_run_table([]) == ""

    def test_feed_is_valid_rss(self, tmp_path):
        index = [{
            "date": "2026-08-19",
            "title": "Episode Review — Cariboo Signals, August 19, 2026",
            "url": "https://example.invalid/podcasts/reviews/episode-review-2026-08-19.html",
            "content_html": "<p>Body &amp; more</p>",
            "published": "2026-08-19T13:48:21+00:00",
        }]
        root = ET.fromstring(episode_review.build_feed(index))
        items = root.findall(".//item")
        assert len(items) == 1
        assert items[0].find("title").text.startswith("Episode Review")
        assert items[0].find("pubDate").text == "Wed, 19 Aug 2026 13:48:21 +0000"

    def test_page_escapes_the_title(self):
        _title, _content, page = episode_review.build_review(
            "2026-08-19", [], {"theme_feed": "Repair & Practical Tech"}, "")
        assert "Repair &amp; Practical Tech" in page
        assert "<h1>Episode Review — Cariboo Signals, August 19, 2026</h1>" in page


class TestIndex:
    def test_rerun_replaces_the_same_date(self, tmp_path, monkeypatch):
        # A re-render must not append a second review for one day.
        monkeypatch.setattr(episode_review, "REVIEWS_DIR", tmp_path)
        monkeypatch.setattr(episode_review, "INDEX_FILE", tmp_path / "index.json")
        episode_review.update_index("2026-08-19", "First", "<p>one</p>")
        index = episode_review.update_index("2026-08-19", "Second", "<p>two</p>")
        assert len(index) == 1
        assert index[0]["title"] == "Second"

    def test_index_is_capped_and_newest_first(self, tmp_path, monkeypatch):
        monkeypatch.setattr(episode_review, "REVIEWS_DIR", tmp_path)
        monkeypatch.setattr(episode_review, "INDEX_FILE", tmp_path / "index.json")
        monkeypatch.setattr(episode_review, "FEED_LIMIT", 3)
        for day in range(1, 6):
            index = episode_review.update_index(f"2026-08-0{day}", f"Day {day}", "<p></p>")
        assert [e["date"] for e in index] == ["2026-08-05", "2026-08-04", "2026-08-03"]


class TestLabelFacts:
    """The model only ever sees these labels — a bare positional list is a
    guessing game it lost three days running (5/15 news, 1/3 deep-dive
    published as four scores out of ten or a hundred)."""

    def test_positional_groups_are_named(self, facts):
        labelled = episode_review.label_facts(facts)
        assert labelled["citation_alignment"] == {
            "roundup_citations_matched": facts["citation_alignment"][0],
            "roundup_citations_total": facts["citation_alignment"][1],
            "deep_dive_citations_matched": facts["citation_alignment"][2],
            "deep_dive_citations_total": facts["citation_alignment"][3],
        }

    def test_repeating_facts_are_named_per_item(self):
        labelled = episode_review.label_facts(
            {"brave_failures": [["meshtastic 2.8", "400 Client Error"]]})
        assert labelled["brave_failures"] == [
            {"query": "meshtastic 2.8", "error": "400 Client Error"}]

    def test_counts_carry_what_they_mean(self):
        labelled = episode_review.label_facts({"tts_retry_failed": 8})
        assert labelled["tts_retry_failed"]["value"] == 8
        # The count says nothing was dropped; the review said the opposite.
        assert "nothing is dropped" in labelled["tts_retry_failed"]["means"]

    def test_single_group_and_unknown_facts_pass_through(self):
        facts = {"audio_minutes": 20.2, "other_warnings": ["⚠️ something new"]}
        assert episode_review.label_facts(facts) == facts

    def test_every_multi_group_extraction_has_names(self):
        """A new capture group without a name is a new guess for the model."""
        import re

        patterns = {k: v[0] for k, v in episode_review._SINGLE.items()}
        patterns.update(episode_review._MULTI)
        for key, pattern in patterns.items():
            groups = re.compile(pattern).groups
            names = episode_review._FIELDS.get(key)
            if groups > 1:
                assert names and len(names) == groups, f"{key} has {groups} unnamed groups"
            else:
                assert names is None, f"{key} takes one group but names {names}"


class TestNarrativeScrub:
    """The narrative is injected as raw HTML into a published page."""

    @pytest.mark.parametrize("dangerous", [
        "<p>ok</p><script>alert(1)</script>",
        "<p onclick=\"steal()\">ok</p>",
        "<iframe src='evil'></iframe><p>ok</p>",
        "<object data='x'></object><p>ok</p>",
    ])
    def test_executable_content_is_removed(self, dangerous):
        cleaned = episode_review._scrub_html(dangerous)
        assert "<p>ok</p>" in cleaned
        for token in ("script", "onclick", "iframe", "object"):
            assert token not in cleaned.lower()

    def test_prose_markup_survives(self):
        fragment = "<h3>Head</h3>\n<p>Body <em>with</em> <code>code</code></p>"
        assert episode_review._scrub_html(fragment) == fragment


# ---------------------------------------------------------------------------
# Roadmap distillation
# ---------------------------------------------------------------------------

BEGIN, END = episode_review.SECTION_BEGIN, episode_review.SECTION_END

ROADMAP = f"""# Roadmap

## From the daily reviews

Prose a human wrote, outside the markers.

{BEGIN}

- [ ] **The first thing is broken.** Some evidence about it, and what would
      close it.
- [x] **The second thing was broken.** Evidence, since fixed.

{END}

## Short-term
- [ ] Something else entirely
"""


@pytest.fixture
def roadmap(tmp_path):
    """ROADMAP.md in the tmp dir the autouse fixture already points at."""
    episode_review.ROADMAP_FILE.write_text(ROADMAP, encoding="utf-8")
    return episode_review.ROADMAP_FILE


def _finding(id_="thing-is-broken", title="A third thing is broken.", detail="Evidence.",
             signal="other"):
    return {"id": id_, "title": title, "detail": detail, "signal": signal}


def _ledger(*findings, dates=("2026-08-20",)):
    ledger = {"items": []}
    for date in dates:
        episode_review.merge_findings(ledger, list(findings), date)
    return ledger


class TestParseSection:
    def test_items_and_their_checkboxes(self, roadmap):
        items = episode_review.parse_section(roadmap.read_text("utf-8"))
        assert [i["title"] for i in items] == ["The first thing is broken.",
                                              "The second thing was broken."]
        assert [i["done"] for i in items] == [False, True]

    def test_wrapped_detail_is_rejoined(self, roadmap):
        first = episode_review.parse_section(roadmap.read_text("utf-8"))[0]
        assert first["detail"] == "Some evidence about it, and what would close it."

    def test_text_outside_the_markers_is_not_an_item(self, roadmap):
        titles = [i["title"] for i in episode_review.parse_section(roadmap.read_text("utf-8"))]
        assert "Something else entirely" not in " ".join(titles)

    def test_no_markers_is_no_items_rather_than_an_error(self):
        assert episode_review.parse_section("# Roadmap\n- [ ] **A.** B\n") == []


class TestApplySection:
    def test_only_the_managed_block_moves(self, roadmap):
        text = roadmap.read_text("utf-8")
        out = episode_review.apply_section(text, f"{BEGIN}\nreplaced\n{END}")
        assert "Prose a human wrote, outside the markers." in out
        assert "## Short-term" in out and "Something else entirely" in out
        assert "The first thing is broken" not in out

    def test_a_file_without_markers_is_left_alone(self):
        text = "# Roadmap\n\n- [ ] Untouched\n"
        assert episode_review.apply_section(text, "anything") == text


class TestSeedLedger:
    """The section was hand-written before this existed. Adopting it is what
    makes the first automated run an edit rather than a replacement."""

    def test_hand_written_items_are_adopted_in_file_order(self, roadmap):
        ledger = episode_review.seed_ledger({"items": []}, roadmap.read_text("utf-8"), "2026-08-23")
        assert [i["title"] for i in ledger["items"]] == ["The first thing is broken.",
                                                        "The second thing was broken."]
        assert all(i["source"] == "manual" for i in ledger["items"])

    def test_a_checked_hand_written_item_is_adopted_closed(self, roadmap):
        ledger = episode_review.seed_ledger({"items": []}, roadmap.read_text("utf-8"), "2026-08-23")
        assert ledger["items"][1]["status"] == "done"

    def test_seeding_twice_does_not_duplicate(self, roadmap):
        text = roadmap.read_text("utf-8")
        ledger = episode_review.seed_ledger({"items": []}, text, "2026-08-23")
        episode_review.seed_ledger(ledger, text, "2026-08-24")
        assert len(ledger["items"]) == 2

    def test_the_render_reproduces_what_it_adopted(self, roadmap):
        """Round trip: seeding then rendering must not rewrite a human's item."""
        text = roadmap.read_text("utf-8")
        ledger = episode_review.seed_ledger({"items": []}, text, "2026-08-23")
        out = episode_review.apply_section(
            text, episode_review.render_section(ledger["items"]))
        again = episode_review.parse_section(out)
        assert [i["title"] for i in again] == ["The first thing is broken."]
        assert again[0]["detail"] == "Some evidence about it, and what would close it."


class TestMergeFindings:
    """One bad night is an incident; the same bad night twice is a roadmap item."""

    def test_a_first_sighting_is_not_written_down(self):
        ledger = _ledger(_finding())
        assert ledger["items"][0]["status"] == "pending"
        assert "third thing" not in episode_review.render_section(ledger["items"])

    def test_the_threshold_sighting_promotes_it(self):
        ledger = _ledger(_finding(), dates=("2026-08-20", "2026-08-21"))
        item = ledger["items"][0]
        assert item["status"] == "open" and item["occurrences"] == 2
        assert "A third thing is broken." in episode_review.render_section(
            ledger["items"])

    def test_rerunning_one_date_is_not_a_recurrence(self):
        """--date re-runs a past review; that must not count as it happening twice."""
        ledger = _ledger(_finding(), dates=("2026-08-20", "2026-08-20"))
        assert ledger["items"][0]["occurrences"] == 1
        assert ledger["items"][0]["status"] == "pending"

    def test_a_drifted_id_is_matched_on_its_title(self):
        """The id is the model's, so it drifts; a close title is the same finding."""
        ledger = _ledger(_finding())
        episode_review.merge_findings(
            ledger, [_finding(id_="the-third-thing-broke")], "2026-08-21")
        assert len(ledger["items"]) == 1
        assert ledger["items"][0]["occurrences"] == 2

    def test_an_unrelated_finding_gets_its_own_item(self):
        ledger = _ledger(_finding())
        episode_review.merge_findings(
            ledger, [_finding(id_="tts-silence", title="Silent takes reach the mix.")],
            "2026-08-21")
        assert len(ledger["items"]) == 2

    def test_the_first_wording_is_kept(self):
        """A detail rewritten nightly is a daily diff on a file nobody asked to change."""
        ledger = _ledger(_finding(detail="The original evidence."))
        episode_review.merge_findings(ledger, [_finding(detail="Reworded overnight.")],
                                      "2026-08-21")
        assert ledger["items"][0]["detail"] == "The original evidence."

    def test_the_findings_per_run_are_capped(self):
        ledger = {"items": []}
        episode_review.merge_findings(
            ledger, [_finding(id_=f"f{n}", title=t) for n, t in enumerate(
                ["Silent takes reach the mix.", "The canary pins OpenAI too early.",
                 "Brave returns two payload shapes.", "Citations do not match the script.",
                 "Held articles never release.", "The roundup cap ignores bonus picks."])],
            "2026-08-20")
        assert len(ledger["items"]) == episode_review.ROADMAP_MAX_FINDINGS


class TestHarvestChecked:
    """The file is where a human answers, so it is read before it is written."""

    def test_a_checked_box_closes_the_item(self, roadmap):
        text = roadmap.read_text("utf-8")
        ledger = episode_review.seed_ledger({"items": []}, text, "2026-08-23")
        ledger["items"][1]["status"] = "open"
        assert episode_review.harvest_checked(ledger, text) == ["The second thing was broken."]
        assert ledger["items"][1]["status"] == "done"

    def test_a_closed_item_leaves_the_section(self, roadmap):
        text = roadmap.read_text("utf-8")
        ledger = episode_review.seed_ledger({"items": []}, text, "2026-08-23")
        rendered = episode_review.render_section(ledger["items"])
        assert "The second thing was broken." not in rendered

    def test_tomorrow_s_review_cannot_reopen_it_on_one_mention(self):
        ledger = _ledger(_finding(), dates=("2026-08-20", "2026-08-21"))
        ledger["items"][0]["status"], ledger["items"][0]["occurrences"] = "done", 0
        episode_review.merge_findings(ledger, [_finding()], "2026-08-22")
        assert ledger["items"][0]["status"] == "done"

    def test_but_a_problem_that_is_genuinely_back_returns(self):
        ledger = _ledger(_finding(), dates=("2026-08-20", "2026-08-21"))
        ledger["items"][0]["status"], ledger["items"][0]["occurrences"] = "done", 0
        episode_review.merge_findings(ledger, [_finding()], "2026-08-22")
        episode_review.merge_findings(ledger, [_finding()], "2026-08-23")
        assert ledger["items"][0]["status"] == "open"


class TestRetireStale:
    def test_a_tool_item_the_reviews_dropped_retires(self):
        ledger = _ledger(_finding(), dates=("2026-08-01", "2026-08-02"))
        assert episode_review.retire_stale(ledger, "2026-08-24") == ["A third thing is broken."]
        assert "A third thing" not in episode_review.render_section(ledger["items"])

    def test_a_quiet_week_is_not_a_fix(self):
        ledger = _ledger(_finding(), dates=("2026-08-16", "2026-08-17"))
        assert episode_review.retire_stale(ledger, "2026-08-24") == []

    def test_a_human_s_item_is_never_retired(self, roadmap):
        """Only a human closes what a human wrote."""
        ledger = episode_review.seed_ledger({"items": []}, roadmap.read_text("utf-8"), "2026-01-01")
        assert episode_review.retire_stale(ledger, "2026-08-24", {}) == []
        assert ledger["items"][0]["status"] == "open"

    def test_a_single_sighting_does_not_wait_for_ever(self):
        """Twenty pending items from August were still in the prompt in late September."""
        ledger = _ledger(_finding(), dates=("2026-08-01",))
        assert episode_review.retire_stale(ledger, "2026-08-24") == ["A third thing is broken."]

    def test_a_retired_item_needs_the_full_count_to_return(self):
        ledger = _ledger(_finding(), dates=("2026-08-01", "2026-08-02"))
        episode_review.retire_stale(ledger, "2026-08-24")
        episode_review.merge_findings(ledger, [_finding()], "2026-08-25")
        assert ledger["items"][0]["status"] == "retired"
        episode_review.merge_findings(ledger, [_finding()], "2026-08-26")
        assert ledger["items"][0]["status"] == "open"


BODIES = "degraded:script/bodies"


class TestSignals:
    """The dedup key and the closing condition, both read off the facts."""

    def test_the_fixture_run_s_signals(self, facts):
        signals = episode_review.run_signals(facts)
        assert all(k.startswith(("degraded:", "short-script", "citations:", "ai-tells",
                                 "voice-ratio", "run-failed")) for k in signals)

    def test_each_kind_of_signal(self):
        signals = episode_review.run_signals({
            "degradations": [["script/bodies", "Brave body-backfill budget spent (12 calls)"],
                             ["script/bodies", "again"]],
            "short_script": [2192, 3400],
            "citation_alignment": [12, 15, 1, 3],
            "quality": [2, 1.4, 3300],
            "runs": [{"trigger": "Primary", "conclusion": "failure"}],
        })
        assert set(signals) == {BODIES, "short-script", "citations:deep-dive",
                                "ai-tells-shipped", "voice-ratio", "run-failed"}
        assert "12 calls" in signals[BODIES]

    def test_metrics_inside_their_lines_are_not_signals(self):
        signals = episode_review.run_signals({
            "citation_alignment": [12, 15, 2, 3], "quality": [0, 1.13, 3300]})
        assert signals == {}

    def test_differently_worded_findings_on_one_signal_are_one_item(self):
        """The ledger held three Brave-budget items on 2026-09-23, one per wording."""
        ledger = {"items": []}
        episode_review.merge_findings(ledger, [_finding(
            id_="brave-body-budget-hit-55-article-drop",
            title="Brave body-backfill exhaustion dropped 55 roundup articles.",
            signal=BODIES)], "2026-09-19")
        episode_review.merge_findings(ledger, [_finding(
            id_="brave-body-backfill-budget-exhaustion-pattern",
            title="Brave body-backfill budget exhaustion is recurring and requires intervention.",
            signal=BODIES)], "2026-09-20")
        assert len(ledger["items"]) == 1
        assert ledger["items"][0]["status"] == "open"
        assert ledger["items"][0]["signal"] == BODIES

    def test_a_close_title_under_another_signal_is_another_item(self):
        ledger = _ledger(_finding(signal=BODIES))
        episode_review.merge_findings(ledger, [_finding(signal="short-script")], "2026-08-21")
        assert [i["signal"] for i in ledger["items"]] == [BODIES, "short-script"]

    def test_an_item_from_before_signals_adopts_one(self):
        ledger = _ledger(_finding())
        episode_review.merge_findings(ledger, [_finding(signal=BODIES)], "2026-08-21")
        assert len(ledger["items"]) == 1 and ledger["items"][0]["signal"] == BODIES

    def test_an_item_closes_when_its_signal_goes_quiet(self):
        ledger = _ledger(_finding(signal=BODIES), dates=("2026-09-19", "2026-09-20"))
        assert episode_review.retire_stale(ledger, "2026-09-22", {}) == []
        assert episode_review.retire_stale(ledger, "2026-09-23", {}) == [
            "A third thing is broken."]

    def test_a_present_signal_keeps_its_item_open_without_a_finding(self):
        ledger = _ledger(_finding(signal=BODIES), dates=("2026-09-01", "2026-09-02"))
        for day in ("2026-09-10", "2026-09-20", "2026-09-30"):
            assert episode_review.retire_stale(ledger, day, {BODIES: "spent"}) == []
        assert ledger["items"][0]["status"] == "open"
        assert ledger["items"][0]["signal_seen"] == "2026-09-30"

    def test_no_log_is_not_evidence_of_a_fix(self):
        ledger = _ledger(_finding(signal=BODIES), dates=("2026-09-01", "2026-09-02"))
        assert episode_review.retire_stale(ledger, "2026-09-30", None) == []
        assert ledger["items"][0]["status"] == "open"


class TestDistillRoadmap:
    """A surface on top of the review; it must never cost the review."""

    def test_a_run_with_no_findings_leaves_the_file_alone(self, roadmap, monkeypatch):
        monkeypatch.setattr(episode_review, "propose_findings", lambda *a, **k: [])
        before = roadmap.read_text("utf-8")
        episode_review.distill_roadmap("2026-08-24", {}, "")
        # The seed pass rewrites the block once, in place; nothing is lost.
        after = roadmap.read_text("utf-8")
        assert "Prose a human wrote, outside the markers." in after
        assert "The first thing is broken." in after
        assert "Something else entirely" in before and "Something else entirely" in after

    def test_running_twice_settles(self, roadmap, monkeypatch):
        monkeypatch.setattr(episode_review, "propose_findings", lambda *a, **k: [])
        episode_review.distill_roadmap("2026-08-24", {}, "")
        settled = roadmap.read_text("utf-8")
        assert episode_review.distill_roadmap("2026-08-25", {}, "") is False
        assert roadmap.read_text("utf-8") == settled

    def test_a_file_without_markers_is_not_touched(self, monkeypatch):
        episode_review.ROADMAP_FILE.write_text("# Roadmap\n\n- [ ] Untouched\n", encoding="utf-8")
        monkeypatch.setattr(episode_review, "propose_findings", lambda *a, **k: [_finding()])
        assert episode_review.distill_roadmap("2026-08-24", {}, "") is False
        assert episode_review.ROADMAP_FILE.read_text("utf-8") == "# Roadmap\n\n- [ ] Untouched\n"

    def test_a_missing_roadmap_is_not_an_error(self):
        assert episode_review.distill_roadmap("2026-08-24", {}, "") is False

    def test_a_corrupt_ledger_does_not_stop_the_distillation(self, roadmap, monkeypatch):
        episode_review.LEDGER_FILE.write_text("{truncated", encoding="utf-8")
        monkeypatch.setattr(episode_review, "propose_findings", lambda *a, **k: [])
        assert episode_review.distill_roadmap("2026-08-24", {}, "") is True

    def test_a_failed_call_publishes_nothing_new(self, roadmap, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("no api key")

        monkeypatch.setattr(episode_review, "propose_findings", boom)
        with pytest.raises(RuntimeError):
            episode_review.distill_roadmap("2026-08-24", {}, "")
        # main() is where that is swallowed, and the review is already on disk.
        assert "The first thing is broken." in roadmap.read_text("utf-8")


class TestFindingSchema:
    def test_the_reply_is_constrained_server_side(self):
        """The strip-the-fences-and-hope pattern is what this replaced."""
        schema = episode_review._FINDING_SCHEMA
        item = schema["properties"]["findings"]["items"]
        assert set(item["required"]) == {"id", "title", "detail", "signal"}
        assert all(p.get("description") for p in item["properties"].values())

    def test_the_signal_is_pinned_to_today_s(self):
        schema = episode_review._finding_schema({BODIES: "x", "short-script": "y"})
        signal = schema["properties"]["findings"]["items"]["properties"]["signal"]
        assert signal["enum"] == [BODIES, "short-script", "other"]
        assert "enum" not in episode_review._FINDING_SCHEMA[
            "properties"]["findings"]["items"]["properties"]["signal"]

    def test_both_object_levels_close_themselves(self):
        """The API refuses an object schema that does not, and the refusal is a
        400 at the call site that `distill_roadmap` swallows as a warning: this
        schema shipped without the inner one on 2026-08-24 and every night's
        distillation returned zero findings while the run stayed green."""
        from config_loader import json_output_config

        schema = json_output_config(episode_review._FINDING_SCHEMA)["format"]["schema"]
        assert schema["additionalProperties"] is False
        assert schema["properties"]["findings"]["items"]["additionalProperties"] is False

    def test_the_prompt_asks_for_zero_findings_as_the_normal_answer(self):
        """Without this the model invents work every night, which is the whole
        failure mode a distillation has."""
        from config_loader import load_prompts_config

        template = load_prompts_config()["roadmap_distill"]["template"]
        assert "empty list" in template.lower()
        for field in ("facts_json", "narrative", "open_items", "closed_items", "planned_items",
                      "signals", "max_findings", "min_occurrences", "tell_block"):
            assert "{" + field + "}" in template


class TestSkipIfReviewed:
    """Rung 3 and the backstop both carry the review job; one review a day."""

    def test_an_existing_review_is_left_alone(self, tmp_path, monkeypatch):
        monkeypatch.setattr(episode_review, "REVIEWS_DIR", tmp_path)
        (tmp_path / "episode-review-2026-09-23.html").write_text("done", encoding="utf-8")
        monkeypatch.setattr(episode_review, "_session",
                            lambda: pytest.fail("reviewed the day twice"))
        monkeypatch.setattr("sys.argv", ["episode_review.py", "--date", "2026-09-23",
                                         "--skip-if-reviewed"])
        assert episode_review.main() == 0
