#!/usr/bin/env python3
"""
cbc_saturday.py — CBC Saturday Morning Radio Generator

Fetches the latest episodes from configured CBC podcast RSS feeds (prioritising
news: World Report → BC Today → CBC Kamloops), trims their intros/outros,
interspersed with short Canadian indie music clips from Jamendo,
and assembles everything into a single MP3 that mimics the CBC Radio 1
Saturday morning listening experience.

Usage:
    python cbc_saturday.py [--dry-run] [--config PATH] [--output PATH]

    --dry-run   Fetch feed metadata and print episode info without downloading audio.
    --config    Path to config JSON (default: config/cbc_saturday.json).
    --output    Override output MP3 path.

Environment:
    JAMENDO_CLIENT_ID   Jamendo API client ID (or set jamendo_client_id in config).
                        Register free at https://devportal.jamendo.com
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import requests
from pydub import AudioSegment

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
DEFAULT_CONFIG = SCRIPT_DIR / "config" / "cbc_saturday.json"

# ---------------------------------------------------------------------------
# Audio helpers (mirrors podcast_generator.py patterns)
# ---------------------------------------------------------------------------

TARGET_SPEECH_DBFS = -20.0
TARGET_MUSIC_DBFS = -28.0
GAP_MS = 500  # silence padding around music transitions


def normalize_segment(audio: AudioSegment, target_dbfs: float) -> AudioSegment:
    """Normalize audio to target dBFS level."""
    change = target_dbfs - audio.dBFS
    return audio.apply_gain(change)


# ---------------------------------------------------------------------------
# RSS / Download
# ---------------------------------------------------------------------------

HEADERS = {"User-Agent": "CBC-Saturday-Generator/1.0 (personal use)"}


def fetch_latest_episode(feed: dict) -> dict | None:
    """Parse an RSS feed and return metadata for the latest episode.

    Returns a dict with keys: name, title, url, pub_date, duration
    or None if the feed is unavailable or has no audio enclosure.
    """
    rss_url = feed["rss_url"]
    try:
        resp = requests.get(rss_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  [WARN] Could not fetch {feed['name']}: {exc}")
        return None

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        print(f"  [WARN] Could not parse RSS for {feed['name']}: {exc}")
        return None

    ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}

    for item in root.iter("item"):
        enclosure = item.find("enclosure")
        if enclosure is None:
            continue
        mime = enclosure.get("type", "")
        if "audio" not in mime:
            continue
        url = enclosure.get("url", "")
        if not url:
            continue

        title_el = item.find("title")
        title = title_el.text if title_el is not None else "(untitled)"

        pub_el = item.find("pubDate")
        pub_date = pub_el.text if pub_el is not None else ""

        duration_el = item.find("itunes:duration", ns)
        duration = duration_el.text if duration_el is not None else ""

        return {
            "name": feed["name"],
            "title": title,
            "url": url,
            "pub_date": pub_date,
            "duration": duration,
            "trim_start_ms": feed.get("trim_start_ms", 10000),
            "trim_end_ms": feed.get("trim_end_ms", 10000),
            "jingle_end_ms": feed.get("jingle_end_ms"),
        }

    print(f"  [WARN] No audio enclosure found in {feed['name']} feed.")
    return None


def download_audio(url: str, dest: Path) -> bool:
    """Stream-download an audio file to dest. Returns True on success."""
    for attempt in range(3):
        try:
            with requests.get(url, headers=HEADERS, stream=True, timeout=60) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=8192):
                        fh.write(chunk)
            return True
        except requests.RequestException as exc:
            print(f"  [WARN] Download attempt {attempt + 1}/3 failed for {url}: {exc}")
    return False


# ---------------------------------------------------------------------------
# Trim
# ---------------------------------------------------------------------------

MIN_EPISODE_MS = 30_000  # 30 s — reject trim result shorter than this


def trim_episode(
    audio: AudioSegment, trim_start_ms: int, trim_end_ms: int
) -> AudioSegment:
    """Slice configured intro/outro durations from an episode.

    Falls back to the original if the result would be shorter than MIN_EPISODE_MS.
    """
    end = len(audio) - trim_end_ms
    if end - trim_start_ms < MIN_EPISODE_MS:
        print(
            f"  [WARN] Trim values too aggressive for {len(audio)}ms clip — skipping trim."
        )
        return audio
    return audio[trim_start_ms:end]


def extract_opening_jingle(raw_audio: AudioSegment, jingle_end_ms: int) -> AudioSegment:
    """Extract the CBC opening jingle to use as the one-time show opener."""
    jingle = raw_audio[:jingle_end_ms]
    return normalize_segment(jingle, TARGET_SPEECH_DBFS)


# ---------------------------------------------------------------------------
# Free Music Archive
# ---------------------------------------------------------------------------

JAMENDO_API_BASE = "https://api.jamendo.com/v3.0"


def fetch_jamendo_tracks(client_id: str, tags: list[str], limit: int = 30) -> list[dict]:
    """Fetch Canadian indie tracks from Jamendo.

    Queries with location_country=CA to prefer Canadian artists, then falls back
    to genre tags without the country filter if no results are found.
    Returns [] on any failure or missing client_id.
    """
    if not client_id:
        print("  [INFO] No JAMENDO_CLIENT_ID set — skipping music fetch.")
        print("         Register free at https://devportal.jamendo.com")
        return []

    url = f"{JAMENDO_API_BASE}/tracks/"

    for tag in tags:
        for country_filter in ["CA", None]:  # try Canadian artists first, then global
            params = {
                "client_id": client_id,
                "format": "json",
                "limit": limit,
                "fuzzytags": tag,
                "audiodownload_allowed": "true",
                "include": "musicinfo",
                "order": "popularity_week",
            }
            if country_filter:
                params["location_country"] = country_filter

            try:
                resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                tracks = data.get("results", [])
                if not tracks:
                    continue
                label = f"Canadian ({country_filter})" if country_filter else "global"
                print(f"  [Jamendo] {len(tracks)} tracks — tag={tag!r}, {label}")
                return tracks
            except requests.RequestException as exc:
                print(f"  [WARN] Jamendo error (tag={tag!r}, country={country_filter}): {exc}")

    print("  [WARN] No Jamendo tracks retrieved for any tag.")
    return []


def get_music_clip(
    tracks: list[dict],
    cache_dir: Path,
    duration_ms: int,
    music_target_dbfs: float,
    used_ids: set[str],
) -> AudioSegment | None:
    """Download a random (un-used) Jamendo track and trim it to duration_ms.

    Caches downloaded files in cache_dir by track ID.
    Returns None if all tracks fail.
    """
    pool = [t for t in tracks if str(t.get("id", "")) not in used_ids]
    random.shuffle(pool)

    for track in pool:
        track_id = str(track.get("id", "unknown"))
        track_url = track.get("audiodownload", "")
        if not track_url:
            continue

        cached = cache_dir / f"jamendo_{track_id}.mp3"
        if not cached.exists():
            print(
                f"  [Music] Downloading: {track.get('name', '?')} "
                f"by {track.get('artist_name', '?')}"
            )
            if not download_audio(track_url, cached):
                continue

        try:
            full = AudioSegment.from_mp3(str(cached))
        except Exception as exc:
            print(f"  [WARN] Could not decode {cached.name}: {exc}")
            cached.unlink(missing_ok=True)
            continue

        if len(full) < duration_ms:
            clip = full
        else:
            # Start slightly into the track to skip any long lead-in silence
            start = min(5000, len(full) // 4)
            clip = full[start : start + duration_ms]

        clip = clip.fade_in(1000).fade_out(1000)
        clip = normalize_segment(clip, music_target_dbfs)
        used_ids.add(track_id)

        print(
            f"  [Music] Using: {track.get('name', '?')} "
            f"by {track.get('artist_name', '?')} ({len(clip) // 1000}s)"
        )
        return clip

    return None


def load_music_from_dir(music_dir: Path, duration_ms: int, music_target_dbfs: float) -> list[AudioSegment]:
    """Load MP3/FLAC files from a local directory, shuffled."""
    files = list(music_dir.glob("*.mp3")) + list(music_dir.glob("*.flac"))
    if not files:
        return []
    random.shuffle(files)
    clips = []
    for f in files:
        try:
            full = AudioSegment.from_file(str(f))
            start = min(5000, len(full) // 4)
            clip = full[start : start + duration_ms]
            clip = clip.fade_in(1000).fade_out(1000)
            clip = normalize_segment(clip, music_target_dbfs)
            clips.append(clip)
        except Exception as exc:
            print(f"  [WARN] Could not load {f.name}: {exc}")
    return clips


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

GAP = AudioSegment.silent(duration=GAP_MS)


def assemble_show(
    episodes: list[tuple[str, AudioSegment]],
    opening_jingle: AudioSegment | None,
    music_clips: list[AudioSegment],
    config: dict,
) -> AudioSegment:
    """Assemble the final show from episodes and music clips.

    Structure:
        CBC opening jingle (once, from first episode's raw intro)
        [short silence]
        Episode 1 content (trimmed)
        [short silence] + music transition + [short silence]
        Episode 2 content (trimmed)
        ...
        [short silence] + closing music fade
    """
    combined = AudioSegment.empty()

    # Opening jingle
    if opening_jingle is not None:
        combined += opening_jingle + GAP

    music_iter = iter(music_clips)

    for i, (name, audio) in enumerate(episodes):
        print(f"  Adding episode: {name} ({len(audio) // 1000}s)")
        combined += normalize_segment(audio, config["speech_target_dbfs"])

        # Add a music transition between episodes (not after the last one)
        if i < len(episodes) - 1:
            clip = next(music_iter, None)
            if clip is not None:
                combined += GAP + clip + GAP
            else:
                # No music available — just add a short pause
                combined += AudioSegment.silent(duration=2000)

    # Closing: fade out with one final music clip if available
    closing = next(music_iter, None)
    if closing is not None:
        combined += GAP + closing.fade_out(3000)

    return combined


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_config(config_path: Path) -> dict:
    with open(config_path) as fh:
        return json.load(fh)


def main() -> None:
    parser = argparse.ArgumentParser(description="CBC Saturday Morning Radio Generator")
    parser.add_argument("--dry-run", action="store_true", help="Fetch metadata only, no audio download.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Config file path.")
    parser.add_argument("--output", type=Path, default=None, help="Override output MP3 path.")
    args = parser.parse_args()

    config = load_config(args.config)

    # Allow Jamendo client ID from env
    jamendo_client_id = os.environ.get("JAMENDO_CLIENT_ID", config.get("jamendo_client_id", ""))

    output_dir = SCRIPT_DIR / config.get("output_dir", "podcasts")
    output_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y-%m-%d")
    output_path = args.output or output_dir / f"cbc_saturday_{date_str}.mp3"

    # Sort feeds by priority (lowest number = highest priority)
    feeds = sorted(config["feeds"], key=lambda f: f.get("priority", 99))

    # -----------------------------------------------------------------------
    # Step 1: Fetch episode metadata
    # -----------------------------------------------------------------------
    print("\n=== Fetching CBC podcast episodes ===")
    episode_meta: list[dict] = []
    for feed in feeds:
        print(f"  Checking: {feed['name']} …")
        meta = fetch_latest_episode(feed)
        if meta:
            print(f"    -> {meta['title']} ({meta['pub_date']})")
            episode_meta.append(meta)
        else:
            print(f"    -> Skipped (unavailable)")

    if not episode_meta:
        print("\n[ERROR] No episodes could be fetched. Exiting.")
        sys.exit(1)

    if args.dry_run:
        print("\n=== Dry-run complete. Episodes that would be included: ===")
        for m in episode_meta:
            print(f"  {m['name']}: {m['title']}")
            print(f"    URL: {m['url']}")
            print(f"    Trim: {m['trim_start_ms']}ms start / {m['trim_end_ms']}ms end")
        return

    # -----------------------------------------------------------------------
    # Step 2: Download + trim episodes
    # -----------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        music_cache = output_dir / ".music_cache"
        music_cache.mkdir(exist_ok=True)

        print("\n=== Downloading and trimming episodes ===")
        episodes: list[tuple[str, AudioSegment]] = []
        opening_jingle: AudioSegment | None = None

        for i, meta in enumerate(episode_meta):
            dest = tmp / f"ep_{i:02d}.mp3"
            print(f"\n  [{i+1}/{len(episode_meta)}] {meta['name']}")
            print(f"    Downloading: {meta['url'][:80]}…")

            if not download_audio(meta["url"], dest):
                print(f"    [WARN] Download failed — skipping.")
                continue

            try:
                raw = AudioSegment.from_mp3(str(dest))
            except Exception as exc:
                print(f"    [WARN] Could not decode audio: {exc}")
                continue

            print(f"    Duration: {len(raw) // 1000}s raw")

            # Extract opening jingle from the very first episode only
            if i == 0 and meta.get("jingle_end_ms"):
                opening_jingle = extract_opening_jingle(raw, meta["jingle_end_ms"])
                print(f"    Opening jingle extracted: {len(opening_jingle) // 1000}s")

            trimmed = trim_episode(raw, meta["trim_start_ms"], meta["trim_end_ms"])
            print(f"    Trimmed to: {len(trimmed) // 1000}s")
            episodes.append((meta["name"], trimmed))

        if not episodes:
            print("\n[ERROR] All episode downloads failed. Exiting.")
            sys.exit(1)

        # -------------------------------------------------------------------
        # Step 3: Music clips
        # -------------------------------------------------------------------
        num_clips_needed = len(episodes)  # one per transition + closing
        music_clips: list[AudioSegment] = []
        duration_ms = config.get("music_transition_duration_ms", 20000)
        music_target_dbfs = config.get("music_target_dbfs", TARGET_MUSIC_DBFS)

        music_dir_str = config.get("music_dir", "")
        if music_dir_str:
            music_dir = Path(music_dir_str)
            if music_dir.is_dir():
                print(f"\n=== Loading music from local dir: {music_dir} ===")
                music_clips = load_music_from_dir(music_dir, duration_ms, music_target_dbfs)
                print(f"  Loaded {len(music_clips)} clips from local dir.")

        if len(music_clips) < num_clips_needed:
            print("\n=== Fetching Canadian indie tracks from Jamendo ===")
            tracks = fetch_jamendo_tracks(jamendo_client_id, config.get("fma_tags", ["indie"]))
            if tracks:
                used_ids: set[str] = set()
                while len(music_clips) < num_clips_needed:
                    clip = get_music_clip(
                        tracks, music_cache, duration_ms, music_target_dbfs, used_ids
                    )
                    if clip is None:
                        break
                    music_clips.append(clip)

        if not music_clips:
            print("\n  [INFO] No music clips available — assembling without music transitions.")

        # -------------------------------------------------------------------
        # Step 4: Assemble
        # -------------------------------------------------------------------
        print(f"\n=== Assembling show ({len(episodes)} episodes, {len(music_clips)} music clips) ===")
        show = assemble_show(
            episodes,
            opening_jingle,
            music_clips,
            {"speech_target_dbfs": config.get("speech_target_dbfs", TARGET_SPEECH_DBFS)},
        )

        total_min = len(show) // 60_000
        total_sec = (len(show) % 60_000) // 1000
        print(f"  Total duration: {total_min}m {total_sec}s")

        # -------------------------------------------------------------------
        # Step 5: Export
        # -------------------------------------------------------------------
        print(f"\n=== Exporting to {output_path} ===")
        show.export(str(output_path), format="mp3", bitrate="128k")
        print(f"  Done. File size: {output_path.stat().st_size // 1024}KB")

    # Summary
    print("\n=== Summary ===")
    print(f"  Output: {output_path}")
    print(f"  Episodes included ({len(episodes)}):")
    for name, audio in episodes:
        print(f"    - {name} ({len(audio) // 1000}s)")
    if opening_jingle:
        print(f"  Opening jingle: {len(opening_jingle) // 1000}s (from first episode)")
    print(f"  Music transitions: {len(music_clips)}")


if __name__ == "__main__":
    main()
