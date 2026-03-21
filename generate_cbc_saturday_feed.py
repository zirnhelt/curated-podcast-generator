#!/usr/bin/env python3
"""
generate_cbc_saturday_feed.py — Build cbc-saturday-feed.xml

Scans podcasts/cbc_saturday_*.mp3, derives pub-dates from filenames, and writes
a podcast-compatible RSS feed.

Usage:
    python generate_cbc_saturday_feed.py [--base-url URL]
"""

from __future__ import annotations

import argparse
import os
import xml.sax.saxutils as saxutils
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PODCASTS_DIR = SCRIPT_DIR / "podcasts"
FEED_FILE = SCRIPT_DIR / "cbc-saturday-feed.xml"

FEED_CONFIG = {
    "title": "Cariboo Saturday Morning — Cariboo Mix",
    "description": (
        "A curated Saturday morning radio experience drawn from CBC podcasts "
        "(World Report, BC Today, Kamloops News, q, Unreserved), "
        "with Canadian indie music transitions from Jamendo. "
        "Assembled automatically each Saturday for the Cariboo region of BC."
    ),
    "author": "Cariboo Signals",
    "language": "en-ca",
    "explicit": False,
    "category": "News",
}


def _estimate_duration(size_bytes: int) -> str:
    """Rough duration estimate: ~128 kbps MP3 → 16 KB/s."""
    seconds = size_bytes // 16_000
    return f"{seconds // 60}:{seconds % 60:02d}"


def generate_feed(base_url: str) -> None:
    mp3s = sorted(PODCASTS_DIR.glob("cbc_saturday_*.mp3"), reverse=True)
    if not mp3s:
        print("  [WARN] No cbc_saturday_*.mp3 files found — feed will be empty.")

    episodes = []
    for mp3 in mp3s[:20]:  # keep last 20
        # filename: cbc_saturday_YYYY-MM-DD.mp3
        stem = mp3.stem  # cbc_saturday_2026-03-22
        date_str = stem.replace("cbc_saturday_", "")
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d").replace(
                hour=16, tzinfo=timezone.utc
            )
            pub_date = dt.strftime("%a, %d %b %Y %H:%M:%S GMT")
            title = f"Cariboo Saturday Morning — {dt.strftime('%B %-d, %Y')}"
        except ValueError:
            pub_date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
            title = f"Cariboo Saturday Morning — {stem}"

        size = mp3.stat().st_size
        url = f"{base_url}podcasts/{mp3.name}"
        guid = f"cbc-saturday-{date_str}"
        duration = _estimate_duration(size)

        episodes.append(
            {
                "title": title,
                "pub_date": pub_date,
                "url": url,
                "size": size,
                "guid": guid,
                "duration": duration,
            }
        )

    cfg = FEED_CONFIG
    feed_url = f"{base_url}cbc-saturday-feed.xml"
    now_rfc = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">',
        "<channel>",
        f"<title>{saxutils.escape(cfg['title'])}</title>",
        f"<link>{saxutils.escape(base_url)}</link>",
        f"<language>{cfg['language']}</language>",
        f"<description>{saxutils.escape(cfg['description'])}</description>",
        f"<itunes:author>{saxutils.escape(cfg['author'])}</itunes:author>",
        f"<itunes:summary>{saxutils.escape(cfg['description'])}</itunes:summary>",
        f'<atom:link href="{saxutils.escape(feed_url)}" rel="self" type="application/rss+xml" xmlns:atom="http://www.w3.org/2005/Atom"/>',
        f"<itunes:explicit>{'true' if cfg['explicit'] else 'false'}</itunes:explicit>",
        "<itunes:type>episodic</itunes:type>",
        f'<itunes:category text="{saxutils.escape(cfg["category"])}"/>',
        f"<lastBuildDate>{now_rfc}</lastBuildDate>",
    ]

    for ep in episodes:
        lines += [
            "<item>",
            f"<title>{saxutils.escape(ep['title'])}</title>",
            f"<pubDate>{ep['pub_date']}</pubDate>",
            f"<description>{saxutils.escape(ep['title'])}</description>",
            f'<enclosure url="{saxutils.escape(ep["url"])}" length="{ep["size"]}" type="audio/mpeg"/>',
            f'<guid isPermaLink="false">{ep["guid"]}</guid>',
            f"<itunes:duration>{ep['duration']}</itunes:duration>",
            "<itunes:explicit>false</itunes:explicit>",
            "</item>",
        ]

    lines += ["</channel>", "</rss>"]

    FEED_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Feed written: {FEED_FILE} ({len(episodes)} episode(s))")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default=os.getenv("PODCAST_BASE_URL", "https://podcast.cariboosignals.ca/"),
    )
    args = parser.parse_args()
    if not args.base_url.endswith("/"):
        args.base_url += "/"
    generate_feed(args.base_url)
