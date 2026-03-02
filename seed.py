#!/usr/bin/env python3
"""
Content seeding tool for the Cariboo Signals podcast generator.

Allows you to bookmark articles or log thoughts for future episodes.
The podcast algorithm picks them up during the next generation run and
works them into a deep dive or discussion when the timing fits.

Usage:
  python seed.py url <url> [--note TEXT] [--priority normal|high] [--theme THEME]
  python seed.py thought <text> [--note TEXT] [--priority normal|high] [--theme THEME]
  python seed.py list [--all]
  python seed.py remove <id>

Examples:
  python seed.py url "https://example.com/mesh-networks" --note "great rural angle"
  python seed.py thought "What if communities owned their own LTE towers?"
  python seed.py url "https://..." --priority high --theme "Resilient Rural Futures"
  python seed.py list
  python seed.py remove abc123
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


SEEDS_FILE = Path(__file__).parent / "podcasts" / "content_seeds.json"

THEMES = [
    "Arts, Culture & Digital Storytelling",
    "Working Lands & Industry",
    "Community Tech & Governance",
    "Indigenous Lands & Innovation",
    "Wild Spaces & Outdoor Life",
    "Cariboo Voices & Local News",
    "Resilient Rural Futures",
]


def _load_seeds() -> dict:
    if SEEDS_FILE.exists():
        with open(SEEDS_FILE) as f:
            return json.load(f)
    return {"version": 1, "seeds": []}


def _save_seeds(data: dict) -> None:
    SEEDS_FILE.parent.mkdir(exist_ok=True)
    with open(SEEDS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved to {SEEDS_FILE}")


def cmd_url(args) -> None:
    data = _load_seeds()
    seed = {
        "id": uuid.uuid4().hex[:8],
        "type": "url",
        "url": args.url,
        "title": None,
        "note": args.note,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "priority": args.priority,
        "theme_hint": args.theme,
        "status": "pending",
        "used_on": None,
    }
    data["seeds"].append(seed)
    _save_seeds(data)
    print(f"Added URL seed [{seed['id']}]: {args.url}")
    if args.note:
        print(f"  Note: {args.note}")
    if args.theme:
        print(f"  Theme hint: {args.theme}")
    if args.priority == "high":
        print("  Priority: HIGH (wins deep dive selection when its theme day arrives)")


def cmd_thought(args) -> None:
    data = _load_seeds()
    seed = {
        "id": uuid.uuid4().hex[:8],
        "type": "thought",
        "content": args.text,
        "note": args.note,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "priority": args.priority,
        "theme_hint": args.theme,
        "status": "pending",
        "used_on": None,
    }
    data["seeds"].append(seed)
    _save_seeds(data)
    print(f"Added thought seed [{seed['id']}]: {args.text[:80]}")
    if args.theme:
        print(f"  Theme hint: {args.theme}")


def cmd_list(args) -> None:
    data = _load_seeds()
    seeds = data.get("seeds", [])
    if not args.all:
        seeds = [s for s in seeds if s.get("status") == "pending"]

    if not seeds:
        print("No seeds found." if args.all else "No pending seeds.")
        return

    now = datetime.now(timezone.utc)
    for s in seeds:
        added = datetime.fromisoformat(s["added_at"])
        age_days = (now - added).days
        status = s.get("status", "pending")
        priority = s.get("priority", "normal")
        sid = s["id"]

        if s["type"] == "url":
            label = s.get("title") or s["url"]
            print(f"[{sid}] url  | {status:6s} | {priority:6s} | age={age_days}d | {label[:70]}")
        else:
            content = s.get("content", "")
            print(f"[{sid}] idea | {status:6s} | {priority:6s} | age={age_days}d | {content[:70]}")

        if s.get("note"):
            print(f"           note: {s['note']}")
        if s.get("best_theme_name"):
            day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            day_abbr = day_names[s["best_theme_day"]] if s.get("best_theme_day") is not None else "?"
            print(f"           queued: {s['best_theme_name']} ({day_abbr})")
        elif s.get("theme_hint"):
            print(f"           theme hint: {s['theme_hint']} (not yet rated)")
        if s.get("used_on"):
            print(f"           used: {s['used_on']}")


def cmd_remove(args) -> None:
    data = _load_seeds()
    before = len(data["seeds"])
    data["seeds"] = [s for s in data["seeds"] if s["id"] != args.id]
    if len(data["seeds"]) == before:
        print(f"No seed found with id '{args.id}'")
        sys.exit(1)
    _save_seeds(data)
    print(f"Removed seed {args.id}")


def main():
    parser = argparse.ArgumentParser(
        description="Seed content for the Cariboo Signals podcast generator."
    )
    sub = parser.add_subparsers(dest="command")

    # url command
    p_url = sub.add_parser("url", help="Seed an article URL for future deep dive")
    p_url.add_argument("url", help="Article URL to bookmark")
    p_url.add_argument("--note", default=None, help="Optional note or angle to explore")
    p_url.add_argument("--priority", choices=["normal", "high"], default="normal",
                       help="high = wins deep dive selection when its theme day arrives")
    p_url.add_argument("--theme", default=None,
                       help=f"Target theme hint (e.g. 'Resilient Rural Futures'). Options: {', '.join(THEMES)}")

    # thought command
    p_thought = sub.add_parser("thought", help="Log a thought or question for further exploration")
    p_thought.add_argument("text", help="The thought, question, or angle to explore")
    p_thought.add_argument("--note", default=None, help="Additional context")
    p_thought.add_argument("--priority", choices=["normal", "high"], default="normal")
    p_thought.add_argument("--theme", default=None, help="Target theme hint")

    # list command
    p_list = sub.add_parser("list", help="List seeds")
    p_list.add_argument("--all", action="store_true", help="Show used seeds too")

    # remove command
    p_remove = sub.add_parser("remove", help="Remove a seed by ID")
    p_remove.add_argument("id", help="Seed ID (from 'list')")

    args = parser.parse_args()

    if args.command == "url":
        cmd_url(args)
    elif args.command == "thought":
        cmd_thought(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "remove":
        cmd_remove(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
