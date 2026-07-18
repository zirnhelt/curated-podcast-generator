"""Tests for super-cycle focus rotation, article holding, and repeat-topic guards."""

from datetime import date, timedelta

import pytest

import podcast_generator as pg
from config_loader import (
    load_super_cycles_config,
    get_focus_for_day,
    get_upcoming_focus_slots,
)


# ---------------------------------------------------------------------------
# Calendar-derived cycle index
# ---------------------------------------------------------------------------

class TestFocusForDay:
    def test_deterministic_for_same_date(self):
        d = date(2026, 7, 21)  # a Tuesday
        assert get_focus_for_day(1, d) == get_focus_for_day(1, d)

    def test_advances_one_slot_per_week(self):
        d = date(2026, 7, 21)
        cycles = load_super_cycles_config()
        n = len(cycles["1"]["cycle"])
        this_week = get_focus_for_day(1, d)
        next_week = get_focus_for_day(1, d + timedelta(days=7))
        assert next_week["index"] == (this_week["index"] + 1) % n

    def test_full_cycle_returns_to_same_focus(self):
        d = date(2026, 7, 21)
        focus = get_focus_for_day(1, d)
        again = get_focus_for_day(1, d + timedelta(weeks=focus["cycle_length"]))
        assert again["slug"] == focus["slug"]

    def test_saturday_has_no_cycle(self):
        assert get_focus_for_day(5, date(2026, 7, 18)) is None

    def test_friday_runs_three_week_cycle(self):
        focus = get_focus_for_day(4, date(2026, 7, 17))
        assert focus["cycle_length"] == 3

    def test_focus_carries_slug_name_keywords_lens(self):
        focus = get_focus_for_day(1, date(2026, 7, 21))
        assert focus["slug"] and focus["name"] and focus["keywords"] and focus["lens"]

    def test_upcoming_slots_exclude_today_and_saturday(self):
        d = date(2026, 7, 18)  # Saturday
        slots = get_upcoming_focus_slots(d, horizon_days=14)
        assert all(slot_date > d for slot_date, _, _ in slots)
        assert all(wd != 5 for _, wd, _ in slots)
        # 14 days minus the two uncycled Saturdays
        assert len(slots) == 12


# ---------------------------------------------------------------------------
# Focus-aware selection
# ---------------------------------------------------------------------------

def _article(title, url, kw=0, boosted=50, summary=""):
    return {
        "title": title,
        "url": url,
        "summary": summary,
        "_keyword_matches": kw,
        "_boosted_score": boosted,
    }


MINING_FOCUS = {
    "slug": "mining-energy",
    "name": "Mining & Energy",
    "keywords": ["mining", "mine", "copper", "gold", "exploration", "drilling", "tailings"],
    "lens": "Center the deep dive on mining and energy.",
}


class TestFocusAwareDeepDive:
    def test_focus_articles_win_deep_dive(self):
        articles = [
            _article("Timber supply review announced", "u1", kw=3, boosted=90),
            _article("Copper mine exploration drilling expands", "u2", kw=1, boosted=40),
            _article("Gold mine tailings upgrade approved", "u3", kw=1, boosted=40),
            _article("New mine drilling permits issued", "u4", kw=0, boosted=40),
            _article("Cattle prices hit record", "u5", kw=2, boosted=80),
        ]
        deep_dive, news = pg.select_deep_dive_from_feed(
            articles, "Working Lands & Industry", count=3, focus=MINING_FOCUS
        )
        assert {a["url"] for a in deep_dive} == {"u2", "u3", "u4"}
        assert {a["url"] for a in news} == {"u1", "u5"}

    def test_thin_focus_week_falls_back_to_theme(self):
        articles = [
            _article("Timber supply review announced", "u1", kw=3, boosted=90),
            _article("Copper mine exploration drilling expands", "u2", kw=1, boosted=40),
            _article("Cattle prices hit record", "u3", kw=2, boosted=80),
            _article("Sawmill reopens after retooling", "u4", kw=2, boosted=70),
        ]
        deep_dive, _ = pg.select_deep_dive_from_feed(
            articles, "Working Lands & Industry", count=3, focus=MINING_FOCUS
        )
        # Only one focus match (<3) — base theme keyword ranking applies
        assert deep_dive[0]["url"] == "u1"

    def test_no_focus_behaves_as_before(self):
        articles = [
            _article("Timber supply review announced", "u1", kw=3, boosted=90),
            _article("Cattle prices hit record", "u2", kw=2, boosted=80),
            _article("Unrelated celebrity news", "u3", kw=0, boosted=99),
        ]
        deep_dive, _ = pg.select_deep_dive_from_feed(
            articles, "Working Lands & Industry", count=2, focus=None
        )
        assert {a["url"] for a in deep_dive} == {"u1", "u2"}

    def test_theme_lens_appends_focus_lens(self):
        base = pg._build_theme_lens("Working Lands & Industry")
        with_focus = pg._build_theme_lens("Working Lands & Industry", focus=MINING_FOCUS)
        assert with_focus.startswith(base)
        assert MINING_FOCUS["lens"] in with_focus
        # Subtlety guardrail: focus steers curation but is never announced on air
        assert "never announce" in with_focus

    def test_no_prompt_surface_names_the_rotation(self):
        lens = pg._build_theme_lens("Working Lands & Industry", focus=MINING_FOCUS)
        for phrase in ("rotation", "this week's focus", "focus week", "super cycle"):
            assert phrase not in lens.lower()


# ---------------------------------------------------------------------------
# Article holding & aired-early ledger
# ---------------------------------------------------------------------------

def _find_saturday_before_focus(slug, max_weeks=6):
    """First Saturday whose 14-day lookahead contains the given focus slug."""
    d = date(2026, 7, 18)  # a Saturday
    for _ in range(max_weeks):
        for slot_date, _wd, focus in get_upcoming_focus_slots(d, horizon_days=14):
            if focus["slug"] == slug:
                return d, slot_date
        d += timedelta(days=7)
    raise AssertionError(f"no Saturday found ahead of focus {slug}")


@pytest.fixture
def holding_env(tmp_path, monkeypatch):
    monkeypatch.setattr(pg, "HOLDING_FILE", tmp_path / "article_holding.json")
    monkeypatch.setattr(pg, "load_recent_citations", lambda days=14: [])
    return tmp_path


def _filler_pool(n=20):
    return [
        _article(f"Williams Lake council briefs part {i}", f"filler-{i}", kw=2)
        for i in range(n)
    ]


class TestArticleHolding:
    def test_offtheme_nonurgent_article_held_for_focus_day(self, holding_env):
        saturday, mining_day = _find_saturday_before_focus("mining-energy")
        mining = _article(
            "Copper mine expansion clears exploration drilling permit",
            "mining-url", kw=0, boosted=50,
        )
        theme, bonus = pg.route_articles_for_focus(
            _filler_pool() + [mining], [], saturday, "Cariboo Local Affairs", None
        )
        assert all(a["url"] != "mining-url" for a in theme + bonus)
        holding = pg.load_memory(pg.HOLDING_FILE)
        entry = holding["mining-url"]
        assert entry["status"] == "held"
        assert entry["target_focus_slug"] == "mining-energy"
        assert entry["target_date"] == mining_day.isoformat()

    def test_urgent_offtheme_article_airs_in_bonus_with_ledger(self, holding_env):
        saturday = date(2026, 7, 18)
        cyber = _article(
            "Ransomware phishing scam warning after fraud reports",
            "cyber-url", kw=0, boosted=95,
        )
        theme, bonus = pg.route_articles_for_focus(
            _filler_pool() + [cyber], [], saturday, "Cariboo Local Affairs", None
        )
        assert any(a["url"] == "cyber-url" and a.get("_no_deep_dive") for a in bonus)
        assert all(a["url"] != "cyber-url" for a in theme)
        entry = pg.load_memory(pg.HOLDING_FILE)["cyber-url"]
        assert entry["status"] == "aired_early"
        assert entry["target_focus_slug"] == "digital-life-security"

    def test_ontheme_article_never_held(self, holding_env):
        saturday = date(2026, 7, 18)
        local = _article(
            "Williams Lake council approves mine reclamation budget",
            "local-url", kw=3, boosted=50,
        )
        theme, _ = pg.route_articles_for_focus(
            _filler_pool() + [local], [], saturday, "Cariboo Local Affairs", None
        )
        assert any(a["url"] == "local-url" for a in theme)
        assert "local-url" not in pg.load_memory(pg.HOLDING_FILE)

    def test_small_pool_never_shrunk_by_holding(self, holding_env):
        saturday, _ = _find_saturday_before_focus("mining-energy")
        mining = _article(
            "Copper mine expansion clears exploration drilling permit",
            "mining-url", kw=0, boosted=50,
        )
        theme, bonus = pg.route_articles_for_focus(
            _filler_pool(5) + [mining], [], saturday, "Cariboo Local Affairs", None
        )
        # Pool below roundup+deep-dive budget: article airs today instead
        assert any(a["url"] == "mining-url" for a in theme)
        assert "mining-url" not in pg.load_memory(pg.HOLDING_FILE)

    def test_release_on_target_day_flags_held_from(self, holding_env):
        target = date(2026, 7, 21)
        pg.save_memory(pg.HOLDING_FILE, {
            "mining-url": {
                "article": _article("Copper mine expansion", "mining-url"),
                "held_date": "2026-07-18",
                "target_date": target.isoformat(),
                "target_weekday": 1,
                "target_focus_slug": "mining-energy",
                "target_focus_name": "Mining & Energy",
                "status": "held",
            }
        })
        focus = get_focus_for_day(1, target)
        theme, _ = pg.route_articles_for_focus(
            _filler_pool(), [], target, "Working Lands & Industry", focus
        )
        released = [a for a in theme if a.get("url") == "mining-url"]
        assert released and released[0]["_held_from"] == "2026-07-18"
        # Same-day re-run releases again (idempotent), next day prunes
        theme2, _ = pg.route_articles_for_focus(
            _filler_pool(), [], target, "Working Lands & Industry", focus
        )
        assert any(a.get("url") == "mining-url" for a in theme2)
        pg._load_article_holding(target + timedelta(days=1))
        assert "mining-url" not in pg.load_memory(pg.HOLDING_FILE)

    def test_feed_copy_preferred_over_held_copy(self, holding_env):
        target = date(2026, 7, 21)
        pg.save_memory(pg.HOLDING_FILE, {
            "mining-url": {
                "article": _article("Copper mine expansion (stale copy)", "mining-url"),
                "held_date": "2026-07-18",
                "target_date": target.isoformat(),
                "target_focus_slug": "mining-energy",
                "status": "held",
            }
        })
        fresh = _article("Copper mine expansion (fresh copy)", "mining-url", kw=2)
        theme, _ = pg.route_articles_for_focus(
            _filler_pool() + [fresh], [], target, "Working Lands & Industry",
            get_focus_for_day(1, target),
        )
        copies = [a for a in theme if a.get("url") == "mining-url"]
        assert len(copies) == 1 and "_held_from" not in copies[0]

    def test_prune_drops_expired_holds(self, holding_env):
        today = date(2026, 7, 18)
        pg.save_memory(pg.HOLDING_FILE, {
            "stale": {
                "article": {}, "held_date": "2026-06-20",
                "target_date": "2026-06-24", "status": "held",
            },
            "future": {
                "article": {}, "held_date": today.isoformat(),
                "target_date": (today + timedelta(days=3)).isoformat(), "status": "held",
            },
        })
        holding = pg._load_article_holding(today)
        assert "stale" not in holding and "future" in holding


class TestFocusCallbacks:
    def test_callback_block_and_consumption(self, holding_env):
        pg.save_memory(pg.HOLDING_FILE, {
            "cyber-url": {
                "article": {"title": "Ransomware scam warning"},
                "held_date": "2026-07-18",
                "target_date": "2026-07-22",
                "target_focus_slug": "digital-life-security",
                "status": "aired_early",
            }
        })
        focus = {"slug": "digital-life-security", "name": "Digital Life & Security"}
        context, urls = pg.format_focus_callbacks_for_prompt(focus)
        assert "Ransomware scam warning" in context
        assert "call back" in context
        assert urls == ["cyber-url"]
        pg.consume_focus_callbacks(urls)
        assert pg.load_memory(pg.HOLDING_FILE) == {}

    def test_no_callbacks_for_other_focus(self, holding_env):
        pg.save_memory(pg.HOLDING_FILE, {
            "cyber-url": {
                "article": {"title": "Ransomware scam warning"},
                "held_date": "2026-07-18",
                "target_focus_slug": "digital-life-security",
                "status": "aired_early",
            }
        })
        context, urls = pg.format_focus_callbacks_for_prompt(MINING_FOCUS)
        assert context == "" and urls == []


# ---------------------------------------------------------------------------
# Repeat-topic acknowledgment & focus-aware memory
# ---------------------------------------------------------------------------

class TestPriorCoverage:
    def test_overlap_with_past_topic_flagged(self):
        deep_dive = [{"title": "Arts on the Fly permit dispute heads back to council"}]
        episode_memory = {
            "2026-07-11": {"date": "2026-07-11",
                           "topics": ["Arts on the Fly permit question"]},
        }
        context = pg.format_prior_coverage_for_prompt(deep_dive, episode_memory, {})
        assert "PRIOR COVERAGE ALERT" in context
        assert "2026-07-11" in context

    def test_overlap_with_past_debate_question_flagged(self):
        deep_dive = [{"title": "Sawmill closure raises timber supply fears"}]
        debate_memory = {
            "2026-07-07": {"date": "2026-07-07",
                           "central_question": "Can the timber supply survive another sawmill closure?"},
        }
        context = pg.format_prior_coverage_for_prompt(deep_dive, {}, debate_memory)
        assert "PRIOR COVERAGE ALERT" in context

    def test_no_overlap_no_block(self):
        deep_dive = [{"title": "Aurora forecast looks strong this weekend"}]
        episode_memory = {
            "2026-07-11": {"date": "2026-07-11", "topics": ["Sawmill closure in Quesnel"]},
        }
        assert pg.format_prior_coverage_for_prompt(deep_dive, episode_memory, {}) == ""


class TestFocusMemory:
    def test_last_time_on_focus_recalled(self):
        episode_memory = {
            "2026-06-23": {"date": "2026-06-23", "topics": ["Copper mine expansion"],
                           "focus": "mining-energy"},
            "2026-07-14": {"date": "2026-07-14", "topics": ["Ranch water tech"],
                           "focus": "agriculture-ranching"},
        }
        context = pg.format_memory_for_prompt(episode_memory, {}, today_focus=MINING_FOCUS)
        assert "RELATED EARLIER EPISODE" in context
        assert "2026-06-23" in context
        assert "rotation" not in context.split("RELATED EARLIER EPISODE")[0].lower()

    def test_no_focus_no_recall_line(self):
        episode_memory = {
            "2026-06-23": {"date": "2026-06-23", "topics": ["Copper mine expansion"],
                           "focus": "mining-energy"},
        }
        context = pg.format_memory_for_prompt(episode_memory, {}, today_focus=None)
        assert "RELATED EARLIER EPISODE" not in context

    def test_debate_must_differ_keys_on_theme_and_focus(self):
        debate_memory = {
            "2026-06-23": {"date": "2026-06-23", "theme": "Working Lands & Industry",
                           "focus": "mining-energy", "central_question": "Mining question?"},
            "2026-06-30": {"date": "2026-06-30", "theme": "Working Lands & Industry",
                           "focus": "forestry", "central_question": "Forestry question?"},
            "2026-07-01": {"date": "2026-07-01", "theme": "Working Lands & Industry",
                           "central_question": "Legacy question?"},  # pre-focus entry
        }
        context = pg.format_debate_memory_for_prompt(
            debate_memory, "Working Lands & Industry", today_focus=MINING_FOCUS
        )
        must_differ = context.split("cross-reference")[0]
        assert "Mining question?" in must_differ
        assert "Legacy question?" in must_differ  # no focus recorded — stay strict
        assert "Forestry question?" not in must_differ
        assert "Forestry question?" in context  # demoted to cross-reference list

    def test_update_memories_record_focus(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pg, "EPISODE_MEMORY_FILE", tmp_path / "episode_memory.json")
        monkeypatch.setattr(pg, "DEBATE_MEMORY_FILE", tmp_path / "debate_memory.json")
        pg.update_episode_memory("2026-07-21", ["topic"], ["theme"], focus=MINING_FOCUS)
        assert pg.load_memory(pg.EPISODE_MEMORY_FILE)["2026-07-21"]["focus"] == "mining-energy"
        pg.update_debate_memory("2026-07-21", "Working Lands & Industry",
                                {"central_question": "q"}, focus=MINING_FOCUS)
        assert pg.load_memory(pg.DEBATE_MEMORY_FILE)["2026-07-21"]["focus"] == "mining-energy"
