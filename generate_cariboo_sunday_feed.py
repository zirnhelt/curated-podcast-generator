#!/usr/bin/env python3
"""
generate_cariboo_sunday_feed.py — Build cariboo-sunday-feed.xml

Scans podcasts/cariboo_sunday_*.mp3, derives pub-dates from filenames, and writes
a podcast-compatible RSS feed.

Usage:
    python generate_cariboo_sunday_feed.py [--base-url URL]
"""

from __future__ import annotations

import argparse
import json
import os
import xml.sax.saxutils as saxutils
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PODCASTS_DIR = SCRIPT_DIR / "podcasts"
FEED_FILE = SCRIPT_DIR / "cariboo-sunday-feed.xml"

COVER_IMAGE = "cariboo-sunday.png"

FEED_CONFIG = {
    "title": "Cariboo Sunday Morning — Cariboo Mix",
    "description": (
        "A curated Sunday morning radio experience drawn from CBC cultural podcasts "
        "(q with Tom Power, Unreserved), "
        "with Canadian indie music transitions from Jamendo. "
        "Assembled automatically each Sunday for the Cariboo region of BC."
    ),
    "author": "Cariboo Signals",
    "language": "en-ca",
    "explicit": False,
    "category": "Arts",
}


def _estimate_duration(size_bytes: int) -> str:
    """Rough duration estimate: ~128 kbps MP3 → 16 KB/s."""
    seconds = size_bytes // 16_000
    return f"{seconds // 60}:{seconds % 60:02d}"


def _build_description(title: str, meta_path: Path) -> str:
    """Build an HTML episode description from companion metadata JSON if available.

    Falls back to plain title when no metadata exists.
    """
    if not meta_path.exists():
        return title

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return title

    parts: list[str] = [f"<p>{title}</p>"]

    episodes = meta.get("episodes", [])
    if episodes:
        parts.append("<p><strong>CBC segments featured:</strong></p><ul>")
        for ep in episodes:
            name = ep.get("name", "")
            ep_title = ep.get("title", name)
            link = ep.get("link", "")
            pub = ep.get("pub_date", "")
            pub_note = f" — {pub}" if pub else ""
            if link:
                parts.append(
                    f'<li><a href="{saxutils.escape(link)}">'
                    f"{saxutils.escape(ep_title)}</a>"
                    f" ({saxutils.escape(name)}){saxutils.escape(pub_note)}</li>"
                )
            else:
                parts.append(
                    f"<li>{saxutils.escape(ep_title)}"
                    f" ({saxutils.escape(name)}){saxutils.escape(pub_note)}</li>"
                )
        parts.append("</ul>")

    music = meta.get("music", [])
    if music:
        parts.append("<p><strong>Music:</strong></p><ul>")
        for track in music:
            track_name = track.get("name", "")
            artist = track.get("artist", "")
            genres = track.get("genres", [])
            shareurl = track.get("shareurl", "")
            genre_note = f" [{', '.join(genres)}]" if genres else ""
            credit = f"{saxutils.escape(track_name)} by {saxutils.escape(artist)}{saxutils.escape(genre_note)}"
            if shareurl:
                parts.append(
                    f'<li><a href="{saxutils.escape(shareurl)}">{credit}</a>'
                    f" via Jamendo (CC)</li>"
                )
            else:
                parts.append(f"<li>{credit} via Jamendo (CC)</li>")
        parts.append("</ul>")

    parts.append(
        "<p><em>Assembled automatically from CBC Radio podcasts for the Cariboo region of BC. "
        "Music sourced from Jamendo under Creative Commons licence.</em></p>"
    )

    return "".join(parts)


def generate_feed(base_url: str) -> None:
    mp3s = sorted(PODCASTS_DIR.glob("cariboo_sunday_*.mp3"), reverse=True)
    if not mp3s:
        print("  [WARN] No cariboo_sunday_*.mp3 files found — feed will be empty.")

    episodes = []
    for mp3 in mp3s[:20]:  # keep last 20
        # filename: cariboo_sunday_YYYY-MM-DD.mp3
        stem = mp3.stem  # cariboo_sunday_2026-03-22
        date_str = stem.replace("cariboo_sunday_", "")
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d").replace(
                hour=16, tzinfo=timezone.utc
            )
            pub_date = dt.strftime("%a, %d %b %Y %H:%M:%S GMT")
            title = f"Cariboo Sunday Morning — {dt.strftime('%B %-d, %Y')}"
        except ValueError:
            pub_date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
            title = f"Cariboo Sunday Morning — {stem}"

        size = mp3.stat().st_size
        url = f"{base_url}podcasts/{mp3.name}"
        guid = f"cariboo-sunday-{date_str}"
        duration = _estimate_duration(size)
        meta_path = mp3.with_suffix(".json")
        description = _build_description(title, meta_path)

        episodes.append(
            {
                "title": title,
                "pub_date": pub_date,
                "url": url,
                "size": size,
                "guid": guid,
                "duration": duration,
                "description": description,
            }
        )

    cfg = FEED_CONFIG
    feed_url = f"{base_url}cariboo-sunday-feed.xml"
    cover_url = f"{base_url}{COVER_IMAGE}"
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
        f'<itunes:image href="{saxutils.escape(cover_url)}"/>',
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
            f"<description><![CDATA[{ep['description']}]]></description>",
            f"<itunes:summary><![CDATA[{ep['description']}]]></itunes:summary>",
            f'<itunes:image href="{saxutils.escape(cover_url)}"/>',
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
