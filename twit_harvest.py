#!/usr/bin/env python3
"""
Harvest editorial angles from TWIT Intelligent Machines episodes.

Fetches the RSS feed + each episode's show-notes page; extracts debate
questions and contrasting perspectives via Claude Haiku; caches results to
podcasts/twit_inspiration.json so podcast_generator.py can inject them as
editorial inspiration into the Deep Dive prompt.

Run weekly (harvest-twit.yml) or manually:
  python twit_harvest.py                 # harvest new episodes, save cache
  python twit_harvest.py --dry-run       # print extraction without saving
  python twit_harvest.py --max-episodes 4
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

import requests

from config_loader import message_text

PODCASTS_DIR = Path("podcasts")
CACHE_FILE = PODCASTS_DIR / "twit_inspiration.json"
CONFIG_FILE = Path("config/twit_sources.json")
HAIKU_MODEL = "claude-haiku-4-5-20251001"
MAX_BODY_CHARS = 4000
CACHE_VERSION = 1


# --------------------------------------------------------------------------- #
# HTML utilities (mirrors email_ingest._TextExtractor pattern)
# --------------------------------------------------------------------------- #

class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._skip = 0
        self._skip_tags = {"script", "style", "head", "nav", "footer"}

    def handle_starttag(self, tag, attrs):
        if tag in self._skip_tags:
            self._skip += 1
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self._skip_tags:
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)

    def get_text(self) -> str:
        return " ".join("".join(self._parts).split())


def _strip_html(html: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html)
        return p.get_text()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)


def _extract_external_links(html: str) -> list[str]:
    """Pull hrefs from show-notes HTML; skip TWIT/social links."""
    skip = ("twit.tv", "twitter.com", "x.com", "facebook.com", "instagram.com",
            "youtube.com", "linkedin.com", "javascript:", "#")
    hrefs = re.findall(r'href=["\']((https?://[^"\'>\s]+))["\']', html)
    return [href for href, _ in hrefs if not any(s in href for s in skip)][:20]


# --------------------------------------------------------------------------- #
# RSS feed parsing
# --------------------------------------------------------------------------- #

def fetch_twit_feed(feed_url: str, max_episodes: int = 8) -> list[dict]:
    """Fetch and parse podcast RSS; return up to max_episodes recent items."""
    try:
        resp = requests.get(feed_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except Exception as exc:
        print(f"  ⚠️  Feed fetch failed: {exc}")
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        print(f"  ⚠️  RSS parse error: {exc}")
        return []

    items = []
    for item in root.findall(".//item")[:max_episodes]:
        guid_el = item.find("guid")
        title_el = item.find("title")
        desc_el = item.find("description")
        link_el = item.find("link")
        pubdate_el = item.find("pubDate")

        guid = (guid_el.text or "").strip() if guid_el is not None else ""
        if not guid:
            continue

        items.append({
            "guid": guid,
            "title": (title_el.text or "").strip() if title_el is not None else "",
            "description_html": (desc_el.text or "") if desc_el is not None else "",
            "link": (link_el.text or "").strip() if link_el is not None else "",
            "pub_date": (pubdate_el.text or "").strip() if pubdate_el is not None else "",
        })

    return items


# --------------------------------------------------------------------------- #
# Episode page fetch (show notes are richer than RSS description)
# --------------------------------------------------------------------------- #

def fetch_episode_page(url: str) -> tuple[str, list[str]]:
    """
    Fetch the TWIT episode page for fuller show notes.
    Returns (plain_text_body, list_of_external_links).
    Falls back gracefully on any error.
    """
    if not url:
        return "", []
    try:
        resp = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        print(f"    Page fetch failed: {exc}")
        return "", []

    links = _extract_external_links(html)
    body = _strip_html(html)
    return body[:MAX_BODY_CHARS], links


# --------------------------------------------------------------------------- #
# Claude Haiku extraction
# --------------------------------------------------------------------------- #

_SYSTEM = (
    "You extract editorial content from AI podcast episodes to help a rural British Columbia "
    "tech podcast (Cariboo Signals) find debate angles it can adapt for its own coverage. "
    "The goal is identifying intellectual tensions and open questions — not copying content. "
    "Be specific. Prefer concrete questions over vague themes."
)

_USER_TMPL = """\
Episode: {title}

Content:
{content}

Extract as JSON only (no other text). Use null for any field you cannot fill meaningfully.

{{
  "question": "the core debate or central tension as one specific question",
  "perspectives": ["first viewpoint in ~15 words", "contrasting viewpoint in ~15 words"],
  "open_questions": ["specific unresolved tension 1", "specific unresolved tension 2"],
  "topics": ["specific technology, company, or policy topic 1", "topic 2", "topic 3"]
}}"""


def extract_inspiration(episode: dict, page_body: str, client) -> dict | None:
    """Call Claude Haiku to extract debate angles. Returns parsed dict or None."""
    rss_text = _strip_html(episode.get("description_html", ""))
    # Prefer page body (richer show notes); fall back to RSS description
    content = page_body if len(page_body) > len(rss_text) else rss_text
    if not content:
        print(f"    No content available — skipping")
        return None

    prompt = _USER_TMPL.format(title=episode["title"], content=content[:3500])

    try:
        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=400,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        usage = response.usage
        print(f"    Haiku: {usage.input_tokens} in / {usage.output_tokens} out tokens")

        raw = message_text(response).strip()
        # Strip markdown code fences if the model wraps its response
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        return json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"    JSON parse error: {exc}")
        return None
    except Exception as exc:
        print(f"    Extraction error: {exc}")
        return None


# --------------------------------------------------------------------------- #
# Cache management
# --------------------------------------------------------------------------- #

def load_inspiration_cache() -> dict:
    PODCASTS_DIR.mkdir(exist_ok=True)
    if not CACHE_FILE.exists():
        return {"version": CACHE_VERSION, "entries": {}}
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"version": CACHE_VERSION, "entries": {}}


def save_inspiration_cache(cache: dict) -> None:
    PODCASTS_DIR.mkdir(exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(cache, f, indent=2)
    tmp.replace(CACHE_FILE)


# --------------------------------------------------------------------------- #
# Main harvest
# --------------------------------------------------------------------------- #

def harvest(feed_url: str, max_episodes: int = 8, dry_run: bool = False) -> int:
    """
    Fetch feed, extract inspiration for uncached episodes, update cache.
    Returns count of newly processed episodes.
    """
    import anthropic  # ponytail: deferred import so load_relevant_inspiration works without API key
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    print(f"  Fetching: {feed_url}")
    episodes = fetch_twit_feed(feed_url, max_episodes=max_episodes)
    if not episodes:
        print("  No episodes returned.")
        return 0

    print(f"  {len(episodes)} episode(s) in feed")
    cache = load_inspiration_cache()
    entries = cache.setdefault("entries", {})

    new_count = 0
    for ep in episodes:
        guid = ep["guid"]
        if guid in entries:
            print(f"  [cached] {ep['title'][:70]}")
            continue

        print(f"  Processing: {ep['title'][:70]}")
        page_body, ext_links = fetch_episode_page(ep["link"])
        result = extract_inspiration(ep, page_body, client)

        if result is None:
            continue

        entry = {
            "title": ep["title"],
            "pub_date": ep["pub_date"],
            "episode_url": ep["link"],
            "harvested_at": datetime.now(timezone.utc).isoformat(),
            "question": result.get("question"),
            "perspectives": result.get("perspectives") or [],
            "open_questions": result.get("open_questions") or [],
            "topics": result.get("topics") or [],
            "discussed_links": ext_links[:10],
        }

        if dry_run:
            print(f"    [dry-run] entry:")
            print(json.dumps(entry, indent=6))
        else:
            entries[guid] = entry
            new_count += 1

    if not dry_run and new_count:
        save_inspiration_cache(cache)
        print(f"  ✅ Saved {new_count} new entry(ies) → {CACHE_FILE}")

    return new_count


# --------------------------------------------------------------------------- #
# Loader called by podcast_generator.py (no API key needed)
# --------------------------------------------------------------------------- #

def load_relevant_inspiration(max_items: int = 3, max_age_days: int = 30) -> list[dict]:
    """
    Return recent inspiration entries for prompt injection, newest first.
    Filters to entries with a non-null question and harvested within max_age_days.
    """
    cache = load_inspiration_cache()
    entries = cache.get("entries", {})
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    results = []
    for entry in entries.values():
        try:
            ts = datetime.fromisoformat(entry.get("harvested_at", ""))
            if ts < cutoff:
                continue
        except (ValueError, TypeError):
            continue
        if entry.get("question"):
            results.append(entry)

    results.sort(key=lambda e: e.get("harvested_at", ""), reverse=True)
    return results[:max_items]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _load_config() -> list[dict]:
    if not CONFIG_FILE.exists():
        print(f"⚠️  Config not found: {CONFIG_FILE}")
        return []
    with open(CONFIG_FILE) as f:
        data = json.load(f)
    return [s for s in data.get("feeds", []) if s.get("enabled", True)]


def main():
    parser = argparse.ArgumentParser(description="Harvest TWIT Intelligent Machines editorial inspiration")
    parser.add_argument("--dry-run", action="store_true", help="Print extraction without saving")
    parser.add_argument("--max-episodes", type=int, default=0,
                        help="Max episodes per feed (0 = use config value)")
    args = parser.parse_args()

    sources = _load_config()
    if not sources:
        print("No enabled sources configured. Check config/twit_sources.json.")
        sys.exit(1)

    total_new = 0
    for source in sources:
        print(f"\n📻 {source['name']}")
        max_ep = args.max_episodes or source.get("max_episodes", 8)
        total_new += harvest(source["url"], max_episodes=max_ep, dry_run=args.dry_run)

    print(f"\nDone. {total_new} new entry(ies) harvested.")


if __name__ == "__main__":
    main()
