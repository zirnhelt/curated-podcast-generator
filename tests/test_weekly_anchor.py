"""Tests for the weekly anchor question — selection, no-repetition, and framing.

State isolation comes from the autouse _isolate_anchor_state fixture in
conftest.py: select_anchor() writes on the first call of any week, so every test
here would otherwise rewrite live production state.
"""

from datetime import date, timedelta

import pytest

import weekly_anchor as wa


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeUsage:
    input_tokens = 10
    output_tokens = 20


class _FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeResponse:
    def __init__(self, text):
        self.content = [_FakeBlock(text)]
        self.usage = _FakeUsage()


class FakeClient:
    """Anthropic stand-in returning canned bodies in order; counts calls."""

    def __init__(self, *bodies):
        self._bodies = list(bodies)
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        if not self._bodies:
            raise RuntimeError("no canned response left")
        body = self._bodies.pop(0)
        if isinstance(body, Exception):
            raise body
        return _FakeResponse(body)


FRAMINGS_JSON = '{"0":"a","1":"b","2":"c","3":"d","4":"e","5":"f","6":"g"}'


@pytest.fixture
def pool(monkeypatch):
    """A deterministic pool replacing config/weekly_anchors.json.

    Deliberately larger than MIN_POOL_REMAINING: at or below that threshold
    select_anchor also fires a top-up, which is correct but would consume the
    fake client's canned framing response. TestTopUp covers that path directly.
    """
    entries = [
        {"id": f"id-{i}", "question": f"Q {i}?", "dimension": f"dim-{i}", "premise": f"p{i}"}
        for i in range(wa.MIN_POOL_REMAINING + 3)
    ]
    monkeypatch.setattr(wa, "load_weekly_anchors_config", lambda: {"pool": entries})
    return entries


# ---------------------------------------------------------------------------
# ISO week arithmetic
# ---------------------------------------------------------------------------

class TestWeekKeys:
    def test_monday_and_sunday_share_a_key(self):
        assert wa.iso_week_key(date(2026, 8, 17)) == wa.iso_week_key(date(2026, 8, 23))

    def test_next_monday_is_a_new_key(self):
        assert wa.iso_week_key(date(2026, 8, 24)) != wa.iso_week_key(date(2026, 8, 17))

    def test_seeded_pin_week_matches_the_target_monday(self):
        assert wa.iso_week_key(date(2026, 8, 17)) == "2026-W34"

    def test_weeks_between_counts_whole_weeks(self):
        assert wa._weeks_between("2026-W34", "2026-W38") == 4
        assert wa._weeks_between("2026-W34", "2026-W34") == 0

    def test_week_key_round_trips_across_a_year_boundary(self):
        for d in (date(2026, 1, 1), date(2026, 12, 31), date(2027, 1, 4)):
            key = wa.iso_week_key(d)
            assert wa.iso_week_key(wa._week_start(key)) == key


# ---------------------------------------------------------------------------
# Selection and idempotency
# ---------------------------------------------------------------------------

class TestSelectAnchor:
    def test_same_week_returns_same_anchor(self, pool):
        client = FakeClient(FRAMINGS_JSON)
        monday = wa.select_anchor(date(2026, 8, 17), client=client)
        thursday = wa.select_anchor(date(2026, 8, 20), client=client)
        sunday = wa.select_anchor(date(2026, 8, 23), client=client)
        assert monday["question"] == thursday["question"] == sunday["question"]
        # The whole point: later days in the week cost no API call at all.
        assert client.calls == 1

    def test_next_week_gets_a_different_anchor(self, pool):
        first = wa.select_anchor(date(2026, 8, 17), client=FakeClient(FRAMINGS_JSON))
        second = wa.select_anchor(date(2026, 8, 24), client=FakeClient(FRAMINGS_JSON))
        assert first["question"] != second["question"]

    def test_rerun_after_state_reload_is_stable(self, pool):
        first = wa.select_anchor(date(2026, 8, 17), client=FakeClient(FRAMINGS_JSON))
        # A separate process (the render stage) reads the same pinned record.
        again = wa.select_anchor(date(2026, 8, 19), client=None)
        assert again["id"] == first["id"]

    def test_pin_week_wins_over_pool_order(self, monkeypatch):
        monkeypatch.setattr(wa, "load_weekly_anchors_config", lambda: {"pool": [
            {"id": "ordinary", "question": "Q ordinary?", "dimension": "dim-a"},
            {"id": "pinned", "question": "Q pinned?", "dimension": "dim-b",
             "pin_week": "2026-W34"},
        ]})
        anchor = wa.select_anchor(date(2026, 8, 17), client=FakeClient(FRAMINGS_JSON))
        assert anchor["id"] == "pinned"

    def test_seeded_config_pins_the_noema_question_to_2026_w34(self):
        """The real config, not a fixture — week one must be what was asked for."""
        anchor = wa.select_anchor(date(2026, 8, 17), client=None)
        assert anchor["question"] == "Why is everyone in tech so sad?"
        assert anchor["dimension"] == "labour-meaning"

    def test_future_pin_is_not_taken_early(self, pool, monkeypatch):
        """Pinning is a statement about a week — pool order must not pre-empt it."""
        entries = list(pool) + [
            {"id": "later", "question": "Q later?", "dimension": "dim-later",
             "pin_week": "2026-W40"},
        ]
        monkeypatch.setattr(wa, "load_weekly_anchors_config", lambda: {"pool": entries})
        # Even when it is the only entry left, a future pin waits its week.
        used = [{"id": e["id"], "dimension": e["dimension"], "week": "2026-W20"}
                for e in pool]
        assert wa._pick(entries, used, "2026-W34") is None
        assert wa._pick(entries, used, "2026-W40")["id"] == "later"

    def test_overdue_pin_still_runs(self, monkeypatch):
        """Shipping a week late must not bury the question that was pinned."""
        monkeypatch.setattr(wa, "load_weekly_anchors_config", lambda: {"pool": [
            {"id": "overdue", "question": "Q overdue?", "dimension": "dim-a",
             "pin_week": "2026-W34"},
            {"id": "next", "question": "Q next?", "dimension": "dim-b"},
        ]})
        anchor = wa.select_anchor(date(2026, 8, 24), client=None)
        assert anchor["id"] == "overdue"

    def test_empty_pool_returns_none_rather_than_raising(self, monkeypatch):
        monkeypatch.setattr(wa, "load_weekly_anchors_config", lambda: {"pool": []})
        assert wa.select_anchor(date(2026, 8, 17), client=None) is None
        assert any("no eligible anchor" in d for d in wa.drain_degradations())


# ---------------------------------------------------------------------------
# No repetition — the guard that keeps this from getting stale
# ---------------------------------------------------------------------------

class TestNoRepetition:
    def test_used_id_is_never_reselected(self, pool):
        used = [{"id": "id-0", "dimension": "unrelated", "week": "2020-W01"}]
        eligible = wa._eligible(pool, used, "2026-W34")
        assert "id-0" not in [c["id"] for c in eligible]

    def test_dimension_inside_cooldown_is_skipped(self, pool):
        recent = wa.iso_week_key(date(2026, 8, 17) - timedelta(weeks=10))
        used = [{"id": "gone", "dimension": "dim-1", "week": recent}]
        eligible = wa._eligible(pool, used, "2026-W34")
        assert "id-1" not in [c["id"] for c in eligible]

    def test_dimension_past_cooldown_is_eligible_again(self, pool):
        old = wa.iso_week_key(date(2026, 8, 17) - timedelta(weeks=30))
        used = [{"id": "gone", "dimension": "dim-1", "week": old}]
        eligible = wa._eligible(pool, used, "2026-W34")
        assert "id-1" in [c["id"] for c in eligible]

    def test_consecutive_weeks_never_repeat_a_question_or_dimension(self):
        """Walk the real seeded pool for its full length.

        Length comes from the config, not a literal: a seed added to top the
        pool up must be walked too, or it is shipped untested.
        """
        from config_loader import load_weekly_anchors_config

        seen_q, seen_dim = set(), set()
        for i in range(len(load_weekly_anchors_config()["pool"])):
            monday = date(2026, 8, 17) + timedelta(weeks=i)
            anchor = wa.select_anchor(monday, client=None)
            assert anchor is not None, f"pool ran dry at week {i}"
            assert anchor["question"] not in seen_q
            assert anchor["dimension"] not in seen_dim
            seen_q.add(anchor["question"])
            seen_dim.add(anchor["dimension"])

    def test_seeded_pool_has_unique_ids_and_dimensions(self):
        from config_loader import load_weekly_anchors_config
        entries = load_weekly_anchors_config()["pool"]
        assert len({e["id"] for e in entries}) == len(entries)
        assert len({e["dimension"] for e in entries}) == len(entries)
        assert all(e.get("question") and e.get("premise") for e in entries)


# ---------------------------------------------------------------------------
# Pool top-up
# ---------------------------------------------------------------------------

class TestTopUp:
    def test_refill_fires_before_the_pool_empties(self, monkeypatch):
        """The threshold exists so a failed top-up costs a warning, not the week."""
        thin = [
            {"id": f"t{i}", "question": f"Q{i}?", "dimension": f"d{i}"}
            for i in range(wa.MIN_POOL_REMAINING - 1)
        ]
        monkeypatch.setattr(wa, "load_weekly_anchors_config", lambda: {"pool": thin})
        generated = '[{"id":"gen","question":"Qg?","dimension":"dg","premise":"p"}]'
        client = FakeClient(generated, FRAMINGS_JSON)
        anchor = wa.select_anchor(date(2026, 8, 17), client=client)
        # Still serves from the seeded pool this week — the refill is for later.
        assert anchor["id"] == "t0"
        assert "gen" in {e["id"] for e in wa.load_anchor_state()["generated"]}

    def test_healthy_pool_makes_no_top_up_call(self, pool):
        client = FakeClient(FRAMINGS_JSON)
        wa.select_anchor(date(2026, 8, 17), client=client)
        assert client.calls == 1  # framings only

    def test_exhausted_pool_triggers_generation(self, monkeypatch):
        monkeypatch.setattr(wa, "load_weekly_anchors_config", lambda: {"pool": []})
        generated = ('[{"id":"gen-1","question":"Q gen?","dimension":"dim-z",'
                     '"premise":"pz"}]')
        client = FakeClient(generated, FRAMINGS_JSON)
        anchor = wa.select_anchor(date(2026, 8, 17), client=client)
        assert anchor is not None
        assert anchor["id"] == "gen-1"
        assert anchor["origin"] == "generated"

    def test_generated_entries_persist_for_later_weeks(self, monkeypatch):
        monkeypatch.setattr(wa, "load_weekly_anchors_config", lambda: {"pool": []})
        generated = ('[{"id":"gen-1","question":"Q1?","dimension":"dim-y","premise":"p"},'
                     '{"id":"gen-2","question":"Q2?","dimension":"dim-z","premise":"p"}]')
        wa.select_anchor(date(2026, 8, 17), client=FakeClient(generated, FRAMINGS_JSON))
        state = wa.load_anchor_state()
        assert {e["id"] for e in state["generated"]} == {"gen-1", "gen-2"}

    def test_failed_top_up_degrades_instead_of_raising(self, monkeypatch):
        monkeypatch.setattr(wa, "load_weekly_anchors_config", lambda: {"pool": []})
        client = FakeClient(RuntimeError("api down"))
        assert wa.select_anchor(date(2026, 8, 17), client=client) is None
        details = wa.drain_degradations()
        assert any("top-up" in d for d in details)

    def test_malformed_generated_entries_are_dropped(self, monkeypatch):
        monkeypatch.setattr(wa, "load_weekly_anchors_config", lambda: {"pool": []})
        body = '[{"question":"no id"},{"id":"ok","question":"Q?","dimension":"d"}]'
        fresh = wa.top_up_pool(FakeClient(body), used=[])
        assert [e["id"] for e in fresh] == ["ok"]

    def test_top_up_prompt_carries_every_used_question_and_dimension(self, monkeypatch):
        captured = {}

        def _fake_call(client, prompt, max_tokens, log_usage=None):
            captured["prompt"] = prompt
            return []

        monkeypatch.setattr(wa, "_call_claude", _fake_call)
        wa.top_up_pool(object(), used=[
            {"question": "Spent one?", "dimension": "dim-a"},
            {"question": "Spent two?", "dimension": "dim-b"},
        ])
        assert "Spent one?" in captured["prompt"]
        assert "Spent two?" in captured["prompt"]
        assert "dim-a" in captured["prompt"] and "dim-b" in captured["prompt"]


# ---------------------------------------------------------------------------
# Framings
# ---------------------------------------------------------------------------

class TestFramings:
    def test_seven_framings_are_stored(self, pool):
        anchor = wa.select_anchor(date(2026, 8, 17), client=FakeClient(FRAMINGS_JSON))
        assert set(anchor["framings"]) == {"0", "1", "2", "3", "4", "5", "6"}

    def test_failed_framing_call_still_yields_an_anchor(self, pool):
        client = FakeClient(RuntimeError("api down"))
        anchor = wa.select_anchor(date(2026, 8, 17), client=client)
        assert anchor is not None and anchor["framings"] == {}
        assert any("framings" in d for d in wa.drain_degradations())

    def test_no_client_yields_an_anchor_without_framings(self, pool):
        anchor = wa.select_anchor(date(2026, 8, 17), client=None)
        assert anchor["question"] == "Q 0?"
        assert anchor["framings"] == {}

    def test_out_of_range_weekday_keys_are_discarded(self, pool):
        client = FakeClient('{"0":"a","9":"nope","x":"nope"}')
        anchor = wa.select_anchor(date(2026, 8, 17), client=client)
        assert set(anchor["framings"]) == {"0"}

    def test_fenced_json_is_tolerated(self, pool):
        client = FakeClient("```json\n" + FRAMINGS_JSON + "\n```")
        anchor = wa.select_anchor(date(2026, 8, 17), client=client)
        assert len(anchor["framings"]) == 7

    def test_framing_prompt_lists_all_seven_themes(self):
        block = wa._themes_block()
        assert block.count("\n") == 6
        assert "Monday" in block and "Sunday" in block


# ---------------------------------------------------------------------------
# The prompt surface — the inverse of the focus's never-announce rule
# ---------------------------------------------------------------------------

class TestPromptSurface:
    ANCHOR = {
        "question": "Why is everyone in tech so sad?",
        "premise": "An emotional recession has settled over knowledge work.",
        "framings": {"1": "What do ranchers know that knowledge workers don't?"},
    }

    def test_no_anchor_renders_nothing(self):
        assert wa.format_anchor_for_prompt(None, 1, "Working Lands & Industry") == ""
        assert wa.format_anchor_for_prompt({}, 1, "Working Lands & Industry") == ""

    def test_block_carries_question_theme_premise_and_framing(self):
        block = wa.format_anchor_for_prompt(self.ANCHOR, 1, "Working Lands & Industry")
        assert "Why is everyone in tech so sad?" in block
        assert "Working Lands & Industry" in block
        assert "emotional recession" in block
        assert "What do ranchers know" in block

    def test_missing_framing_omits_the_line_without_breaking(self):
        block = wa.format_anchor_for_prompt(self.ANCHOR, 5, "Cariboo Local Affairs")
        assert "TODAY'S ANGLE" not in block
        assert "Why is everyone in tech so sad?" in block

    def test_block_instructs_the_hosts_to_name_it(self):
        """Unlike the super-cycle focus, the anchor is spoken. Inverse of
        test_no_prompt_surface_names_the_rotation in test_super_cycles.py."""
        block = wa.format_anchor_for_prompt(self.ANCHOR, 1, "Working Lands & Industry").lower()
        assert "say the question out loud" in block

    def test_block_carries_the_drop_it_escape_hatch(self):
        """The single guard against seven days of manufactured connections."""
        block = wa.format_anchor_for_prompt(self.ANCHOR, 1, "Working Lands & Industry")
        assert "DOES NOT GENUINELY REACH THE QUESTION, DROP IT COMPLETELY" in block

    def test_block_is_padded_so_the_template_reads_the_same_either_way(self):
        block = wa.format_anchor_for_prompt(self.ANCHOR, 1, "Working Lands & Industry")
        assert block.startswith("\n") and block.endswith("\n")

    def test_braces_in_generated_text_do_not_break_formatting(self):
        anchor = dict(self.ANCHOR, framings={"1": "a {curly} brace and a {0}"})
        block = wa.format_anchor_for_prompt(anchor, 1, "Working Lands & Industry")
        assert "{curly}" in block


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------

class TestPruning:
    def test_stale_assigned_weeks_are_dropped_but_used_survives(self):
        state = {
            "assigned": {"2026-W01": {"id": "old"}, "2026-W33": {"id": "recent"}},
            "used": [{"id": "old", "dimension": "d", "week": "2026-W01"}],
            "generated": [],
        }
        pruned = wa._prune(state, "2026-W34")
        assert "2026-W01" not in pruned["assigned"]
        assert "2026-W33" in pruned["assigned"]
        # The no-repeat ledger has a much longer memory than the pin cache.
        assert len(pruned["used"]) == 1

    def test_used_entries_past_two_years_are_dropped(self):
        state = {"assigned": {}, "generated": [],
                 "used": [{"id": "ancient", "dimension": "d", "week": "2023-W01"}]}
        assert wa._prune(state, "2026-W34")["used"] == []


# ---------------------------------------------------------------------------
# Script header round-trip across the stage boundary
# ---------------------------------------------------------------------------

class TestScriptHeader:
    def _write(self, tmp_path, monkeypatch, **kwargs):
        import podcast_generator as pg
        monkeypatch.setattr(pg, "PODCASTS_DIR", tmp_path)
        return pg.save_script_to_file("**WELCOME**\n**RILEY:** Hi.\n", "Test Theme", **kwargs)

    def test_anchor_round_trips(self, tmp_path, monkeypatch):
        import podcast_generator as pg
        path = self._write(tmp_path, monkeypatch,
                           anchor_question="Why is everyone in tech so sad?")
        meta = pg.read_script_metadata(path)
        assert meta["anchor"] == "Why is everyone in tech so sad?"
        assert meta["theme"] == "Test Theme"

    def test_script_without_anchor_reads_back_none(self, tmp_path, monkeypatch):
        import podcast_generator as pg
        path = self._write(tmp_path, monkeypatch)
        assert pg.read_script_metadata(path)["anchor"] is None

    def test_multiline_question_is_collapsed_to_one_header_line(self, tmp_path, monkeypatch):
        import podcast_generator as pg
        path = self._write(tmp_path, monkeypatch, anchor_question="Why is\neveryone sad?")
        assert pg.read_script_metadata(path)["anchor"] == "Why is everyone sad?"
        # A stray newline must not truncate the rest of the header.
        assert pg.read_script_metadata(path)["theme"] == "Test Theme"

    def test_question_containing_a_colon_survives(self, tmp_path, monkeypatch):
        import podcast_generator as pg
        path = self._write(tmp_path, monkeypatch, anchor_question="One question: why?")
        assert pg.read_script_metadata(path)["anchor"] == "One question: why?"
