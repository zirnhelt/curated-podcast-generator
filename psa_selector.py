#!/usr/bin/env python3
"""
PSA selector for Cariboo Signals podcast.
Selects a community organization to feature based on:
1. Event-driven: upcoming awareness dates or local events
2. Round-robin fallback: cycles through the day's roster
"""

import json
from datetime import date
from pathlib import Path

CONFIG_DIR = Path(__file__).parent / "config"
PODCASTS_DIR = Path(__file__).parent / "podcasts"
PSA_STATE_FILE = PODCASTS_DIR / "psa_rotation_state.json"

EVENT_LOOKAHEAD_DAYS = 7


def load_psa_organizations():
    """Load PSA organizations config."""
    with open(CONFIG_DIR / "psa_organizations.json", "r") as f:
        return json.load(f)["organizations"]


def load_psa_events():
    """Load PSA events calendar."""
    with open(CONFIG_DIR / "psa_events.json", "r") as f:
        return json.load(f)["events"]


def load_rotation_state():
    """Load round-robin rotation state from disk."""
    if PSA_STATE_FILE.exists():
        with open(PSA_STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_rotation_state(state):
    """Persist rotation state to disk."""
    PODCASTS_DIR.mkdir(exist_ok=True)
    with open(PSA_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def get_orgs_for_weekday(weekday, organizations):
    """Return list of (org_id, org_data) tuples assigned to a weekday."""
    return [
        (org_id, org)
        for org_id, org in organizations.items()
        if weekday in org["weekdays"]
    ]


def find_active_events(today, events, lookahead_days=EVENT_LOOKAHEAD_DAYS):
    """Find events that are active today or start within the lookahead window.

    Returns events sorted by specificity: single-day events first, then shorter
    ranges, then longer ranges. This ensures a specific awareness day (like
    Earth Day) takes priority over a broad seasonal event.
    """
    today_mmdd = today.strftime("%m-%d")
    year = today.year

    active = []
    for event in events:
        start_mmdd = event["start_date"]
        end_mmdd = event["end_date"]

        try:
            start = date(year, int(start_mmdd[:2]), int(start_mmdd[3:]))
            end = date(year, int(end_mmdd[:2]), int(end_mmdd[3:]))
        except ValueError:
            continue

        # Event is active if today falls within its date range
        if start <= today <= end:
            duration = (end - start).days
            active.append((duration, event))
            continue

        # Or if the event starts within the lookahead window
        days_until = (start - today).days
        if 0 < days_until <= lookahead_days:
            duration = (end - start).days
            active.append((duration, event))

    # Sort by duration (shorter/more specific events first)
    active.sort(key=lambda x: x[0])
    return [event for _, event in active]


def match_event_to_roster(active_events, roster_org_ids, organizations):
    """Find the best event match for today's roster.

    Returns (org_id, org_data, psa_angle) or None.
    """
    for event in active_events:
        # Event targets a specific org
        target_id = event.get("organization_id")
        if target_id and target_id in roster_org_ids:
            org = organizations[target_id]
            angle = _format_psa_angle(event["psa_angle"], org)
            return target_id, org, angle

        # Event targets multiple specific orgs (rotate through them)
        target_ids = event.get("organization_ids", [])
        for tid in target_ids:
            if tid in roster_org_ids:
                org = organizations[tid]
                angle = _format_psa_angle(event["psa_angle"], org)
                return tid, org, angle

        # Event applies to all orgs (Volunteer Week, Giving Tuesday, etc.)
        if event.get("all_orgs") and roster_org_ids:
            # Pick first org on the roster for this "all orgs" event
            first_id = roster_org_ids[0]
            org = organizations[first_id]
            angle = _format_psa_angle(event["psa_angle"], org)
            return first_id, org, angle

    return None


def _format_psa_angle(angle_template, org):
    """Fill in {org_name} and {org_website} placeholders in PSA angle text."""
    return angle_template.format(
        org_name=org["name"],
        org_website=org.get("website", "their website"),
    )


def round_robin_select(weekday, roster, state):
    """Select the next org in rotation for this weekday.

    Returns (org_id, org_data, updated_state).
    """
    key = str(weekday)
    last_index = state.get(key, -1)
    next_index = (last_index + 1) % len(roster)
    state[key] = next_index
    org_id, org_data = roster[next_index]
    return org_id, org_data, state


def select_psa(today=None):
    """Select today's PSA organization and talking points.

    Returns a dict with:
        org_id: str
        org_name: str
        org_short_name: str
        org_description: str
        org_website: str | None
        psa_angle: str | None  (event-specific angle, or None for round-robin)
        event_name: str | None
        source: "event" | "rotation"
    """
    if today is None:
        today = date.today()

    organizations = load_psa_organizations()
    events = load_psa_events()

    weekday = today.weekday()
    roster = get_orgs_for_weekday(weekday, organizations)

    if not roster:
        return None

    roster_org_ids = [org_id for org_id, _ in roster]

    # Try event-driven selection first
    active_events = find_active_events(today, events)
    match = match_event_to_roster(active_events, roster_org_ids, organizations)

    if match:
        org_id, org, psa_angle = match
        # Find which event matched for metadata
        event_name = None
        for event in active_events:
            target = event.get("organization_id")
            targets = event.get("organization_ids", [])
            if target == org_id or org_id in targets or event.get("all_orgs"):
                event_name = event["name"]
                break

        return {
            "org_id": org_id,
            "org_name": org["name"],
            "org_short_name": org["short_name"],
            "org_description": org["description"],
            "org_website": org.get("website"),
            "psa_angle": psa_angle,
            "event_name": event_name,
            "source": "event",
        }

    # Fall back to round-robin
    state = load_rotation_state()
    org_id, org, state = round_robin_select(weekday, roster, state)
    save_rotation_state(state)

    return {
        "org_id": org_id,
        "org_name": org["name"],
        "org_short_name": org["short_name"],
        "org_description": org["description"],
        "org_website": org.get("website"),
        "psa_angle": None,
        "event_name": None,
        "source": "rotation",
    }


if __name__ == "__main__":
    result = select_psa()
    if result:
        print(f"Today's PSA: {result['org_name']}")
        print(f"  Source: {result['source']}")
        if result["event_name"]:
            print(f"  Event: {result['event_name']}")
        if result["psa_angle"]:
            print(f"  Angle: {result['psa_angle']}")
        else:
            print(f"  Description: {result['org_description']}")
    else:
        print("No PSA organizations configured for today.")
