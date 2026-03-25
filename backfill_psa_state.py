#!/usr/bin/env python3
"""
Backfill psa_rotation_state.json from existing podcast scripts.

Reads the COMMUNITY SPOTLIGHT section of every podcast_script_*.txt file,
matches the featured organization to psa_organizations.json, and records
the most-recent air date per org in the last_aired dict.

Also reconstructs the rotation index per weekday from the last-aired org.

Run once after pulling the repo to seed the state file before the next
daily generation, so the round-robin picks up where the scripts left off.
"""

import json
import re
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PODCASTS_DIR = SCRIPT_DIR / "podcasts"
PSA_STATE_FILE = PODCASTS_DIR / "psa_rotation_state.json"
PSA_ORGS_FILE = SCRIPT_DIR / "config" / "psa_organizations.json"


def load_organizations():
    with open(PSA_ORGS_FILE) as f:
        return json.load(f)["organizations"]


def extract_psa_from_script(script_path):
    """Return the organization name mentioned in the COMMUNITY SPOTLIGHT section."""
    text = script_path.read_text(encoding="utf-8", errors="replace")

    # Find the COMMUNITY SPOTLIGHT block
    match = re.search(
        r"\*\*COMMUNITY SPOTLIGHT\*\*\s*\n(.*?)(?=\n\*\*[A-Z]|\Z)",
        text,
        re.DOTALL,
    )
    if not match:
        return None

    spotlight_text = match.group(1)[:600]  # first 600 chars is plenty
    return spotlight_text


def match_org(spotlight_text, organizations):
    """Return (org_id, org_data) for the org most prominently mentioned."""
    spotlight_lower = spotlight_text.lower()
    best_id = None
    best_score = 0

    for org_id, org in organizations.items():
        # Score by how many words of the org name appear in the spotlight text
        name_words = [w.lower() for w in org["name"].split() if len(w) > 3]
        short_words = [w.lower() for w in org["short_name"].split() if len(w) > 3]
        all_words = name_words + short_words

        score = sum(1 for w in all_words if w in spotlight_lower)
        # Bonus for full name match
        if org["name"].lower() in spotlight_lower:
            score += 5
        if org.get("short_name", "").lower() in spotlight_lower:
            score += 3

        if score > best_score:
            best_score = score
            best_id = org_id

    if best_score >= 2:
        return best_id, organizations[best_id]
    return None, None


def get_weekday_roster(weekday, organizations):
    """Return ordered list of org_ids assigned to a weekday."""
    return [
        org_id
        for org_id, org in organizations.items()
        if weekday in org["weekdays"]
    ]


def backfill():
    organizations = load_organizations()

    # Collect (date, weekday, org_id) for every script
    records = []
    for script_path in sorted(PODCASTS_DIR.glob("podcast_script_*.txt")):
        name = script_path.stem  # e.g. podcast_script_2026-03-24_working_lands_and_industry
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", name)
        if not date_match:
            continue
        episode_date = date.fromisoformat(date_match.group(1))
        weekday = episode_date.weekday()

        spotlight_text = extract_psa_from_script(script_path)
        if not spotlight_text:
            continue

        org_id, org = match_org(spotlight_text, organizations)
        if org_id:
            records.append((episode_date, weekday, org_id))

    if not records:
        print("No records found — nothing to backfill.")
        return

    # Build last_aired: keep the most recent date per org
    last_aired = {}
    for episode_date, weekday, org_id in records:
        prev = last_aired.get(org_id)
        if prev is None or episode_date > date.fromisoformat(prev):
            last_aired[org_id] = episode_date.isoformat()

    # Build rotation index: for each weekday, find the index of the last-aired org
    rotation = {}
    # Group records by weekday, take the most recent per weekday
    last_per_weekday = {}
    for episode_date, weekday, org_id in sorted(records):
        last_per_weekday[weekday] = (episode_date, org_id)

    for weekday, (_, org_id) in last_per_weekday.items():
        roster = get_weekday_roster(weekday, organizations)
        if org_id in roster:
            rotation[str(weekday)] = roster.index(org_id)
        else:
            rotation[str(weekday)] = 0

    state = {"rotation": rotation, "last_aired": last_aired}

    # Write state
    PODCASTS_DIR.mkdir(exist_ok=True)
    with open(PSA_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

    print(f"✅ Backfilled PSA rotation state to {PSA_STATE_FILE}")
    print(f"   {len(last_aired)} organizations recorded in last_aired")
    print(f"   Rotation indices: {rotation}")
    print()
    print("Last aired dates:")
    for org_id, aired_date in sorted(last_aired.items(), key=lambda x: x[1], reverse=True):
        org_name = organizations.get(org_id, {}).get("short_name", org_id)
        print(f"  {aired_date}  {org_name} ({org_id})")


if __name__ == "__main__":
    backfill()
