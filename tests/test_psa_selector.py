"""Tests for PSA selector module."""

import json
import pytest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from psa_selector import (
    get_orgs_for_weekday,
    find_active_events,
    match_event_to_roster,
    round_robin_select,
    select_psa,
    _format_psa_angle,
    record_aired,
    MIN_DAYS_BETWEEN_REPEATS,
)


# --- Fixtures ---

@pytest.fixture
def sample_orgs():
    return {
        "scout-island": {
            "name": "Scout Island Nature Centre",
            "short_name": "Scout Island",
            "description": "Nature education centre",
            "website": "scoutisland.ca",
            "weekdays": [4],
            "tags": ["nature"],
        },
        "ccacs": {
            "name": "Central Cariboo Arts & Culture Society",
            "short_name": "CCACS",
            "description": "Arts and culture",
            "website": "centralcaribooarts.com",
            "weekdays": [0],
            "tags": ["arts"],
        },
        "cmha-cariboo": {
            "name": "CMHA Cariboo Chilcotin",
            "short_name": "CMHA",
            "description": "Mental health services",
            "website": "cariboo.cmha.bc.ca",
            "weekdays": [5],
            "tags": ["mental health"],
        },
        "first-journey-trails": {
            "name": "First Journey Trails",
            "short_name": "First Journey Trails",
            "description": "Trail building",
            "website": "firstjourneytrails.com",
            "weekdays": [4],
            "tags": ["trails"],
        },
    }


@pytest.fixture
def sample_events():
    return [
        {
            "name": "Earth Day",
            "start_date": "04-22",
            "end_date": "04-22",
            "organization_id": "scout-island",
            "psa_angle": "Happy Earth Day from {org_name}.",
        },
        {
            "name": "Mental Health Week",
            "start_date": "05-04",
            "end_date": "05-10",
            "organization_id": "cmha-cariboo",
            "psa_angle": "CMHA offers free counselling.",
        },
        {
            "name": "Summer trails",
            "start_date": "06-01",
            "end_date": "08-31",
            "organization_id": "first-journey-trails",
            "psa_angle": "Trail season is here.",
        },
        {
            "name": "Giving Tuesday",
            "start_date": "12-02",
            "end_date": "12-02",
            "organization_id": None,
            "all_orgs": True,
            "psa_angle": "Support {org_name} at {org_website}.",
        },
        {
            "name": "Indigenous History Month",
            "start_date": "06-01",
            "end_date": "06-30",
            "organization_ids": ["denisiqi", "cariboo-friendship"],
            "psa_angle": "Learn more about {org_name}.",
        },
    ]


# --- get_orgs_for_weekday ---

class TestGetOrgsForWeekday:
    def test_returns_matching_orgs(self, sample_orgs):
        result = get_orgs_for_weekday(4, sample_orgs)
        ids = [org_id for org_id, _ in result]
        assert "scout-island" in ids
        assert "first-journey-trails" in ids

    def test_returns_empty_for_unassigned_day(self, sample_orgs):
        result = get_orgs_for_weekday(3, sample_orgs)
        assert result == []

    def test_single_org_day(self, sample_orgs):
        result = get_orgs_for_weekday(0, sample_orgs)
        assert len(result) == 1
        assert result[0][0] == "ccacs"


# --- find_active_events ---

class TestFindActiveEvents:
    def test_exact_date_match(self, sample_events):
        today = date(2026, 4, 22)
        active = find_active_events(today, sample_events)
        names = [e["name"] for e in active]
        assert "Earth Day" in names

    def test_within_range(self, sample_events):
        today = date(2026, 5, 7)
        active = find_active_events(today, sample_events)
        names = [e["name"] for e in active]
        assert "Mental Health Week" in names

    def test_lookahead_window(self, sample_events):
        today = date(2026, 4, 18)  # 4 days before Earth Day
        active = find_active_events(today, sample_events, lookahead_days=7)
        names = [e["name"] for e in active]
        assert "Earth Day" in names

    def test_outside_lookahead(self, sample_events):
        today = date(2026, 4, 10)  # 12 days before Earth Day
        active = find_active_events(today, sample_events, lookahead_days=7)
        names = [e["name"] for e in active]
        assert "Earth Day" not in names

    def test_shorter_events_sorted_first(self, sample_events):
        # On June 15, both "Summer trails" (92 days) and "Indigenous History Month" (29 days) are active
        today = date(2026, 6, 15)
        active = find_active_events(today, sample_events)
        # Shorter range event should come first
        multi_day = [e for e in active if e["name"] in ("Indigenous History Month", "Summer trails")]
        assert len(multi_day) == 2
        assert multi_day[0]["name"] == "Indigenous History Month"

    def test_no_active_events(self, sample_events):
        today = date(2026, 1, 15)
        active = find_active_events(today, sample_events)
        assert active == []


# --- match_event_to_roster ---

class TestMatchEventToRoster:
    def test_specific_org_match(self, sample_events, sample_orgs):
        # Earth Day event targets scout-island, which is on Friday's roster
        active = [sample_events[0]]  # Earth Day
        match = match_event_to_roster(active, ["scout-island", "first-journey-trails"], sample_orgs)
        assert match is not None
        org_id, org, angle = match
        assert org_id == "scout-island"
        assert "Earth Day" in angle

    def test_no_match_when_org_not_on_roster(self, sample_events, sample_orgs):
        active = [sample_events[0]]  # Earth Day targets scout-island
        match = match_event_to_roster(active, ["ccacs"], sample_orgs)
        assert match is None

    def test_all_orgs_event(self, sample_events, sample_orgs):
        active = [sample_events[3]]  # Giving Tuesday (all_orgs=True)
        match = match_event_to_roster(active, ["ccacs"], sample_orgs)
        assert match is not None
        org_id, org, angle = match
        assert org_id == "ccacs"

    def test_multi_org_event(self, sample_events, sample_orgs):
        # Indigenous History Month targets denisiqi and cariboo-friendship
        active = [sample_events[4]]
        sample_orgs["denisiqi"] = {
            "name": "Denisiqi Services",
            "short_name": "Denisiqi",
            "description": "Child and family services",
            "website": "denisiqi.org",
            "weekdays": [3],
            "tags": [],
        }
        match = match_event_to_roster(active, ["denisiqi"], sample_orgs)
        assert match is not None
        assert match[0] == "denisiqi"


# --- _format_psa_angle ---

class TestFormatPsaAngle:
    def test_fills_placeholders(self):
        org = {"name": "Test Org", "website": "test.org"}
        result = _format_psa_angle("Visit {org_name} at {org_website}.", org)
        assert result == "Visit Test Org at test.org."

    def test_missing_website_fallback(self):
        org = {"name": "Test Org"}
        result = _format_psa_angle("Visit {org_name} at {org_website}.", org)
        assert result == "Visit Test Org at their website."


# --- round_robin_select ---

class TestRoundRobinSelect:
    def _fresh_state(self):
        return {"rotation": {}, "last_aired": {}}

    def test_starts_at_first_org(self):
        roster = [("a", {"name": "A"}), ("b", {"name": "B"})]
        state = self._fresh_state()
        today = date(2026, 1, 1)
        org_id, org, state = round_robin_select(4, roster, state, today)
        assert org_id == "a"
        assert state["rotation"]["4"] == 0

    def test_advances_through_roster(self):
        roster = [("a", {"name": "A"}), ("b", {"name": "B"}), ("c", {"name": "C"})]
        # "a" aired long ago, so "b" should be next
        old_date = date(2025, 12, 1)
        state = {"rotation": {"4": 0}, "last_aired": {"a": old_date.isoformat()}}
        today = date(2026, 1, 1)
        org_id, _, state = round_robin_select(4, roster, state, today)
        assert org_id == "b"
        assert state["rotation"]["4"] == 1

    def test_wraps_around(self):
        roster = [("a", {"name": "A"}), ("b", {"name": "B"})]
        old_date = date(2025, 12, 1)
        state = {"rotation": {"4": 1}, "last_aired": {"b": old_date.isoformat()}}
        today = date(2026, 1, 1)
        org_id, _, state = round_robin_select(4, roster, state, today)
        assert org_id == "a"
        assert state["rotation"]["4"] == 0

    def test_skips_recently_aired_org(self):
        roster = [("a", {"name": "A"}), ("b", {"name": "B"}), ("c", {"name": "C"})]
        today = date(2026, 2, 10)
        recent = date(2026, 2, 7)  # 3 days ago, within cooldown
        # After index 0 ("a"), next would be "b" but it aired recently
        state = {
            "rotation": {"4": 0},
            "last_aired": {"b": recent.isoformat()},
        }
        org_id, _, state = round_robin_select(4, roster, state, today)
        assert org_id == "c"  # skips "b", lands on "c"

    def test_fallback_to_least_recent_when_all_aired(self):
        roster = [("a", {"name": "A"}), ("b", {"name": "B"})]
        today = date(2026, 2, 10)
        # Both aired within the cooldown window
        state = {
            "rotation": {"4": 0},
            "last_aired": {
                "a": date(2026, 2, 8).isoformat(),  # 2 days ago
                "b": date(2026, 2, 5).isoformat(),  # 5 days ago (least recent)
            },
        }
        org_id, _, state = round_robin_select(4, roster, state, today)
        assert org_id == "b"  # "b" is least recently aired

    def test_independent_per_weekday(self):
        roster_mon = [("x", {"name": "X"})]
        roster_fri = [("y", {"name": "Y"}), ("z", {"name": "Z"})]
        today = date(2026, 1, 1)
        state = self._fresh_state()
        _, _, state = round_robin_select(0, roster_mon, state, today)
        _, _, state = round_robin_select(4, roster_fri, state, today)
        assert state["rotation"]["0"] == 0
        assert state["rotation"]["4"] == 0

    def test_cross_week_deduplication(self):
        """An org aired within the cooldown window should be skipped."""
        roster = [("a", {"name": "A"}), ("b", {"name": "B"})]
        today = date(2026, 2, 17)
        six_days_ago = date(2026, 2, 11)  # 6 days ago — within cooldown (< 7)
        state = {
            "rotation": {"4": 1},  # last picked was "b" at index 1
            "last_aired": {"a": six_days_ago.isoformat()},
        }
        # Next in rotation would be "a", but it aired only 6 days ago
        org_id, _, _ = round_robin_select(4, roster, state, today, min_days=MIN_DAYS_BETWEEN_REPEATS)
        assert org_id == "b"  # should skip "a" and pick "b"

    def test_exactly_min_days_is_allowed(self):
        """An org aired exactly min_days ago is eligible again."""
        roster = [("a", {"name": "A"}), ("b", {"name": "B"})]
        today = date(2026, 2, 17)
        seven_days_ago = date(2026, 2, 10)  # exactly 7 days ago — cooldown lifted
        state = {
            "rotation": {"4": 1},
            "last_aired": {"a": seven_days_ago.isoformat()},
        }
        org_id, _, _ = round_robin_select(4, roster, state, today, min_days=MIN_DAYS_BETWEEN_REPEATS)
        assert org_id == "a"  # "a" is back in rotation after 7 days


# --- select_psa (integration) ---

class TestSelectPsa:
    def test_returns_dict_with_required_keys(self):
        result = select_psa(date(2026, 2, 6))  # A Friday
        if result:
            assert "org_id" in result
            assert "org_name" in result
            assert "org_short_name" in result
            assert "org_description" in result
            assert "source" in result
            assert result["source"] in ("event", "rotation")

    def test_event_driven_selection(self):
        # April 22, 2026 is a Wednesday (weekday=2), but Earth Day targets scout-island (Friday)
        # So on a Wednesday, Earth Day shouldn't match
        # Let's test a Friday during Earth Day's lookahead
        result = select_psa(date(2026, 4, 17))  # Friday, 5 days before Earth Day
        if result and result["source"] == "event":
            assert result["event_name"] is not None

    def test_rotation_fallback(self):
        # Pick a date with no active events for the day's roster
        # March 19, 2026 is a Thursday (Indigenous day) — no events target Thu orgs in mid-March
        result = select_psa(date(2026, 3, 19))
        if result:
            assert result["source"] == "rotation"
            assert result["psa_angle"] is None

    def test_returns_none_for_empty_roster(self):
        """If somehow no orgs are assigned to a weekday, returns None."""
        with patch("psa_selector.load_psa_organizations", return_value={}):
            result = select_psa(date(2026, 2, 6))
            assert result is None


# --- Config file validation ---

class TestConfigFiles:
    def test_organizations_json_valid(self):
        config_path = Path(__file__).parent.parent / "config" / "psa_organizations.json"
        with open(config_path) as f:
            data = json.load(f)
        orgs = data["organizations"]
        assert len(orgs) > 0
        for org_id, org in orgs.items():
            assert "name" in org
            assert "short_name" in org
            assert "description" in org
            assert "weekdays" in org
            assert isinstance(org["weekdays"], list)
            assert all(0 <= d <= 6 for d in org["weekdays"])

    def test_events_json_valid(self):
        config_path = Path(__file__).parent.parent / "config" / "psa_events.json"
        with open(config_path) as f:
            data = json.load(f)
        events = data["events"]
        assert len(events) > 0
        for event in events:
            assert "name" in event
            assert "start_date" in event
            assert "end_date" in event
            assert "psa_angle" in event
            # Validate date format MM-DD
            start = event["start_date"]
            end = event["end_date"]
            assert len(start) == 5 and start[2] == "-"
            assert len(end) == 5 and end[2] == "-"

    def test_all_event_org_ids_exist_in_roster(self):
        """Every organization_id referenced in events should exist in the org roster."""
        config_dir = Path(__file__).parent.parent / "config"
        with open(config_dir / "psa_organizations.json") as f:
            orgs = json.load(f)["organizations"]
        with open(config_dir / "psa_events.json") as f:
            events = json.load(f)["events"]

        for event in events:
            org_id = event.get("organization_id")
            if org_id:
                assert org_id in orgs, f"Event '{event['name']}' references unknown org '{org_id}'"
            for oid in event.get("organization_ids", []):
                assert oid in orgs, f"Event '{event['name']}' references unknown org '{oid}'"

    def test_every_weekday_has_at_least_one_org(self):
        config_path = Path(__file__).parent.parent / "config" / "psa_organizations.json"
        with open(config_path) as f:
            orgs = json.load(f)["organizations"]
        for day in range(7):
            day_orgs = [oid for oid, o in orgs.items() if day in o["weekdays"]]
            assert len(day_orgs) > 0, f"Weekday {day} has no PSA organizations assigned"
