"""Tests for super-cycle focus rotation, article holding, and repeat-topic guards."""

from datetime import date, timedelta

import pytest

import podcast_generator as pg
from config_loader import (
    load_super_cycles_config,
    get_focus_for_day,
    get_event_focus_for_day,
    get_upcoming_day_slots,
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

    def test_upcoming_slots_exclude_today_and_cover_every_day(self):
        d = date(2026, 7, 18)  # Saturday
        slots = get_upcoming_day_slots(d, horizon_days=14)
        assert all(slot_date > d for slot_date, _, _, _ in slots)
        # Every day in the horizon gets a slot, uncycled Saturdays included:
        # the daily theme is a holding target even when the rotation has no
        # focus for that weekday.
        assert len(slots) == 14
        assert all(theme for _, _, theme, _ in slots)
        saturdays = [f for _, wd, _, f in slots if wd == 5]
        assert saturdays and all(f is None for f in saturdays)


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
        for slot_date, _wd, _theme, focus in get_upcoming_day_slots(d, horizon_days=14):
            if focus and focus["slug"] == slug:
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

    # --- Both buckets are routed (2026-08-17 regression) -------------------
    #
    # The hold loop used to iterate theme_articles only — the one bucket that by
    # definition contains nothing off-theme. Monday 2026-08-17 carried 8 theme
    # articles and 72 bonus ones, so nothing was ever eligible and the roundup
    # aired a lumber-tariffs piece (Tuesday's theme) and two 3D-printing pieces
    # (Wednesday's focus) on an Arts & Culture day.

    def test_bonus_bucket_article_is_held_for_its_focus_day(self, holding_env):
        saturday, mining_day = _find_saturday_before_focus("mining-energy")
        mining = _article(
            "Copper mine expansion clears exploration drilling permit",
            "mining-url", kw=0, boosted=50,
        )
        theme, bonus = pg.route_articles_for_focus(
            _filler_pool(), [mining], saturday, "Cariboo Local Affairs", None
        )
        assert all(a["url"] != "mining-url" for a in theme + bonus)
        entry = pg.load_memory(pg.HOLDING_FILE)["mining-url"]
        assert entry["status"] == "held"
        assert entry["target_date"] == mining_day.isoformat()

    def test_kept_bonus_article_stays_in_the_bonus_bucket(self, holding_env):
        """Routing must not promote an off-theme story into the theme blocks."""
        saturday = date(2026, 7, 18)
        misc = _article("A wholly unrelated story about nothing", "misc-url", kw=0)
        theme, bonus = pg.route_articles_for_focus(
            _filler_pool(), [misc], saturday, "Cariboo Local Affairs", None
        )
        assert any(a["url"] == "misc-url" for a in bonus)
        assert all(a["url"] != "misc-url" for a in theme)

    def test_held_for_an_upcoming_theme_when_no_focus_matches(self, holding_env):
        """Forestry is a Tuesday theme keyword every week, but only reaches a
        Tuesday slot on the weeks the rotation happens to sit on Forestry."""
        # A week whose Tuesday focus is *not* forestry, so only the theme can match.
        d = date(2026, 7, 18)
        for _ in range(6):
            tuesday = d + timedelta(days=(1 - d.weekday()) % 7 or 7)
            focus = get_focus_for_day(1, tuesday)
            if "forestry" not in pg._build_focus_keywords(focus):
                break
            d += timedelta(days=7)
        else:
            pytest.skip("every upcoming Tuesday sits on the forestry focus")

        lumber = _article(
            "New lumber tariffs restrict timber imports for sawmill operators",
            "lumber-url", kw=0, boosted=50,
        )
        theme, bonus = pg.route_articles_for_focus(
            _filler_pool(), [lumber], d, "Arts, Culture & Digital Storytelling", None
        )
        assert all(a["url"] != "lumber-url" for a in theme + bonus)
        entry = pg.load_memory(pg.HOLDING_FILE)["lumber-url"]
        assert entry["status"] == "held"
        assert date.fromisoformat(entry["target_date"]).weekday() == 1

    def test_local_story_is_never_held(self, holding_env):
        """Local news is time-sensitive and geography is orthogonal to the
        rotation — a Cariboo story opens the show whatever day it matches.

        It does give up its deep-dive claim when it carries none of today's
        subject matter, but it airs today either way: the ledger entry is a
        callback, never a hold.
        """
        saturday, _ = _find_saturday_before_focus("mining-energy")
        local_mining = _article(
            "Williams Lake copper mine expansion clears drilling permit",
            "local-mining-url", kw=0, boosted=50,
        )
        theme, bonus = pg.route_articles_for_focus(
            _filler_pool(), [local_mining], saturday, "Cariboo Local Affairs", None
        )
        assert any(a["url"] == "local-mining-url" for a in theme + bonus)
        entry = pg.load_memory(pg.HOLDING_FILE).get("local-mining-url")
        assert entry is None or entry["status"] != "held"


# ---------------------------------------------------------------------------
# The geographic day (2026-08-22)
#
# Cariboo Local Affairs is defined by WHERE a story is; every other theme is
# defined by what it is about. Scoring the two the same way made the word
# 'local' a theme keyword, so the day imported "8 local AI models that run
# great on 8GB of VRAM" and a Brooklyn ADU that "follows local and zoning
# laws", and it made place names the deep dive's ranking signal, so the debate
# ran on softwood duties and a beef-plant closure — Tuesday's episode.
# ---------------------------------------------------------------------------

class TestGeographicDay:
    def test_theme_is_flagged_geographic(self):
        assert pg._is_geographic_theme("Cariboo Local Affairs")
        assert not pg._is_geographic_theme("Working Lands & Industry")

    def test_strict_keywords_drop_the_description_prose(self):
        strict = pg._build_strict_theme_keywords("Cariboo Local Affairs")
        loose = pg._build_theme_keywords("Cariboo Local Affairs")
        for junk in ("that", "shape", "everyday", "life"):
            assert junk in loose, "description folding still expected in the loose set"
            assert junk not in strict

    def test_subject_keywords_are_civic_not_geographic(self):
        subject = pg._build_theme_subject_keywords("Cariboo Local Affairs")
        assert "council" in subject and "bylaw" in subject and "zoning" in subject
        for place in ("cariboo", "williams lake", "quesnel", "chilcotin"):
            assert place not in subject
        # 'local' matched "local AI models" and "Strange Spots on Local Fish"
        assert "local" not in subject

    def test_topical_theme_subject_keywords_are_unchanged(self):
        assert (pg._build_theme_subject_keywords("Working Lands & Industry")
                == pg._build_strict_theme_keywords("Working Lands & Industry"))

    def test_nothing_is_held_for_the_geographic_day(self, holding_env):
        """Five articles were waiting for 2026-08-22 on the word 'local'."""
        monday = date(2026, 8, 17)
        imports = [
            _article("8 local AI models that run great on 8GB of VRAM or less",
                     "vram-url", kw=0, boosted=50),
            _article("What Will It Take to Fix New York's Housing Shortage?",
                     "nyc-url", kw=0, boosted=70,
                     summary="A development and zoning fight over housing supply."),
        ]
        pg.route_articles_for_focus(_filler_pool(), imports, monday,
                                    "Arts, Culture & Digital Storytelling", None)
        holding = pg.load_memory(pg.HOLDING_FILE)
        saturday_slug = pg._theme_slug("Cariboo Local Affairs")
        assert all(e.get("target_focus_slug") != saturday_slug
                   for e in holding.values())

    def test_local_story_off_todays_subject_defers_its_deep_dive(self, holding_env):
        """The 2026-08-22 deep dive, in one article."""
        saturday = date(2026, 8, 22)
        lumber = _article(
            "B.C. lumber industry hoping for trade relief in new Canada-US deal",
            "lumber-url", kw=2, boosted=87,
            summary="Softwood lumber is subject to separate duties; sawmill "
                    "operators and the forestry sector await the outcome.",
        )
        lumber["authors"] = [{"name": "My Cariboo Now"}]
        theme, bonus = pg.route_articles_for_focus(
            _filler_pool(), [lumber], saturday, "Cariboo Local Affairs", None
        )
        aired = [a for a in theme + bonus if a["url"] == "lumber-url"]
        assert aired, "a local story always airs the day it arrives"
        assert aired[0]["_no_deep_dive"] is True
        entry = pg.load_memory(pg.HOLDING_FILE)["lumber-url"]
        assert entry["status"] == "aired_early"
        assert date.fromisoformat(entry["target_date"]).weekday() == 1

        # ...and the callback lands on Working Lands day
        target = date.fromisoformat(entry["target_date"])
        context, urls = pg.format_focus_callbacks_for_prompt(
            get_focus_for_day(1, target), theme_name="Working Lands & Industry"
        )
        assert "lumber-url" in urls and "lumber industry" in context

    def test_local_civic_story_keeps_its_deep_dive_claim(self, holding_env):
        saturday = date(2026, 8, 22)
        civic = _article(
            "Williams Lake council approves the mine reclamation zoning bylaw",
            "civic-url", kw=2, boosted=60,
        )
        theme, bonus = pg.route_articles_for_focus(
            _filler_pool(), [civic], saturday, "Cariboo Local Affairs", None
        )
        kept = [a for a in theme + bonus if a["url"] == "civic-url"]
        assert kept and not kept[0].get("_no_deep_dive")


class TestDeferredArticlesStayOutOfTheDeepDive:
    """`_no_deep_dive` was written by the router and read by nothing."""

    def test_substance_swap_will_not_promote_a_deferred_article(self):
        body = "word " * 200
        thin = _article("Quesnel council votes on the transit budget", "civic-url",
                        kw=2, boosted=60)
        thin["_body"] = "too short"
        deferred = _article("Softwood duties land on Cariboo timber", "lumber-url",
                            kw=3, boosted=90)
        deferred.update({"_body": body, "_no_deep_dive": True})
        spare = _article("Williams Lake council debates the zoning bylaw",
                         "zoning-url", kw=2, boosted=40)
        spare["_body"] = body

        deep_dive, news = pg._ensure_deep_dive_substance(
            [thin], [deferred, spare],
            theme_keywords=pg._build_theme_keywords("Cariboo Local Affairs"),
        )
        assert [a["url"] for a in deep_dive] == ["zoning-url"]
        assert "lumber-url" in {a["url"] for a in news}


class TestGeographicDeepDiveSelection:
    def _pool(self):
        return [
            _article("B.C. lumber industry hoping for trade relief in new deal",
                     "lumber-url", kw=3, boosted=87,
                     summary="Softwood duties on Cariboo timber."),
            _article("Ranching families west of Williams Lake recognized",
                     "ranch-url", kw=3, boosted=83,
                     summary="Cariboo Chilcotin cattle stewardship award."),
            _article("Quesnel council votes on the transit budget",
                     "civic-url", kw=1, boosted=55),
            _article("100 Mile House riders gear up for a training ride",
                     "ride-url", kw=2, boosted=67),
        ]

    def test_civic_subject_outranks_place_names(self):
        """The lumber story leads on every other signal — 3 feed keyword
        matches, a boosted score of 87, and two Cariboo place names against
        the council story's one."""
        deep_dive, _ = pg.select_deep_dive_from_feed(
            self._pool(), "Cariboo Local Affairs", count=2
        )
        assert deep_dive[0]["url"] == "civic-url"

    def test_deferred_articles_do_not_anchor_the_debate(self):
        pool = self._pool()
        for a in pool:
            if a["url"] in ("lumber-url", "ranch-url"):
                a["_no_deep_dive"] = True
        deep_dive, news = pg.select_deep_dive_from_feed(
            pool, "Cariboo Local Affairs", count=3
        )
        assert {a["url"] for a in deep_dive} == {"civic-url", "ride-url"}
        # Nothing is dropped — they still air in the roundup
        assert {a["url"] for a in news} == {"lumber-url", "ranch-url"}

    def test_deferred_articles_return_when_the_day_is_too_thin(self):
        pool = self._pool()
        for a in pool:
            if a["url"] != "civic-url":
                a["_no_deep_dive"] = True
        deep_dive, _ = pg.select_deep_dive_from_feed(
            pool, "Cariboo Local Affairs", count=3
        )
        assert len(deep_dive) == 3, "a debate with no sources is the worse failure"

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


class TestHomeJurisdictionCentrality:
    """Williams Lake is the home jurisdiction; Quesnel is a neighbour.

    Regression for 2026-09-05, when the Cariboo Local Affairs deep dive ran on
    Quesnel's winter-shelter siting and a Quesnel council candidate. Both are
    civic and both are local — nothing in the ranking could tell whose council
    it was.
    """

    THEME = "Cariboo Local Affairs"

    def _pool(self):
        return [
            _article("City of Quesnel makes final push for an alternative shelter site",
                     "quesnel-url", kw=1, boosted=84,
                     summary="Quesnel council weighs zoning and a bylaw amendment."),
            _article("Williams Lake council debates the water infrastructure budget",
                     "wl-url", kw=1, boosted=61,
                     summary="City of Williams Lake budget deliberations."),
        ]

    def test_home_council_outranks_a_denser_neighbour_story(self):
        deep_dive, _ = pg.select_deep_dive_from_feed(
            self._pool(), self.THEME, count=2
        )
        assert deep_dive[0]["url"] == "wl-url"

    def test_the_neighbour_story_still_airs(self):
        """An ordering rule, not an exclusion."""
        deep_dive, news = pg.select_deep_dive_from_feed(
            self._pool(), self.THEME, count=2
        )
        assert {a["url"] for a in deep_dive} == {"wl-url", "quesnel-url"}
        assert news == []

    def test_home_place_hits_are_zero_off_the_geographic_theme(self):
        """`home_places` is configured on the geographic day alone, so the term
        is a constant everywhere else and changes no existing ordering."""
        a = _article("Williams Lake council debates the budget", "wl-url")
        assert pg._home_place_hits(a, "Working Lands & Industry") == 0
        assert pg._home_place_hits(a, self.THEME) > 0

    def test_a_home_story_off_the_civic_subject_does_not_lead(self):
        """Subject matter still gates entry: this promotes a home *civic*
        story, never a home speedway story."""
        pool = [
            _article("Racers took their final laps at Thunder Mountain Speedway "
                     "in Williams Lake", "speedway-url", kw=2, boosted=90),
            _article("Quesnel council votes on the transit budget",
                     "quesnel-url", kw=1, boosted=55),
        ]
        deep_dive, _ = pg.select_deep_dive_from_feed(pool, self.THEME, count=2)
        assert deep_dive[0]["url"] == "quesnel-url"


class TestElectionEventFocus:
    """The event focus is calendar-bounded and outranks routine civic business.

    Unlike a super-cycle focus it is named on air — an election is the civic
    fact the coverage exists to serve, not a curation device.
    """

    THEME = "Cariboo Local Affairs"
    ELECTION = {
        "name": "Williams Lake 2026 general local election",
        "start": "2026-09-01",
        "end": "2026-10-24",
        "keywords": ["nomination", "candidate", "councillor", "seeking re-election",
                     "general local election", "acclamation"],
        "lens": "Name the election on air. Report the race, never endorse.",
    }

    def test_window_is_calendar_bounded(self):
        assert get_event_focus_for_day(5, date(2026, 9, 5)) is not None
        assert get_event_focus_for_day(5, date(2026, 10, 17)) is not None
        assert get_event_focus_for_day(5, date(2026, 10, 31)) is None
        assert get_event_focus_for_day(5, date(2026, 8, 29)) is None

    def test_no_event_focus_on_a_topical_theme(self):
        assert get_event_focus_for_day(1, date(2026, 9, 5)) is None

    def test_election_material_leads_the_deep_dive(self):
        pool = [
            _article("Williams Lake council debates the water budget",
                     "budget-url", kw=1, boosted=88,
                     summary="City of Williams Lake infrastructure spending."),
            _article("Three Williams Lake city councillors not seeking re-election",
                     "election-url", kw=1, boosted=52,
                     summary="Nomination papers are due Friday."),
        ]
        deep_dive, _ = pg.select_deep_dive_from_feed(
            pool, self.THEME, count=2, event_focus=self.ELECTION
        )
        assert deep_dive[0]["url"] == "election-url"

    def test_election_material_is_civic_without_a_theme_keyword(self):
        """An event match counts as civic subject matter in its own right —
        the vocabulary is safe here because every candidate is already local."""
        pool = [
            _article("Nomination papers filed ahead of Friday's deadline",
                     "nom-url", kw=0, boosted=40,
                     summary="Williams Lake residents step forward."),
            _article("Cariboo cattle prices hold steady into the fall",
                     "cattle-url", kw=0, boosted=80),
        ]
        deep_dive, _ = pg.select_deep_dive_from_feed(
            pool, self.THEME, count=1, event_focus=self.ELECTION
        )
        assert deep_dive[0]["url"] == "nom-url"

    def test_the_lens_is_named_on_air_unlike_a_focus(self):
        lens = pg._build_theme_lens(self.THEME, event_focus=self.ELECTION)
        assert "Name the election on air" in lens
        assert "never announce it" not in lens.lower()

    def test_the_shipped_lens_carries_the_no_endorsement_rule(self):
        event = get_event_focus_for_day(5, date(2026, 9, 5))
        lens = pg._build_theme_lens(self.THEME, event_focus=event).lower()
        assert "never tell listeners how to vote" in lens

    def test_no_event_focus_leaves_the_lens_untouched(self):
        assert (pg._build_theme_lens(self.THEME)
                == pg._build_theme_lens(self.THEME, event_focus=None))


class TestElectionRecall:
    """An election story airs the day it breaks AND returns on the civic day.

    Both dates matter: it is news on the day it breaks and context on the day
    the show covers the race. So this is a recall, not a hold — nothing is
    withheld from today to pay for Saturday.
    """

    EVENT = {
        "name": "Williams Lake 2026 general local election",
        "start": "2026-09-01",
        "end": "2026-10-24",
        "weekday": 5,
        "keywords": ["nomination", "candidate", "seeking re-election", "councillor"],
        "lens": "Name the election on air.",
    }
    TUESDAY = date(2026, 9, 8)
    SATURDAY = date(2026, 9, 12)

    def _story(self):
        return _article(
            "Three Williams Lake city councillors not seeking re-election",
            "wl-election-url", kw=2, boosted=79,
            summary="Nomination papers are due Friday in Williams Lake.")

    def test_recall_targets_the_next_civic_day(self):
        assert pg._recall_target_date(self.TUESDAY, self.EVENT) == self.SATURDAY

    def test_the_window_closes_the_lane(self):
        assert pg._recall_target_date(date(2026, 10, 20), self.EVENT) == date(2026, 10, 24)
        assert pg._recall_target_date(date(2026, 10, 27), self.EVENT) is None
        assert pg._recall_target_date(self.TUESDAY, None) is None

    def test_the_story_still_airs_on_the_day_it_breaks(self, holding_env):
        """Local news is the most time-sensitive material in the pool. A recall
        must never become a hold."""
        theme, bonus = pg.route_articles_for_focus(
            [self._story()], [], self.TUESDAY, "Working Lands & Industry",
            None, event_focus=self.EVENT)
        assert [a["url"] for a in theme] == ["wl-election-url"]
        assert bonus == []

    def test_it_comes_back_on_the_civic_day(self, holding_env):
        pg.route_articles_for_focus([self._story()], [], self.TUESDAY,
                                    "Working Lands & Industry", None,
                                    event_focus=self.EVENT)
        theme, _ = pg.route_articles_for_focus(
            [], [], self.SATURDAY, "Cariboo Local Affairs", None,
            event_focus=self.EVENT)
        assert [a["url"] for a in theme] == ["wl-election-url"]
        assert theme[0]["_recalled_from"] == self.TUESDAY.isoformat()

    def test_a_recall_survives_having_been_cited(self, holding_env, monkeypatch):
        """Every other holding status is pruned once the URL appears in recent
        citations. A recall exists *because* the story already aired."""
        monkeypatch.setattr(pg, "load_recent_citations",
                            lambda days=7: [{"url": "wl-election-url"}])
        pg.route_articles_for_focus([self._story()], [], self.TUESDAY,
                                    "Working Lands & Industry", None,
                                    event_focus=self.EVENT)
        theme, _ = pg.route_articles_for_focus(
            [], [], self.SATURDAY, "Cariboo Local Affairs", None,
            event_focus=self.EVENT)
        assert [a["url"] for a in theme] == ["wl-election-url"]

    def test_a_fresh_copy_in_the_feed_wins_over_the_recall(self, holding_env):
        """No double-listing when the outlet re-runs the story."""
        pg.route_articles_for_focus([self._story()], [], self.TUESDAY,
                                    "Working Lands & Industry", None,
                                    event_focus=self.EVENT)
        theme, _ = pg.route_articles_for_focus(
            [self._story()], [], self.SATURDAY, "Cariboo Local Affairs", None,
            event_focus=self.EVENT)
        assert [a["url"] for a in theme] == ["wl-election-url"]

    def test_it_does_not_fire_twice(self, holding_env):
        """Released is released — the story must not return every Saturday."""
        pg.route_articles_for_focus([self._story()], [], self.TUESDAY,
                                    "Working Lands & Industry", None,
                                    event_focus=self.EVENT)
        pg.route_articles_for_focus([], [], self.SATURDAY, "Cariboo Local Affairs",
                                    None, event_focus=self.EVENT)
        theme, _ = pg.route_articles_for_focus(
            [], [], date(2026, 9, 19), "Cariboo Local Affairs", None,
            event_focus=self.EVENT)
        assert theme == []

    def test_a_non_election_local_story_is_not_recalled(self, holding_env):
        pool = [_article("Cariboo cattle prices hold steady into the fall",
                         "cattle-url", kw=2, boosted=80,
                         summary="Williams Lake ranchers report a steady season.")]
        pg.route_articles_for_focus(pool, [], self.TUESDAY,
                                    "Working Lands & Industry", None,
                                    event_focus=self.EVENT)
        theme, _ = pg.route_articles_for_focus(
            [], [], self.SATURDAY, "Cariboo Local Affairs", None,
            event_focus=self.EVENT)
        assert theme == []

    def test_a_non_local_election_story_is_not_recalled(self, holding_env):
        """US midterm coverage carries the same vocabulary."""
        pool = [_article("What to watch in the Massachusetts primary election",
                         "us-url", kw=0, boosted=70,
                         summary="A crowded field of candidates in Boston.")]
        pg.route_articles_for_focus(pool, [], self.TUESDAY,
                                    "Working Lands & Industry", None,
                                    event_focus=self.EVENT)
        theme, _ = pg.route_articles_for_focus(
            [], [], self.SATURDAY, "Cariboo Local Affairs", None,
            event_focus=self.EVENT)
        assert theme == []

    def test_the_civic_day_does_not_book_stories_back_to_itself(self, holding_env):
        pg.route_articles_for_focus([self._story()], [], self.SATURDAY,
                                    "Cariboo Local Affairs", None,
                                    event_focus=self.EVENT)
        theme, _ = pg.route_articles_for_focus(
            [], [], date(2026, 9, 19), "Cariboo Local Affairs", None,
            event_focus=self.EVENT)
        assert theme == []

    def test_a_recalled_story_can_anchor_the_deep_dive(self, holding_env):
        pg.route_articles_for_focus([self._story()], [], self.TUESDAY,
                                    "Working Lands & Industry", None,
                                    event_focus=self.EVENT)
        theme, _ = pg.route_articles_for_focus(
            [], [], self.SATURDAY, "Cariboo Local Affairs", None,
            event_focus=self.EVENT)
        theme.append(_article("Quesnel council votes on the transit budget",
                              "quesnel-url", kw=1, boosted=95))
        deep_dive, _ = pg.select_deep_dive_from_feed(
            theme, "Cariboo Local Affairs", count=2, event_focus=self.EVENT)
        assert deep_dive[0]["url"] == "wl-election-url"


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


def _cite(day, title, url=None, discussed=True):
    return {"episode_date": day, "title": title, "url": url or title, "discussed": discussed}


class TestRunningThreads:
    """2026-10-03: data centres aired a fifth time in eight days, introduced fresh."""
    PRIOR = [
        _cite("2026-09-27", "[Tom's Hardware] US AI data centers projected to become a top gas consumer"),
        _cite("2026-09-29", "[NYT Business] Wall Street Is Growing Skeptical of the Data Center Boom"),
        _cite("2026-09-30", "[Dezeen] Can data centres ever be examples of good design?"),
    ]

    def test_same_subject_through_a_different_story_is_tagged(self):
        arts = [{"title": "[Williams Lake Tribune] B.C. Greens propose AI data centre moratorium",
                 "url": "greens"}]
        assert pg.mark_running_threads(arts, self.PRIOR, "2026-10-03") == 1
        assert arts[0]["_prior_mentions"] == ["2026-09-30", "2026-09-29", "2026-09-27"]
        tag = pg._coverage_tags(arts[0])
        assert "RUNNING THREAD" in tag and "data centre" in tag and "Wednesday Sep 30" in tag

    def test_shared_words_that_are_not_a_pair_do_not_match(self):
        prior = [_cite("2026-09-27", "Data breach exposes 220 million records from last year")]
        arts = [{"title": "Million-year-old crocodile skin shows camouflage, study year", "url": "x"}]
        assert pg.mark_running_threads(arts, prior, "2026-10-03") == 0

    def test_place_names_and_stopwords_never_make_a_thread(self):
        prior = [_cite("2026-09-29", "South Cariboo school runs Terry Fox event in 100 Mile House")]
        arts = [{"title": "Orange Shirt Day commemorated by South Cariboo schools in 100 Mile House",
                 "url": "x"}]
        assert pg.mark_running_threads(arts, prior, "2026-10-03") == 0

    def test_window_undiscussed_and_same_url_are_ignored(self):
        prior = [
            _cite("2026-09-20", "Data centre boom"),                      # outside the window
            _cite("2026-10-01", "Data centre boom", discussed=False),     # never aired
            _cite("2026-10-02", "Data centre boom", url="same"),          # recall's job
            _cite("2026-10-03", "Data centre boom"),                      # today's re-run
        ]
        arts = [{"title": "Data centre moratorium proposed", "url": "same"}]
        assert pg.mark_running_threads(arts, prior, "2026-10-03") == 0

    def test_recall_tag_wins_over_thread_tag(self):
        a = {"_recalled_from": "2026-10-02", "_prior_mentions": ["2026-10-02"],
             "_thread_phrase": "snap election"}
        tag = pg._coverage_tags(a)
        assert "ALREADY COVERED" in tag and "RUNNING THREAD" not in tag

    def test_deep_dive_articles_carry_the_coverage_tags(self):
        """The recall tag reached only the roundup; two recalled stories aired as
        new deep-dive material on 2026-10-03."""
        import inspect
        src = inspect.getsource(pg.generate_podcast_script)
        dd = src.split("def _format_deep_dive_article", 1)[1].split("\n    def ", 1)[0]
        assert "_coverage_tags(a)" in dd


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


class TestCandidateRecordRules:
    """The election lens must ask for the whole record, not the flattering slice.

    On 2026-09-12 the deep dive reported a mayoral candidate's 2014 win ("that's
    a mandate") as his current standing, never mentioned the 2022 loss that
    followed it, and called the incumbent "the sitting incumbent" for the entire
    segment while its own source carried the name. Every assertion here names a
    sentence of that failure.
    """

    THEME = "Cariboo Local Affairs"

    @pytest.fixture
    def lens(self):
        event = get_event_focus_for_day(5, date(2026, 9, 19))
        assert event is not None
        return pg._build_theme_lens(self.THEME, event_focus=event).lower()

    def test_every_race_on_the_ballot_is_named(self, lens):
        """A race the episode never names is a race it did not cover — and the
        CRD electoral area directors are on the same ballot as the city races."""
        for race in ("mayor", "council", "electoral area director",
                     "school district 27 trustee"):
            assert race in lens, race

    def test_candidates_are_named_not_described_by_role(self, lens):
        assert "the sitting incumbent" in lens  # quoted as the thing not to say
        assert "placeholders, not reporting" in lens

    def test_a_previous_loss_is_reported_as_plainly_as_a_win(self, lens):
        assert "the losses exactly as plainly as the wins" in lens
        assert "most recent" in lens

    def test_an_older_win_is_never_current_standing(self, lens):
        assert "never called a mandate, a landslide or name recognition" in lens

    def test_documented_controversy_is_part_of_the_record(self, lens):
        for term in ("conflict-of-interest", "censure", "resignation"):
            assert term in lens, term
        assert "thumb on the scale" in lens

    def test_the_record_is_sourced_or_unsaid(self, lens):
        """The counterweight to the rules above: naming a loss or a controversy
        is reporting only when a source establishes it."""
        assert "never infer a record" in lens
        assert "allegation" in lens

    def test_no_endorsement_survives_the_rewrite(self, lens):
        assert "never tell listeners how to vote" in lens
        assert "never rank them" in lens

    def test_the_anchor_yields_to_the_race(self, lens):
        """Half the 2026-09-12 deep dive was the week's anchor question about
        friction rather than the race in front of it."""
        assert "drop the anchor for the day" in lens

    def test_crd_areas_rank_as_home_jurisdiction(self):
        """`home_places` answers 'is this story ours?'. An area-director story
        is a race the listener votes in, so it cannot rank as a neighbour."""
        article = _article("Electoral Area F director acclaimed for a third term",
                           "area-f-url", kw=0, boosted=40,
                           summary="Horsefly and Likely voters get no ballot.")
        assert pg._home_place_hits(article, self.THEME) > 0


class TestEventResearchSweep:
    """The lens can only demand facts the research pass went and got.

    A nomination-day story carries none of a candidate's record, so without the
    sweep every rule in TestCandidateRecordRules degrades to "the show has not
    established that".
    """

    ARTICLES = [{"title": "Nominations close for Williams Lake council",
                 "summary": "s", "_body": "b"}]

    @pytest.fixture
    def capture(self, monkeypatch):
        seen = {}

        def fake_loop(client, model, system_prompt, user_content, tools,
                      tool_executors, max_iterations, max_tokens):
            seen["system"] = system_prompt
            seen["user"] = user_content
            seen["tools"] = [t["name"] for t in tools]
            seen["tool_defs"] = tools
            seen["executors"] = tool_executors
            seen["iterations"] = max_iterations
            return seen.get("reply", "NONE")

        seen["queries"] = []

        seen["freshness"] = {}

        def fake_search(query, api_key, count=5, freshness=None):
            seen["queries"].append(query)
            seen["freshness"][query] = freshness
            return [{"title": f"hit for {query}", "url": "https://example.org",
                     "description": "snippet"}]

        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key")
        monkeypatch.setattr(pg, "_run_agentic_loop", fake_loop)
        monkeypatch.setattr(pg, "_brave_research_available", lambda: True)
        monkeypatch.setattr(pg, "_brave_search", fake_search)
        monkeypatch.setattr(pg, "_BRAVE_WALLS", {
            "search": {"hit": False, "detail": ""}, "answers": {"hit": False, "detail": ""}})
        monkeypatch.setattr(pg, "_BRAVE_SEARCH_STATE", {
            "search_calls": 0, "search_ts": 0.0, "deep_calls": 0, "deep_ts": 0.0,
            "answer_calls": 0, "event_calls": 0, "page_fetches": 0})
        seen["degraded"] = []
        monkeypatch.setattr(pg, "degrade", lambda seg, detail: seen["degraded"].append(detail))
        return seen

    def _run(self, event):
        return pg.research_deep_dive_with_agent(
            self.ARTICLES, "Cariboo Local Affairs", object(), event_focus=event)

    def test_the_shipped_event_carries_a_research_brief(self):
        event = get_event_focus_for_day(5, date(2026, 9, 19))
        brief = event.get("research", "").lower()
        assert "won or lost" in brief
        assert "incumbent" in brief
        assert "could not find a record for" in brief

    def test_an_active_event_makes_research_a_standing_assignment(self, capture):
        self._run(get_event_focus_for_day(5, date(2026, 9, 19)))
        assert "STANDING ASSIGNMENT" in capture["system"]
        assert "Research IS warranted today" in capture["system"]

    def test_an_active_event_widens_the_search_allowance(self, capture):
        self._run(get_event_focus_for_day(5, date(2026, 9, 19)))
        assert f"up to {pg.EVENT_RESEARCH_SEARCH_LIMIT} targeted" in capture["system"]
        assert capture["iterations"] == (pg.EVENT_RESEARCH_SEARCH_LIMIT
                                         + pg.EVENT_PAGE_FETCH_LIMIT + 1)
        assert capture["tools"] == ["web_search", "fetch_page"]

    def test_the_roll_call_searches_every_race_and_candidate(self, capture):
        """2026-09-26: eight agent searches met a twenty-name ballot, and eleven
        council candidates plus three CRD seats aired as "nothing found"."""
        event = get_event_focus_for_day(5, date(2026, 9, 26))
        self._run(event)
        races = event["roster"]["races"]
        running = {c for r in races for c in r["candidates"]} - {"Jared Wardlaw-Gimbel"}
        for race in races:
            assert race["search"] in capture["queries"]
        for name in running:
            assert f'"{name}" Williams Lake' in capture["queries"], name
        assert '"Jared Wardlaw-Gimbel" Williams Lake' not in capture["queries"]
        assert len(capture["queries"]) == len(races) + len(running)
        assert len(capture["queries"]) <= pg.BRAVE_EVENT_CALL_LIMIT
        # The agent reads the roll call before it spends a search.
        assert "ROLL-CALL SEARCHES" in capture["user"]
        assert 'QUERY: "Ruth Lloyd" Williams Lake' in capture["user"]

    def test_the_roll_call_stops_at_its_own_meter(self, capture, monkeypatch):
        monkeypatch.setattr(pg, "BRAVE_EVENT_CALL_LIMIT", 5)
        self._run(get_event_focus_for_day(5, date(2026, 9, 26)))
        assert len(capture["queries"]) == 5
        # The deep-dive meter fact resolution runs on is untouched.
        assert pg._BRAVE_SEARCH_STATE["deep_calls"] == 0
        assert any("event-sweep budget spent" in d for d in capture["degraded"])

    def test_an_ordinary_day_runs_no_roll_call(self, capture):
        self._run(None)
        assert capture["queries"] == []
        # Page reads are on no Brave meter, so every day's pass gets them.
        assert capture["tools"] == ["web_search", "fetch_page"]

    def test_a_thin_result_degrades(self, capture):
        """Most of the ballot unfound is a research failure the run report
        carries, not a quiet green day."""
        event = get_event_focus_for_day(5, date(2026, 9, 26))
        capture["reply"] = ("PRE-RESEARCHED INSIGHTS FOR THE DEEP DIVE\n...\n"
                            "NO RECORD FOUND: Ruth Lloyd; Charlene Hays; Cianna O'Connor; "
                            "Billie Sheridan; Nathan Wiebe; Kayla Zaruk; Greg Jeannotte; "
                            "Mary Forbes")
        assert self._run(event)
        assert any("no record for 8 of 21" in d for d in capture["degraded"])

    def test_a_mostly_sourced_result_does_not_degrade(self, capture):
        event = get_event_focus_for_day(5, date(2026, 9, 26))
        capture["reply"] = ("PRE-RESEARCHED INSIGHTS FOR THE DEEP DIVE\n...\n"
                            "NO RECORD FOUND: Ruth Lloyd; Kayla Zaruk")
        self._run(event)
        assert not any("no record" in d for d in capture["degraded"])

    def test_an_empty_result_on_a_ballot_day_degrades(self, capture):
        self._run(get_event_focus_for_day(5, date(2026, 9, 26)))
        assert any("returned nothing" in d for d in capture["degraded"])

    def test_page_reads_are_budgeted(self, capture, monkeypatch):
        monkeypatch.setattr(pg, "EVENT_PAGE_FETCH_LIMIT", 1)
        monkeypatch.setattr(pg, "_fetch_page_text", lambda url: "Area F: acclaimed")
        assert pg._fetch_page_tool_executor({"url": "https://a.example"}) == "Area F: acclaimed"
        assert "budget spent" in pg._fetch_page_tool_executor({"url": "https://b.example"})

    def test_page_reads_refuse_non_http(self):
        assert pg._fetch_page_text("file:///etc/passwd") == ""


    def test_an_ordinary_day_decides_for_itself(self, capture):
        """Six days in seven still decide for themselves whether to research,
        at the original four-search budget, now with page reads."""
        self._run(None)
        assert "STANDING ASSIGNMENT" not in capture["system"]
        assert "up to 4 targeted" in capture["system"]
        assert capture["iterations"] == 4 + pg.TOPIC_PAGE_FETCH_LIMIT + 1

    # --- An all-week election gets its own pass (2026-10-02) ---------------
    # From 2026-09-28 the provincial vote rode in on a roundup story every
    # weekday and took the whole research pass: every research question was the
    # ballot, none was the deep dive's.

    @staticmethod
    def _weekday_events():
        from config_loader import get_research_events
        return get_research_events(1, date(2026, 10, 6))

    def test_a_provincial_story_leaves_the_deep_dive_its_own_pass(self, capture):
        pg.research_deep_dive_with_agent(self.ARTICLES, "Working Lands & Industry",
                                         object(), events=self._weekday_events())
        assert "STANDING ASSIGNMENT" not in capture["system"]
        assert "up to 4 targeted" in capture["system"]
        assert capture["queries"] == [], "the roll call belongs to the election pass"

    def test_saturday_still_shares_one_pass(self, capture):
        from config_loader import get_research_events
        events = get_research_events(5, date(2026, 10, 3))
        assert pg._ballot_pass_events(events) == []
        pg.research_deep_dive_with_agent(self.ARTICLES, "Cariboo Local Affairs",
                                         object(), events=events)
        assert "STANDING ASSIGNMENT" in capture["system"]

    def test_only_a_weekday_all_week_event_gets_a_pass(self):
        names = [e["name"] for e in pg._ballot_pass_events(self._weekday_events())]
        assert names == ["2026 B.C. provincial general election"]
        assert pg._ballot_pass_events([]) == []

    def test_the_ballot_pass_runs_on_the_election_meter(self, capture):
        events = self._weekday_events()
        pg._research_event_ballot(self.ARTICLES, object(), events)
        assert "ELECTION RESEARCH" in capture["system"]
        assert "not the deep dive's subject" in capture["system"]
        assert "STANDING ASSIGNMENT" in capture["system"]
        assert capture["tools"] == ["web_search", "fetch_page"]
        assert pg._BRAVE_SEARCH_STATE["deep_calls"] == 0
        assert pg._BRAVE_SEARCH_STATE["event_calls"] == len(capture["queries"]) > 0

    def test_who_is_running_is_asked_of_this_campaign_only(self, capture):
        events = self._weekday_events()
        pg._research_event_ballot(self.ARTICLES, object(), events)
        race = "Cariboo-Chilcotin candidates 2026 B.C. election"
        assert capture["freshness"][race].startswith("2026-09-22to")
        # A candidate's record is the earlier pages.
        assert capture["freshness"]['"Lorne Doerkson" B.C. election 2026'] is None

    def test_the_ballot_search_is_results_only(self, capture, monkeypatch):
        """SOURCED OR UNSAID: an Answers reply carries no URL."""
        events = self._weekday_events()
        pg._research_event_ballot(self.ARTICLES, object(), events)
        tool = next(t for t in capture["tool_defs"] if t["name"] == "web_search")
        assert "mode" not in tool["input_schema"]["properties"]

        def _no_answers(q):
            raise AssertionError("the election pass asked Answers")
        monkeypatch.setattr(pg, "_brave_summarize", _no_answers)
        out = capture["executors"]["web_search"]({"query": "who filed", "mode": "answer"})
        assert "Source: https://example.org" in out
        assert pg._BRAVE_SEARCH_STATE["deep_calls"] == 0

    def test_recent_applies_the_campaign_window(self, capture):
        pg._research_event_ballot(self.ARTICLES, object(), self._weekday_events())
        capture["executors"]["web_search"]({"query": "who filed now", "recent": True})
        capture["executors"]["web_search"]({"query": "2020 result"})
        assert capture["freshness"]["who filed now"].startswith("2026-09-22to")
        assert capture["freshness"]["2020 result"] is None

    def test_each_pass_is_held_to_its_search_allowance(self, capture):
        """2026-09-29: 24 searches against an allowance of 12, then all 8
        Answers calls, before fact resolution ran."""
        self._run(None)
        run = capture["executors"]["web_search"]
        outs = [run({"query": f"q{i}", "mode": "results"}) for i in range(5)]
        assert all("Source:" in o for o in outs[:4])
        assert "allowance for this pass is spent" in outs[4]
        assert pg._BRAVE_SEARCH_STATE["deep_calls"] == 4

    def test_the_deep_dive_pass_reads_pages_on_its_own_budget(self, capture, monkeypatch):
        monkeypatch.setattr(pg, "_fetch_page_text", lambda url: "the report")
        self._run(None)
        read = capture["executors"]["fetch_page"]
        outs = [read({"url": f"https://p.example/{i}"}) for i in range(pg.TOPIC_PAGE_FETCH_LIMIT + 1)]
        assert outs[:-1] == ["the report"] * pg.TOPIC_PAGE_FETCH_LIMIT
        assert "budget spent" in outs[-1]
        # The election passes' page budget is untouched.
        assert pg._BRAVE_SEARCH_STATE["page_fetches"] == 0

    def test_the_election_pass_reads_the_stories_that_brought_it_in(self):
        askew = {"title": "Retired school teacher named BC NDP candidate for Cariboo-Chilcotin",
                 "summary": "Kathryn Askew of Canim Lake"}
        sar = {"title": "Volunteer search and rescue in the Chilcotin", "summary": "Callouts"}
        assert pg._event_articles(self._weekday_events(), [sar, askew]) == [askew]

    def test_the_pipeline_runs_the_ballot_after_the_deep_dive_in_its_own_segment(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "podcast_generator.py").read_text()
        deep = src.index("brave_context = research_deep_dive_with_agent(")
        seg = src.index('with segment("script/election-research", critical=False):')
        ballot = src.index("ballot_context = _research_event_ballot(")
        assert deep < seg < ballot

    def test_the_sweep_fits_under_the_deep_dive_meter(self):
        """`_resolve_script_questions_with_brave` runs after the research pass on
        the same meter — the widened sweep must not spend the whole budget."""
        assert pg.EVENT_RESEARCH_SEARCH_LIMIT < pg.BRAVE_DEEP_DIVE_CALL_LIMIT
        assert pg.BRAVE_DEEP_DIVE_CALL_LIMIT - pg.EVENT_RESEARCH_SEARCH_LIMIT >= 4


class TestAllWeekEvents:
    """The 2026 provincial snap vote belongs to no one theme: it is swept on
    whichever day the episode carries its material."""

    def test_provincial_event_is_live_every_day_of_its_window(self):
        from config_loader import get_research_events

        for weekday in range(7):
            names = [e["name"] for e in get_research_events(weekday, date(2026, 10, 6))]
            assert "2026 B.C. provincial general election" in names
        assert get_research_events(1, date(2026, 11, 1)) == []

    def test_saturday_carries_both_ballots_own_day_first(self):
        from config_loader import get_research_events

        events = get_research_events(5, date(2026, 10, 3))
        assert [e["_own_day"] for e in events] == [True, False]

    def test_provincial_sweep_needs_provincial_material(self):
        from config_loader import get_research_events

        events = get_research_events(1, date(2026, 10, 6))
        tech = [{"title": "A new 3D printer review", "summary": "Hands-on with the X1"}]
        prov = [{"title": "Cariboo-Chilcotin candidates debate in Williams Lake",
                 "summary": "The B.C. NDP and B.C. Conservatives met at the hall."}]
        us = [{"title": "Senate race tightens", "summary": "US midterm election polls"}]
        assert pg._events_in_play(events, tech) == []
        assert pg._events_in_play(events, us) == []
        assert [e["name"] for e in pg._events_in_play(events, prov)] == [
            "2026 B.C. provincial general election"]

    def test_mla_matches_on_word_boundary(self):
        from config_loader import get_research_events

        events = get_research_events(1, date(2026, 10, 6))
        assert pg._events_in_play(events, [{"title": "Dalmatian rescue", "summary": ""}]) == []
        assert pg._events_in_play(events, [{"title": "MLA opens office", "summary": ""}])

    def test_provincial_roll_call_covers_ridings_and_seat_holders(self):
        from config_loader import get_research_events

        event = [e for e in get_research_events(1, date(2026, 10, 6)) if not e["_own_day"]][0]
        queries = pg._event_sweep_queries(event)
        assert "Cariboo-Chilcotin candidates 2026 B.C. election" in queries
        assert '"Lorne Doerkson" B.C. election 2026' in queries
        assert '"Sheldon Clare" B.C. election 2026' in queries

    def test_provincial_lens_rides_along_without_changing_the_theme(self):
        from config_loader import get_research_events

        event = [e for e in get_research_events(1, date(2026, 10, 6)) if not e["_own_day"]][0]
        plain = pg._build_theme_lens("Working Lands & Industry")
        lens = pg._build_theme_lens("Working Lands & Industry", extra_events=[event])
        assert lens.startswith(plain)
        assert "never move a name between the two ballots" in lens
        assert "Seat held going in by Lorne Doerkson" in lens


# ---------------------------------------------------------------------------
# Released articles must carry the target day's labels, not the hold day's
#
# The 2026-09-17 episode is the whole reason this block exists. Three Indigenous
# articles were held earlier in the week FOR Indigenous Lands day, released into
# that day's pool, and then cut by the same run as "over budget/unconnected" —
# because the labels that travelled with them (`_keyword_matches`, `_is_bonus`)
# describe whichever day they were held ON, and both consumers read exactly
# those. The roundup aired 15 stories with an empty theme block.
# ---------------------------------------------------------------------------

THURSDAY_THEME = "Indigenous Lands & Innovation"


def _held_article(title, url, summary="", **extra):
    """An article as the holding pen stores it: stamped by the day it was held."""
    a = _article(title, url, kw=0, boosted=50, summary=summary)
    a["_is_bonus"] = True          # the hold day's verdict, not today's
    a["_theme_score"] = 97         # a percentile from the hold day's feed
    a["_theme_score_raw"] = 6      # that day's charter, not this one's
    a.update(extra)
    return a


class TestReleasedArticleRelabelling:
    def test_relabel_recomputes_against_the_day_it_lands_on(self):
        a = _held_article(
            "[APTN News] Fighting fires with good fire: how Indigenous land "
            "guardians are protecting the boreal forest", "good-fire",
        )
        pg._relabel_for_day(a, THURSDAY_THEME)
        assert a["_keyword_matches"] > 0
        assert a["_is_bonus"] is False

    def test_relabel_drops_the_hold_days_scores_rather_than_rewriting_them(self):
        a = _held_article("Something off-theme entirely", "off")
        pg._relabel_for_day(a, THURSDAY_THEME)
        # There is no charter judgment for today's theme to substitute, and a
        # wrong number that looks authoritative is worse than no number.
        assert "_theme_score" not in a
        assert "_theme_score_raw" not in a
        assert a["_keyword_matches"] == 0
        assert a["_is_bonus"] is True

    def test_relabel_strips_the_source_tag_before_matching(self):
        # "[APTN News]" is a source tag, not evidence about the body.
        tagged = _held_article("[APTN News] Ottawa budget briefing", "t1")
        pg._relabel_for_day(tagged, THURSDAY_THEME)
        assert tagged["_keyword_matches"] == 0

    def test_released_article_reaches_the_roundup_theme_block(self):
        a = _held_article(
            "[APTN News] B.C. wildfires hit First Nations reserves harder, "
            "human rights report says", "aptn-fires",
        )
        pg._relabel_for_day(a, THURSDAY_THEME)
        a["_held_from"] = "2026-09-16"
        pg._annotate_roundup_blocks([a], THURSDAY_THEME)
        assert a["_roundup_block"] in ("theme", "theme_adjacent")

    def test_released_article_is_never_relegated_to_the_offtheme_tail(self):
        # Even with nothing matching, an imported article is theme_adjacent —
        # the router already overruled the feed's `_is_bonus` by releasing it.
        a = _held_article("An article about nothing in particular", "nowt")
        pg._relabel_for_day(a, THURSDAY_THEME)
        a["_held_from"] = "2026-09-16"
        pg._annotate_roundup_blocks([a], THURSDAY_THEME)
        assert a["_roundup_block"] == "theme_adjacent"
        assert a["_roundup_block"] not in ("standalone", "kicker")

    def test_released_article_is_protected_from_the_roundup_cap(self):
        released = _held_article("[APTN News] Treaty talks resume", "rel")
        pg._relabel_for_day(released, THURSDAY_THEME)
        released["_held_from"] = "2026-09-16"
        filler = [_article(f"Gadget review number {i}", f"f{i}", kw=0)
                  for i in range(40)]
        kept, dropped = pg._curate_roundup_pool(
            [released] + filler, THURSDAY_THEME, pg.NEWS_ROUNDUP_COUNT
        )
        assert any(a["url"] == "rel" for a in kept)
        assert all(a["url"] != "rel" for a in dropped)

    def test_released_article_can_reach_the_deep_dive(self):
        released = _held_article("A held story with no keyword hits", "rel")
        pg._relabel_for_day(released, THURSDAY_THEME)
        released["_held_from"] = "2026-09-16"
        assert released["_keyword_matches"] == 0  # would have been weak_match
        deep, _news = pg.select_deep_dive_from_feed(
            [released] + [_article(f"Filler {i}", f"f{i}", kw=0) for i in range(5)],
            THURSDAY_THEME, count=3,
        )
        assert any(a["url"] == "rel" for a in deep)

    def test_a_genuine_keyword_match_still_outranks_an_imported_article(self):
        # `_imported` is the last sort key, so this promotes nothing.
        released = _held_article("A held story with no keyword hits", "rel")
        pg._relabel_for_day(released, THURSDAY_THEME)
        released["_held_from"] = "2026-09-16"
        strong = _article("Indigenous guardians expand treaty land stewardship",
                          "strong", kw=4, boosted=90)
        deep, _news = pg.select_deep_dive_from_feed(
            [released, strong], THURSDAY_THEME, count=1,
        )
        assert deep[0]["url"] == "strong"


class TestCharterScoreGuardsTheExport:
    def test_high_charter_fit_is_never_exported_despite_zero_keywords(self, holding_env):
        # The 2026-09-17 export: an IndigiNews feature on an Nlaka'pamux
        # community's wildfire-mitigation programme. Zero strict Thursday
        # keywords — the nation's name is not in the list and "[IndigiNews]" is
        # stripped as a source tag — and it matched Friday's wildfire slot twice.
        thursday = date(2026, 9, 17)
        goats = _article(
            "[IndigiNews] Call in the goats: Kanaka Bar calls on herd for help "
            "with wildfire risk", "goats-url", kw=0, boosted=69,
            summary="As wildfires gain intensity each year, this Nlaka'pamux "
                    "community is finding creative ways to prepare",
        )
        goats["_theme_score_raw"] = pg.HOLD_MIN_THEME_RAW
        theme, bonus = pg.route_articles_for_focus(
            _filler_pool() + [goats], [], thursday, THURSDAY_THEME, None
        )
        assert any(a["url"] == "goats-url" for a in theme + bonus)
        assert "goats-url" not in pg.load_memory(pg.HOLDING_FILE)

    def test_low_charter_fit_still_routes_to_its_day(self, holding_env):
        # The guard must not simply disable holding. Same shape, charter says no.
        thursday = date(2026, 9, 17)
        paleo = _article(
            "Wildfires near the South Pole burned 90 million years ago",
            "paleo-url", kw=0, boosted=40,
            summary="Ancient charcoal shows wildfire in Antarctic forests",
        )
        paleo["_theme_score_raw"] = 2
        # A pool that IS scored, so the rank bar is live and still declines to
        # protect a raw of 2 — rank without a floor is not evidence.
        theme, bonus = pg.route_articles_for_focus(
            _filler_pool() + [paleo], [], thursday, THURSDAY_THEME, None
        )
        assert all(a["url"] != "paleo-url" for a in theme + bonus)
        assert pg.load_memory(pg.HOLDING_FILE)["paleo-url"]["status"] == "held"

    def test_top_of_a_collapsed_pool_is_protected_by_rank(self, holding_env):
        # When the joint-scoring collapse holds a whole theme under the absolute
        # floor, nothing clears HOLD_MIN_THEME_RAW and only the relative bar
        # fires. This is the case that was live on 2026-09-17.
        thursday = date(2026, 9, 17)
        best = _article(
            "Wildfire crews trial a new mitigation programme", "best-url",
            kw=0, boosted=60, summary="A community prepares for fire season",
        )
        best["_theme_score_raw"] = pg.HOLD_MIN_THEME_RAW - 1
        pool = _filler_pool()
        for a in pool:
            a["_theme_score_raw"] = 3
        theme, bonus = pg.route_articles_for_focus(
            pool + [best], [], thursday, THURSDAY_THEME, None
        )
        assert any(a["url"] == "best-url" for a in theme + bonus)
        assert "best-url" not in pg.load_memory(pg.HOLDING_FILE)

    def test_missing_charter_score_changes_nothing(self, holding_env):
        # Scripts and feeds predating `_theme_score_raw` must route as before.
        thursday = date(2026, 9, 17)
        a = _article("Copper mine expansion clears drilling permit", "mine-url",
                     kw=0, boosted=50)
        assert "_theme_score_raw" not in a
        theme, bonus = pg.route_articles_for_focus(
            _filler_pool() + [a], [], thursday, THURSDAY_THEME, None
        )
        assert all(x["url"] != "mine-url" for x in theme + bonus)
        assert pg.load_memory(pg.HOLDING_FILE)["mine-url"]["status"] == "held"

    def test_rank_guard_stays_dark_when_the_pool_is_mostly_unscored(self, holding_env):
        # A feed predating `_theme_score_raw`, or one where most items lack it,
        # gives a ranking that says nothing. Better to hold as before than to
        # protect whichever article happens to carry the only number.
        thursday = date(2026, 9, 17)
        a = _article("Copper mine expansion clears drilling permit", "mine-url",
                     kw=0, boosted=50)
        a["_theme_score_raw"] = pg.HOLD_RANK_MIN_THEME_RAW + 1
        pool = _filler_pool()  # none of these carry a charter score
        theme, bonus = pg.route_articles_for_focus(
            pool + [a], [], thursday, THURSDAY_THEME, None
        )
        assert all(x["url"] != "mine-url" for x in theme + bonus)
        assert pg.load_memory(pg.HOLDING_FILE)["mine-url"]["status"] == "held"


class TestThinThemePoolIsReported:
    def test_empty_theme_block_on_a_themed_day_is_a_degradation(self):
        # 2026-09-17 aired 15 roundup stories with an empty theme block on
        # Indigenous Lands day and the run went green. The floor is the signal.
        assert pg.ROUNDUP_THEME_FLOOR > 0
        assert pg.THEME_POOL_FLOOR >= pg.ROUNDUP_THEME_FLOOR + 3

    def test_geographic_day_is_exempt_from_the_theme_floor(self):
        # Saturday has no theme block by construction — every candidate is local.
        assert pg._is_geographic_theme("Cariboo Local Affairs")
        assert not pg._is_geographic_theme(THURSDAY_THEME)


class TestCohereDeepDiveRerank:
    """USE_COHERE reranks the live deep-dive pick. It reorders the head of the
    rule-ranked list and never admits what the rules left out. On 2026-10-02
    the Tyee's "Municipal Campaign Trail" piece reached the Wild Spaces deep
    dive on the focus keyword "trail"."""

    RECREATION_FOCUS = {
        "slug": "recreation-access", "name": "Recreation & Backcountry Access",
        "keywords": ["trail", "backcountry", "hiking", "access", "camping"],
        "lens": "Center the deep dive on recreation and backcountry access.",
    }

    @pytest.fixture
    def rerank(self, monkeypatch):
        import cohere_enrichment as ce
        seen = {"calls": []}

        class _Client:
            def rerank(self, query, documents, model, top_n):
                seen["calls"].append({"query": query, "docs": documents, "top_n": top_n})
                order = seen.get("order") or list(range(len(documents)))
                return type("R", (), {"results": [type("H", (), {"index": i})()
                                                  for i in order[:top_n]]})()

        monkeypatch.setattr(ce, "COHERE_ENABLED", True)
        monkeypatch.setattr(ce, "_get_client", lambda: _Client())
        monkeypatch.setattr(ce, "_degradations", [])
        return seen

    def _focus_pool(self):
        return [
            _article("Harassment and Hate on the Municipal Campaign Trail", "tyee", kw=1, boosted=63),
            _article("Backcountry access road reopens to hikers", "road", kw=1, boosted=55),
            _article("Hiking trail stewardship crew expands", "crew", kw=1, boosted=50),
            _article("Camping fees rise at recreation sites", "fees", kw=1, boosted=45),
            _article("Celebrity gossip with no keywords", "gossip", kw=0, boosted=99),
        ]

    def _keyword_pool(self):
        """The keyword ranking puts the Tyee piece first, on a word coincidence."""
        pool = self._focus_pool()
        pool[0]["_keyword_matches"] = 2
        return pool

    def test_rerank_picks_from_the_rule_ranked_head(self, rerank):
        rerank["order"] = [1, 2, 3, 0]   # the model puts the Tyee piece last
        deep_dive, news = pg.select_deep_dive_from_feed(
            self._keyword_pool(), "Wild Spaces & Outdoor Life", count=3)
        assert [a["url"] for a in deep_dive] == ["road", "crew", "fees"]
        assert "tyee" in {a["url"] for a in news}

    def test_rerank_never_admits_what_the_rules_left_out(self, rerank):
        pg.select_deep_dive_from_feed(
            self._focus_pool(), "Wild Spaces & Outdoor Life", count=3,
            focus=self.RECREATION_FOCUS)
        docs = " ".join(rerank["calls"][0]["docs"])
        assert "Celebrity gossip" not in docs

    def test_query_is_theme_and_focus_never_the_anchor(self, rerank):
        pg.select_deep_dive_from_feed(
            self._focus_pool(), "Wild Spaces & Outdoor Life", count=3,
            focus=self.RECREATION_FOCUS)
        query = rerank["calls"][0]["query"]
        assert "Wild Spaces & Outdoor Life" in query
        assert "Recreation & Backcountry Access" in query
        assert "backcountry access" in query.lower()

    def test_pool_is_bounded(self, rerank):
        pool = [_article(f"Trail story {i}", f"u{i}", kw=1, boosted=50) for i in range(30)]
        pg.select_deep_dive_from_feed(pool, "Wild Spaces & Outdoor Life", count=3,
                                      focus=self.RECREATION_FOCUS)
        assert len(rerank["calls"][0]["docs"]) == pg.DEEP_DIVE_RERANK_POOL

    def test_nothing_to_choose_means_no_call(self, rerank):
        pool = self._focus_pool()[:3]
        pg.select_deep_dive_from_feed(pool, "Wild Spaces & Outdoor Life", count=3,
                                      focus=self.RECREATION_FOCUS)
        assert rerank["calls"] == []

    def test_the_geographic_day_is_never_reranked(self, rerank):
        pool = [_article(f"Williams Lake council votes on budget {i}", f"c{i}",
                         kw=1, boosted=50) for i in range(6)]
        pg.select_deep_dive_from_feed(pool, "Cariboo Local Affairs", count=3)
        assert rerank["calls"] == []

    def test_a_failed_rerank_keeps_the_keyword_ranking(self, monkeypatch):
        import cohere_enrichment as ce

        class _Broken:
            def rerank(self, **kw):
                raise RuntimeError("down")
        monkeypatch.setattr(ce, "COHERE_ENABLED", True)
        monkeypatch.setattr(ce, "_get_client", lambda: _Broken())
        monkeypatch.setattr(ce, "_degradations", [])
        deep_dive, _ = pg.select_deep_dive_from_feed(
            self._keyword_pool(), "Wild Spaces & Outdoor Life", count=3)
        assert [a["url"] for a in deep_dive] == ["tyee", "road", "crew"]
        assert any("deep-dive rerank" in d for d in ce.drain_degradations())

    def test_the_run_report_carries_cohere_fallbacks(self, monkeypatch):
        import cohere_enrichment as ce
        rows = []
        monkeypatch.setattr(pg, "degrade", lambda seg, detail: rows.append((seg, detail)))
        monkeypatch.setattr(ce, "_degradations", ["deep-dive rerank: failed"])
        pg._report_cohere_degradations("script/cohere")
        assert rows == [("script/cohere", "deep-dive rerank: failed")]


class TestOpenThreads:
    """2026-10-03: Casey said he'd watch the first post-vote agenda; nothing kept it."""
    CASEY = {"host": "casey", "query": "Williams Lake council first agenda deferred items",
             "watch_for": "Whether the deferred-items list is on the first agenda after the vote",
             "due": "2026-10-25"}

    @pytest.fixture
    def memory_env(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pg, "DEBATE_MEMORY_FILE", tmp_path / "debate_memory.json")
        monkeypatch.setattr(pg, "get_pacific_now", lambda: pg.datetime(2026, 10, 3, 9))
        pg._RUN_SEGMENTS.clear()
        pg._RESEARCH_LOG.clear()
        yield
        pg._RUN_SEGMENTS.clear()
        pg._RESEARCH_LOG.clear()

    def _save(self, *threads, theme="Cariboo Local Affairs", day="2026-10-03"):
        pg.update_debate_memory(day, theme, {"central_question": "q",
                                             "open_threads": list(threads)})
        return pg.get_debate_memory()

    def test_summary_schema_asks_for_open_threads(self):
        item = pg._debate_summary_schema()["properties"]["open_threads"]["items"]
        assert set(item["required"]) == {"host", "watch_for", "due", "query"}
        assert "calls_to_action" in pg._debate_summary_schema()["required"]

    def test_stored_threads_are_capped_dated_and_open(self, memory_env):
        stored = self._save(self.CASEY, {**self.CASEY, "due": "not a date"},
                            {**self.CASEY, "due": "2027-06-01"})["2026-10-03"]["open_threads"]
        assert len(stored) == pg.OPEN_THREAD_MAX_PER_EPISODE
        assert stored[0]["due"] == "2026-10-25" and stored[0]["status"] == "open"
        assert stored[1]["due"] == "2026-10-10"          # unreadable: a week out

    def test_due_date_is_clamped(self):
        far, past = pg._normalize_open_threads(
            [{**self.CASEY, "due": "2027-06-01"}, {**self.CASEY, "due": "2026-01-01"}],
            "2026-10-03")
        assert far["due"] == "2026-12-02" and past["due"] == "2026-10-04"

    def test_due_only_on_the_same_theme_on_or_after_due(self, memory_env):
        memory = self._save(self.CASEY)
        assert pg.due_open_threads(memory, "2026-10-24", "Cariboo Local Affairs") == []
        assert pg.due_open_threads(memory, "2026-10-26", "Science, Wonder & the Natural World") == []
        (due,) = pg.due_open_threads(memory, "2026-10-31", "Cariboo Local Affairs")
        assert due["opened"] == "2026-10-03"

    def test_search_uses_the_deep_dive_meter_since_the_thread_opened(self, monkeypatch):
        calls = []
        monkeypatch.setattr(pg, "_brave_deep_dive_open", lambda: True)
        monkeypatch.setattr(pg, "_brave_deep_dive_rate_limit", lambda q, k, count, freshness:
                            calls.append(freshness) or
                            [{"title": "Council agenda Nov 4", "url": "https://wltribune.com/x",
                              "description": "Deferred items listed."}])
        thread = {**self.CASEY, "opened": "2026-10-03"}
        block, found = pg.research_open_threads([thread], "key", "2026-10-31")
        assert calls == ["2026-10-03to2026-10-31"]
        assert found == {("2026-10-03", self.CASEY["watch_for"]): True}
        assert "Casey said on Saturday Oct 3" in block and "wltribune.com" in block
        assert "SOURCED OR UNSAID" in block

    def test_nothing_found_still_reaches_the_hosts(self, monkeypatch):
        monkeypatch.setattr(pg, "_brave_deep_dive_open", lambda: False)
        block, found = pg.research_open_threads(
            [{**self.CASEY, "opened": "2026-10-03"}], "key", "2026-10-31")
        assert "Nothing found since then" in block
        assert found == {("2026-10-03", self.CASEY["watch_for"]): None}

    def test_an_unsearched_offer_does_not_count_toward_expiry(self, memory_env):
        self._save(self.CASEY)
        offered = [{**pg.get_debate_memory()["2026-10-03"]["open_threads"][0], "opened": "2026-10-03"}]
        key = ("2026-10-03", self.CASEY["watch_for"])
        pg.record_open_threads(offered, {key: None}, "2026-10-31")
        pg.record_open_threads(offered, {key: None}, "2026-11-07")
        (t,) = pg.get_debate_memory()["2026-10-03"]["open_threads"]
        assert t["status"] == "open" and t["surfaced"] == []

    def test_sourced_thread_is_aired_once(self, memory_env):
        self._save(self.CASEY)
        offered = [{**pg.get_debate_memory()["2026-10-03"]["open_threads"][0], "opened": "2026-10-03"}]
        key = ("2026-10-03", self.CASEY["watch_for"])
        pg.record_open_threads(offered, {key: True}, "2026-10-31")
        pg.record_open_threads(offered, {key: True}, "2026-10-31")   # same-day re-run
        (t,) = pg.get_debate_memory()["2026-10-03"]["open_threads"]
        assert t["status"] == "aired" and t["surfaced"] == ["2026-10-31"]
        assert pg.due_open_threads(pg.get_debate_memory(), "2026-11-07", "Cariboo Local Affairs") == []

    def test_unsourced_thread_expires_after_two_offers_and_degrades(self, memory_env):
        self._save(self.CASEY)
        offered = [{**pg.get_debate_memory()["2026-10-03"]["open_threads"][0], "opened": "2026-10-03"}]
        key = ("2026-10-03", self.CASEY["watch_for"])
        pg.record_open_threads(offered, {key: False}, "2026-10-31")
        assert pg.get_debate_memory()["2026-10-03"]["open_threads"][0]["status"] == "open"
        assert not pg._RUN_SEGMENTS
        pg.record_open_threads(offered, {key: False}, "2026-11-07")
        assert pg.get_debate_memory()["2026-10-03"]["open_threads"][0]["status"] == "expired"
        assert pg._RUN_SEGMENTS[-1]["name"] == "script/open-threads"

    def test_thread_never_offered_expires_after_its_window(self, memory_env):
        self._save(self.CASEY)
        pg.record_open_threads([], {}, "2026-11-08")
        assert pg.get_debate_memory()["2026-10-03"]["open_threads"][0]["status"] == "open"
        pg.record_open_threads([], {}, "2026-11-09")
        assert pg.get_debate_memory()["2026-10-03"]["open_threads"][0]["status"] == "expired"
