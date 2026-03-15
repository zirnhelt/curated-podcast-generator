#!/usr/bin/env python3
"""
Content seeding tool for the Cariboo Signals podcast generator.

Allows you to bookmark articles or log thoughts for future episodes.
The podcast algorithm picks them up during the next generation run and
works them into a deep dive or discussion when the timing fits.

Usage:
  python seed.py url <url> [--note TEXT] [--priority normal|high] [--theme THEME] [--tag TAG]
  python seed.py thought <text> [--note TEXT] [--priority normal|high] [--theme THEME] [--tag TAG]
  python seed.py list [--all]
  python seed.py remove <id>

Examples:
  python seed.py url "https://example.com/mesh-networks" --note "great rural angle"
  python seed.py thought "What if communities owned their own LTE towers?"
  python seed.py url "https://..." --priority high --theme "Resilient Rural Futures"
  python seed.py url "https://..." --tag "billionaires"   # bespoke episode tag
  python seed.py list
  python seed.py remove abc123

Bespoke episodes:
  When 3+ seeds share the same --tag, a bespoke long-form debate episode is
  automatically triggered via generate_bespoke.py. Tags are free-form strings
  (e.g. "billionaires", "middle-east", "ai-regulation").
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


SEEDS_FILE = Path(__file__).parent / "podcasts" / "content_seeds.json"
TAGS_FILE  = Path(__file__).parent / "tags.json"

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


def _write_tags_json(data: dict) -> None:
    """Publish tags.json to the repo root for the iOS Shortcut to read.

    Fetched via raw.githubusercontent.com/main/tags.json — no auth required.
    The shortcut shows 'pending' tags as the primary list (active topics to add
    seeds to) and falls back to 'all' if the user wants a recently-used tag.

    Each entry is a plain string so iOS Shortcuts' 'Choose from List' works
    with zero extra steps.
    """
    seeds = data.get("seeds", [])

    # Pending tags — the ones the iOS Shortcut should highlight
    pending_counts: dict[str, int] = {}
    for s in seeds:
        t = s.get("tag")
        if t and s.get("status") == "pending":
            pending_counts[t] = pending_counts.get(t, 0) + 1

    # All tags ever used, most-recently-added first
    seen: set[str] = set()
    all_tags: list[str] = []
    for s in reversed(seeds):
        t = s.get("tag")
        if t and t not in seen:
            seen.add(t)
            all_tags.append(t)

    # Pending list sorted by seed count descending (busiest topic first)
    pending_tags = sorted(pending_counts, key=lambda t: -pending_counts[t])

    payload = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pending": pending_tags,
        "pending_counts": pending_counts,
        "all": all_tags,
    }
    with open(TAGS_FILE, "w") as f:
        json.dump(payload, f, indent=2)


def _save_seeds(data: dict) -> None:
    SEEDS_FILE.parent.mkdir(exist_ok=True)
    with open(SEEDS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    _write_tags_json(data)
    print(f"Saved to {SEEDS_FILE}")


BESPOKE_TRIGGER_THRESHOLD = 3


def check_bespoke_trigger(data: dict, tag: str) -> None:
    """Check if enough tagged seeds exist to trigger a bespoke episode.

    When the count of pending seeds with *tag* reaches BESPOKE_TRIGGER_THRESHOLD,
    dispatch the generate-bespoke.yml workflow via the GitHub API.

    Requires GITHUB_TOKEN and GITHUB_REPOSITORY environment variables
    (automatically available inside GitHub Actions).
    """
    tag_lower = tag.lower()
    pending_tagged = [
        s for s in data.get("seeds", [])
        if s.get("tag", "").lower() == tag_lower and s.get("status") == "pending"
    ]
    count = len(pending_tagged)
    print(f"  Tag '{tag_lower}': {count} pending seed(s) (trigger at {BESPOKE_TRIGGER_THRESHOLD})")

    if count < BESPOKE_TRIGGER_THRESHOLD:
        return

    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print(f"  Threshold reached! Run manually: python generate_bespoke.py --tag {tag_lower}")
        return

    import urllib.request
    url = f"https://api.github.com/repos/{repo}/actions/workflows/generate-bespoke.yml/dispatches"
    payload = json.dumps({"ref": "main", "inputs": {"tag": tag_lower}}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            if resp.status == 204:
                print(f"  Auto-triggered bespoke episode for tag '{tag_lower}'!")
            else:
                print(f"  Unexpected response {resp.status} when dispatching bespoke workflow")
    except Exception as e:
        print(f"  Could not dispatch bespoke workflow: {e}")
        print(f"  Run manually: python generate_bespoke.py --tag {tag_lower}")


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
        "tag": args.tag.lower() if args.tag else None,
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
    if args.tag:
        print(f"  Bespoke tag: {args.tag.lower()}")
    if args.priority == "high":
        print("  Priority: HIGH (wins deep dive selection when its theme day arrives)")
    if args.tag:
        check_bespoke_trigger(data, args.tag)


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
        "tag": args.tag.lower() if args.tag else None,
        "status": "pending",
        "used_on": None,
    }
    data["seeds"].append(seed)
    _save_seeds(data)
    print(f"Added thought seed [{seed['id']}]: {args.text[:80]}")
    if args.theme:
        print(f"  Theme hint: {args.theme}")
    if args.tag:
        print(f"  Bespoke tag: {args.tag.lower()}")
    if args.tag:
        check_bespoke_trigger(data, args.tag)


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
        if s.get("tag"):
            print(f"           bespoke tag: #{s['tag']}")
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
    p_url.add_argument("--tag", default=None,
                       help="Bespoke topic tag (e.g. 'billionaires'). When 3+ seeds share a tag, a bespoke episode is auto-generated.")

    # thought command
    p_thought = sub.add_parser("thought", help="Log a thought or question for further exploration")
    p_thought.add_argument("text", help="The thought, question, or angle to explore")
    p_thought.add_argument("--note", default=None, help="Additional context")
    p_thought.add_argument("--priority", choices=["normal", "high"], default="normal")
    p_thought.add_argument("--theme", default=None, help="Target theme hint")
    p_thought.add_argument("--tag", default=None,
                           help="Bespoke topic tag (same as --tag for url)")

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
