#!/usr/bin/env python3
"""
cariboo_saturday.py — Cariboo Saturday Morning Radio Generator

Fetches the latest episodes from configured CBC podcast RSS feeds (prioritising
news: World Report → BC Today → CBC Kamloops), trims their intros/outros,
interspersed with short Canadian indie music clips from Jamendo,
and assembles everything into a single MP3 that mimics the CBC Radio 1
Saturday morning listening experience.

Riley hosts the show, adding commentary between segments, reading Cariboo weather,
and identifying each music track.

Usage:
    python cariboo_saturday.py [--dry-run] [--config PATH] [--output PATH]

    --dry-run   Fetch feed metadata and print episode info without downloading audio.
    --config    Path to config JSON (default: config/cariboo_saturday.json).
    --output    Override output MP3 path.

Environment:
    JAMENDO_CLIENT_ID   Jamendo API client ID (or set jamendo_client_id in config).
                        Register free at https://devportal.jamendo.com
    OPENAI_API_KEY      Required for Riley's voice (TTS). Without it the show
                        assembles silently between segments.
    ANTHROPIC_API_KEY   Required for Riley's script lines. Without it fallback
                        templates are used.
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
DEFAULT_CONFIG = SCRIPT_DIR / "config" / "cariboo_saturday.json"

# Load host personalities so Riley's voice stays consistent with the main show
_hosts_config_path = SCRIPT_DIR / "config" / "hosts.json"
HOSTS_CONFIG: dict = json.loads(_hosts_config_path.read_text(encoding="utf-8")) if _hosts_config_path.exists() else {}

# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

TARGET_SPEECH_DBFS = -20.0
TARGET_MUSIC_DBFS = -28.0
GAP_MS = 500  # silence padding around music transitions


def normalize_segment(audio: AudioSegment, target_dbfs: float) -> AudioSegment:
    """Normalize audio to target dBFS level."""
    change = target_dbfs - audio.dBFS
    return audio.apply_gain(change)


# ---------------------------------------------------------------------------
# Riley hosting — TTS + AI script generation
# ---------------------------------------------------------------------------

def get_openai_client():
    """Return a cached OpenAI client, or None if OPENAI_API_KEY is unset."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    if not hasattr(get_openai_client, "_client"):
        try:
            from openai import OpenAI
            get_openai_client._client = OpenAI(api_key=api_key)
        except ImportError:
            print("  [WARN] openai package not installed — Riley TTS disabled.")
            get_openai_client._client = None
    return get_openai_client._client


def get_anthropic_client():
    """Return a cached Anthropic client, or None if ANTHROPIC_API_KEY is unset."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    if not hasattr(get_anthropic_client, "_client"):
        try:
            import anthropic
            get_anthropic_client._client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            print("  [WARN] anthropic package not installed — AI script lines disabled.")
            get_anthropic_client._client = None
    return get_anthropic_client._client


def generate_riley_line(context: str) -> str:
    """Use Claude to write a short natural spoken line for Riley.

    Riley's personality is loaded from config/hosts.json so her voice stays
    consistent with her character in the main Cariboo Signals daily show.
    Falls back to a plain empty string if the API is unavailable.
    """
    client = get_anthropic_client()
    if not client:
        return ""

    riley = HOSTS_CONFIG.get("riley", {})
    riley_bio = riley.get("full_bio", "warm radio host who knows the Cariboo region of BC well")
    riley_questions = "; ".join(riley.get("recurring_questions", []))

    prompt = (
        "You are writing a short spoken line for Riley, host of Cariboo Saturday Morning "
        "on cariboosignals.ca — part of the Cariboo Weekends programming block.\n\n"
        f"Riley's personality: {riley_bio}\n"
        + (f"Her recurring angles: {riley_questions}\n" if riley_questions else "")
        + "\nShe sounds like a natural radio host — not a newsreader. "
        "No emojis, no stage directions, no quotation marks. "
        "Just the words she would say on air. Under 3 sentences.\n\n"
        f"Context: {context}"
    )
    try:
        import anthropic
        response = get_anthropic_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        print(f"  [WARN] Claude API error generating Riley line: {exc}")
        return ""


def trim_tts_silence(
    segment: AudioSegment, silence_thresh: float = -45, min_silence_len: int = 80
) -> AudioSegment:
    """Trim leading/trailing silence from a TTS segment."""
    from pydub.silence import detect_leading_silence

    start = detect_leading_silence(
        segment, silence_threshold=silence_thresh, chunk_size=min_silence_len
    )
    end = detect_leading_silence(
        segment.reverse(), silence_threshold=silence_thresh, chunk_size=min_silence_len
    )
    duration = len(segment)
    trimmed = segment[start : duration - end] if duration - end > start else segment
    return trimmed


def riley_tts(text: str, tmp_dir: Path) -> AudioSegment | None:
    """Convert text to audio using Riley's voice (OpenAI TTS nova).

    Returns None if TTS is unavailable or text is empty.
    """
    if not text:
        return None
    client = get_openai_client()
    if not client:
        print("  [INFO] No OPENAI_API_KEY — skipping Riley commentary.")
        return None

    tmp_file = tmp_dir / f"riley_{abs(hash(text)) % 10_000_000}.mp3"
    try:
        response = client.audio.speech.create(
            model="tts-1",
            voice="nova",
            input=text.replace("Quesnel", "Kweh-nell"),
            speed=1.0,
        )
        with open(tmp_file, "wb") as fh:
            fh.write(response.content)
        speech = AudioSegment.from_mp3(str(tmp_file))
        speech = trim_tts_silence(speech)
        return normalize_segment(speech, TARGET_SPEECH_DBFS)
    except Exception as exc:
        print(f"  [WARN] Riley TTS error: {exc}")
        return None


def riley_speak(context: str, tmp_dir: Path) -> AudioSegment | None:
    """Generate a Riley line via Claude, then convert to TTS audio.

    Returns None if either step is unavailable.
    """
    text = generate_riley_line(context)
    if not text:
        return None
    print(f"  [Riley] {text}")
    return riley_tts(text, tmp_dir)


# ---------------------------------------------------------------------------
# RSS / Download
# ---------------------------------------------------------------------------

HEADERS = {"User-Agent": "Cariboo-Saturday-Generator/1.0 (personal use)"}


def fetch_episode_candidates(feed: dict, max_candidates: int = 3) -> list[dict]:
    """Parse an RSS feed and return up to max_candidates audio episodes, newest first.

    Returns a list of dicts with keys: name, title, url, pub_date, duration, etc.
    Returns an empty list if the feed is unavailable or has no audio enclosures.
    Keeping multiple candidates lets the caller fall back to an older episode if the
    latest one fails to download (e.g. geo-restriction or transient network error).
    """
    rss_url = feed["rss_url"]
    try:
        resp = requests.get(rss_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  [WARN] Could not fetch {feed['name']}: {exc}")
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        print(f"  [WARN] Could not parse RSS for {feed['name']}: {exc}")
        return []

    ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
    candidates: list[dict] = []

    for item in root.iter("item"):
        if len(candidates) >= max_candidates:
            break
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

        link_el = item.find("link")
        episode_link = link_el.text.strip() if link_el is not None and link_el.text else ""

        candidates.append({
            "name": feed["name"],
            "title": title,
            "url": url,
            "pub_date": pub_date,
            "duration": duration,
            "link": episode_link,
            "trim_start_ms": feed.get("trim_start_ms", 10000),
            "trim_end_ms": feed.get("trim_end_ms", 10000),
            "jingle_end_ms": feed.get("jingle_end_ms"),
            "intermission_after": feed.get("intermission_after", False),
        })

    if not candidates:
        print(f"  [WARN] No audio enclosures found in {feed['name']} feed.")
    return candidates


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
# Music — Jamendo
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
    max_song_duration_ms: int = 240_000,
) -> tuple[AudioSegment | None, dict | None]:
    """Download a random (un-used) Jamendo track and trim it to duration_ms.

    The clip is capped at max_song_duration_ms (default 4 minutes) regardless of
    the requested duration_ms, so intermission slots never play an overly long song.
    If the track's natural length (after the lead-in skip) is shorter than the
    requested duration, the full available length is used instead of padding.

    Caches downloaded files in cache_dir by track ID.
    Returns (clip, track_info) or (None, None) if all tracks fail.
    """
    effective_max = min(duration_ms, max_song_duration_ms)

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

        # Start slightly into the track to skip any long lead-in silence
        start = min(5000, len(full) // 4)
        available_ms = len(full) - start
        clip_ms = min(available_ms, effective_max)
        clip = full[start : start + clip_ms]

        clip = clip.fade_in(1000).fade_out(1000)
        clip = normalize_segment(clip, music_target_dbfs)
        used_ids.add(track_id)

        track_info = {
            "name": track.get("name", ""),
            "artist": track.get("artist_name", ""),
            "genres": (
                track.get("musicinfo", {}).get("tags", {}).get("genres", [])
            ),
            "shareurl": track.get("shareurl", ""),
        }
        print(
            f"  [Music] Using: {track_info['name']} "
            f"by {track_info['artist']} ({len(clip) // 1000}s)"
        )
        return clip, track_info

    return None, None


def load_music_from_dir(
    music_dir: Path, duration_ms: int, music_target_dbfs: float
) -> list[tuple[AudioSegment, dict]]:
    """Load MP3/FLAC files from a local directory, shuffled.

    Returns list of (clip, track_info) tuples.
    """
    files = list(music_dir.glob("*.mp3")) + list(music_dir.glob("*.flac"))
    if not files:
        return []
    random.shuffle(files)
    results = []
    for f in files:
        try:
            full = AudioSegment.from_file(str(f))
            start = min(5000, len(full) // 4)
            clip = full[start : start + duration_ms]
            clip = clip.fade_in(1000).fade_out(1000)
            clip = normalize_segment(clip, music_target_dbfs)
            results.append((clip, {"name": f.stem, "artist": "", "genres": []}))
        except Exception as exc:
            print(f"  [WARN] Could not load {f.name}: {exc}")
    return results


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

GAP = AudioSegment.silent(duration=GAP_MS)


def _riley_segment(audio: AudioSegment | None) -> AudioSegment:
    """Wrap a Riley TTS segment with short silence padding, or return empty."""
    if audio is None:
        return AudioSegment.empty()
    return GAP + audio + GAP


def assemble_show(
    episodes: list[tuple[str, AudioSegment]],
    opening_jingle: AudioSegment | None,
    music_items: list[tuple[AudioSegment, dict]],
    weather_summary: str | None,
    config: dict,
    tmp_dir: Path,
    intermission_indices: set[int] | None = None,
) -> tuple[AudioSegment, list[dict]]:
    """Assemble the final show with Riley hosting.

    Structure:
        CBC opening jingle (once, from first episode's raw intro)
        Riley: show open + weather
        Episode 1
        Riley: outro for episode + music tease
        Music clip 1
        Riley: track ID + intro for next segment
        Episode 2
        ...
        Riley: sign-off
        Closing music (fade out)
    """
    if intermission_indices is None:
        intermission_indices = set()

    combined = AudioSegment.empty()
    chapters: list[dict] = []

    # Opening jingle
    chapters.append({"startTime": 0, "title": "Introduction"})
    if opening_jingle is not None:
        combined += opening_jingle + GAP

    # --- Riley: show opener + weather ---
    date_str = datetime.now().strftime("%A, %B %-d")
    segment_names = ", ".join(name for name, _ in episodes)
    weather_note = f" {weather_summary}" if weather_summary else ""
    opener_context = (
        f"Riley opens the Cariboo Saturday Morning show. Today is {date_str}.{weather_note} "
        f"The segments lined up are: {segment_names}. She welcomes listeners, gives the weather "
        f"naturally (if provided), and briefly teases what's coming up."
    )
    opener = riley_speak(opener_context, tmp_dir)
    combined += _riley_segment(opener)

    music_iter = iter(music_items)

    for i, (name, audio) in enumerate(episodes):
        print(f"  Adding episode: {name} ({len(audio) // 1000}s)")
        chapters.append({"startTime": round(len(combined) / 1000, 1), "title": name})
        combined += normalize_segment(audio, config["speech_target_dbfs"])

        clip_item = next(music_iter, None)
        is_last = i == len(episodes) - 1

        if not is_last and clip_item is not None:
            clip, track_info = clip_item
            is_intermission = i in intermission_indices
            next_name = episodes[i + 1][0]

            if is_intermission:
                # Riley: wrap up news block and announce music intermission
                outro_context = (
                    f"Riley wraps up the news block — that's the last local news segment "
                    f"(just finished {name}). She tells listeners there's a music intermission "
                    f"before the longer CBC programming coming up ({next_name} and more). "
                    f"Warm and natural, 1-2 sentences."
                )
            else:
                # Riley: brief episode wrap-up and music tease
                outro_context = (
                    f"Riley briefly wraps up the {name} segment and says a music break "
                    f"is coming before {next_name}. Keep it to one short sentence."
                )
            outro = riley_speak(outro_context, tmp_dir)
            combined += _riley_segment(outro)

            # Music
            if track_info.get("name"):
                track_label = f"Music — {track_info['name']} by {track_info['artist']}"
            else:
                track_label = "Music Break"
            music_chapter: dict = {"startTime": round(len(combined) / 1000, 1), "title": track_label}
            if track_info.get("shareurl"):
                music_chapter["url"] = track_info["shareurl"]
            chapters.append(music_chapter)
            combined += GAP + clip + GAP

            # Riley: music ID + intro for next segment
            genres_str = (
                f", genres: {', '.join(track_info['genres'])}"
                if track_info.get("genres")
                else ""
            )
            if is_intermission:
                track_id_context = (
                    f"Riley IDs the music track that just played: '{track_info['name']}' "
                    f"by {track_info['artist']}{genres_str}. "
                    f"She gives a natural mention of the artist and any Cariboo/BC/Canadian "
                    f"connection if it fits, then welcomes listeners back from the intermission "
                    f"and introduces the next longer segment: {next_name}."
                )
            else:
                track_id_context = (
                    f"Riley IDs the music track that just played: '{track_info['name']}' "
                    f"by {track_info['artist']}{genres_str}. "
                    f"She gives a brief natural mention of the artist (e.g. whether they're a "
                    f"solo act, duo, band; any Cariboo/BC/Canadian connection if it fits), "
                    f"then introduces the next segment: {next_name}."
                )
            track_id = riley_speak(track_id_context, tmp_dir)
            combined += _riley_segment(track_id)

        elif is_last:
            # No more episodes — closing music if available
            clip_item = next(music_iter, None)
            if clip_item is not None:
                clip, track_info = clip_item

                # Riley: sign-off before closing music
                signoff_context = (
                    "Riley signs off the Cariboo Saturday Morning show warmly, "
                    "thanks listeners, and says there's one last track to close out the morning."
                )
                signoff = riley_speak(signoff_context, tmp_dir)
                combined += _riley_segment(signoff)

                if track_info.get("name"):
                    track_label = f"Music — {track_info['name']} by {track_info['artist']}"
                else:
                    track_label = "Music Break"
                music_chapter = {"startTime": round(len(combined) / 1000, 1), "title": track_label}
                if track_info.get("shareurl"):
                    music_chapter["url"] = track_info["shareurl"]
                chapters.append(music_chapter)
                combined += GAP + clip.fade_out(3000)
            else:
                # No closing music — Riley signs off directly
                signoff_context = (
                    "Riley signs off the Cariboo Saturday Morning show warmly and thanks listeners."
                )
                signoff = riley_speak(signoff_context, tmp_dir)
                combined += _riley_segment(signoff)

        elif is_last and clip_item is None:
            combined += AudioSegment.silent(duration=2000)

    return combined, chapters


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_config(config_path: Path) -> dict:
    with open(config_path) as fh:
        return json.load(fh)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cariboo Saturday Morning Radio Generator")
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
    output_path = args.output or output_dir / f"cariboo_saturday_{date_str}.mp3"

    # Sort feeds by priority (lowest number = highest priority)
    feeds = sorted(config["feeds"], key=lambda f: f.get("priority", 99))

    # -----------------------------------------------------------------------
    # Step 1: Fetch weather
    # -----------------------------------------------------------------------
    weather_summary: str | None = None
    print("\n=== Fetching Cariboo weather ===")
    try:
        from weather import fetch_weather
        weather_data = fetch_weather()
        if weather_data:
            weather_summary = weather_data["summary"]
            print(f"  Weather: {weather_summary}")
        else:
            print("  [WARN] Weather fetch returned no data.")
    except Exception as exc:
        print(f"  [WARN] Weather fetch failed: {exc}")

    # -----------------------------------------------------------------------
    # Step 2: Fetch episode metadata
    # -----------------------------------------------------------------------
    print("\n=== Fetching CBC podcast episodes ===")
    # episode_candidates maps feed name -> list of fallback episodes (newest first)
    episode_candidates: list[list[dict]] = []
    for feed in feeds:
        print(f"  Checking: {feed['name']} …")
        candidates = fetch_episode_candidates(feed)
        if candidates:
            print(f"    -> {candidates[0]['title']} ({candidates[0]['pub_date']}) [{len(candidates)} candidate(s)]")
            episode_candidates.append(candidates)
        else:
            print(f"    -> Skipped (unavailable)")
    episode_meta: list[dict] = [c[0] for c in episode_candidates]  # used for dry-run display

    if not episode_candidates:
        print("\n[ERROR] No episodes could be fetched. Exiting.")
        sys.exit(1)

    if args.dry_run:
        print("\n=== Dry-run complete. Episodes that would be included: ===")
        for m in episode_meta:
            print(f"  {m['name']}: {m['title']}")
            print(f"    URL: {m['url']}")
            print(f"    Trim: {m['trim_start_ms']}ms start / {m['trim_end_ms']}ms end")
        if weather_summary:
            print(f"\n  Weather: {weather_summary}")
        return

    # -----------------------------------------------------------------------
    # Step 3: Download + trim episodes
    # -----------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        music_cache = output_dir / ".music_cache"
        music_cache.mkdir(exist_ok=True)

        print("\n=== Downloading and trimming episodes ===")
        episodes: list[tuple[str, AudioSegment]] = []
        intermission_indices: set[int] = set()
        opening_jingle: AudioSegment | None = None

        for i, candidates in enumerate(episode_candidates):
            dest = tmp / f"ep_{i:02d}.mp3"
            feed_name = candidates[0]["name"]
            print(f"\n  [{i+1}/{len(episode_candidates)}] {feed_name}")

            # Try each candidate in order until one downloads successfully
            raw: AudioSegment | None = None
            meta: dict | None = None
            for attempt, cand in enumerate(candidates):
                if attempt > 0:
                    print(f"    [FALLBACK] Trying older episode: {cand['title']}")
                print(f"    Downloading: {cand['url'][:80]}…")
                if not download_audio(cand["url"], dest):
                    print(f"    [WARN] Download failed (attempt {attempt + 1}/{len(candidates)}).")
                    continue
                try:
                    raw = AudioSegment.from_mp3(str(dest))
                    meta = cand
                    break
                except Exception as exc:
                    print(f"    [WARN] Could not decode audio: {exc}")

            if raw is None or meta is None:
                print(f"    [WARN] All {len(candidates)} candidate(s) failed — skipping {feed_name}.")
                continue

            print(f"    Duration: {len(raw) // 1000}s raw")

            # Extract opening jingle from the very first episode only
            if i == 0 and meta.get("jingle_end_ms"):
                opening_jingle = extract_opening_jingle(raw, meta["jingle_end_ms"])
                print(f"    Opening jingle extracted: {len(opening_jingle) // 1000}s")

            trimmed = trim_episode(raw, meta["trim_start_ms"], meta["trim_end_ms"])
            print(f"    Trimmed to: {len(trimmed) // 1000}s")
            if meta.get("intermission_after"):
                intermission_indices.add(len(episodes))
            episodes.append((meta["name"], trimmed))

        if not episodes:
            print("\n[ERROR] All episode downloads failed. Exiting.")
            sys.exit(1)

        # -------------------------------------------------------------------
        # Step 4: Music clips
        # -------------------------------------------------------------------
        # Build per-slot durations: one clip per episode (transition or closing),
        # plus one extra for the closing music after the last episode.
        # Slots where the preceding episode has intermission_after get a longer clip.
        duration_ms = config.get("music_transition_duration_ms", 20000)
        intermission_ms = config.get("music_intermission_duration_ms", 240000)
        max_song_duration_ms = config.get("max_song_duration_ms", 240_000)
        music_target_dbfs = config.get("music_target_dbfs", TARGET_MUSIC_DBFS)

        # One slot per episode (the last episode's slot is "wasted" by the iterator
        # but consumed), plus one final closing-music slot.
        transition_durations: list[int] = []
        for idx in range(len(episodes)):
            if idx in intermission_indices:
                transition_durations.append(intermission_ms)
                print(f"  [Music] Slot {idx}: intermission ({intermission_ms // 1000}s)")
            else:
                transition_durations.append(duration_ms)
        transition_durations.append(duration_ms)  # closing music slot
        num_clips_needed = len(transition_durations)

        music_items: list[tuple[AudioSegment, dict]] = []
        music_dir_str = config.get("music_dir", "")
        if music_dir_str:
            music_dir = Path(music_dir_str)
            if music_dir.is_dir():
                print(f"\n=== Loading music from local dir: {music_dir} ===")
                # Local dir: load with transition duration; intermission slots reuse last clip
                music_items = load_music_from_dir(music_dir, duration_ms, music_target_dbfs)
                print(f"  Loaded {len(music_items)} clips from local dir.")

        if len(music_items) < num_clips_needed:
            print("\n=== Fetching Canadian indie tracks from Jamendo ===")
            tracks = fetch_jamendo_tracks(jamendo_client_id, config.get("fma_tags", ["indie"]))
            if tracks:
                used_ids: set[str] = set()
                for slot_duration in transition_durations[len(music_items):]:
                    clip, track_info = get_music_clip(
                        tracks, music_cache, slot_duration, music_target_dbfs, used_ids,
                        max_song_duration_ms=max_song_duration_ms,
                    )
                    if clip is None:
                        break
                    music_items.append((clip, track_info))

        if not music_items:
            print("\n  [INFO] No music clips available — assembling without music transitions.")

        # -------------------------------------------------------------------
        # Step 5: Assemble with Riley hosting
        # -------------------------------------------------------------------
        print(f"\n=== Assembling show ({len(episodes)} episodes, {len(music_items)} music clips) ===")
        if intermission_indices:
            print(f"  Intermission after episode indices: {sorted(intermission_indices)}")
        show, chapters = assemble_show(
            episodes,
            opening_jingle,
            music_items,
            weather_summary,
            {"speech_target_dbfs": config.get("speech_target_dbfs", TARGET_SPEECH_DBFS)},
            tmp,
            intermission_indices=intermission_indices,
        )

        total_min = len(show) // 60_000
        total_sec = (len(show) % 60_000) // 1000
        print(f"  Total duration: {total_min}m {total_sec}s")

        # -------------------------------------------------------------------
        # Step 6: Export
        # -------------------------------------------------------------------
        print(f"\n=== Exporting to {output_path} ===")
        show.export(str(output_path), format="mp3", bitrate="128k")
        print(f"  Done. File size: {output_path.stat().st_size // 1024}KB")

        # Step 7b: Write Podcast Index chapters JSON
        chapters_path = output_path.with_name(output_path.stem + "_chapters.json")
        chapters_data = {"version": "1.2.0", "chapters": chapters}
        chapters_path.write_text(json.dumps(chapters_data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Chapters: {chapters_path} ({len(chapters)} chapter(s))")

        # -------------------------------------------------------------------
        # Step 7: Save companion metadata JSON for show notes
        # -------------------------------------------------------------------
        meta_path = output_path.with_suffix(".json")
        # Build episode list from candidates (only those that downloaded successfully)
        included_names = {name for name, _ in episodes}
        meta_episodes = [
            {
                "name": m["name"],
                "title": m["title"],
                "pub_date": m["pub_date"],
                "link": m.get("link", ""),
            }
            for cands in episode_candidates
            for m in cands[:1]  # use the first (latest) candidate's metadata for show notes
            if m["name"] in included_names
        ]
        meta_music = [
            {
                "name": ti.get("name", ""),
                "artist": ti.get("artist", ""),
                "genres": ti.get("genres", []),
                "shareurl": ti.get("shareurl", ""),
            }
            for _, ti in music_items
        ]
        show_metadata = {
            "date": date_str,
            "episodes": meta_episodes,
            "music": meta_music,
        }
        meta_path.write_text(json.dumps(show_metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Metadata: {meta_path}")

    # Summary
    print("\n=== Summary ===")
    print(f"  Output: {output_path}")
    print(f"  Episodes included ({len(episodes)}):")
    for name, audio in episodes:
        print(f"    - {name} ({len(audio) // 1000}s)")
    if opening_jingle:
        print(f"  Opening jingle: {len(opening_jingle) // 1000}s (from first episode)")
    print(f"  Music transitions: {len(music_items)}")
    if weather_summary:
        print(f"  Weather: {weather_summary}")


if __name__ == "__main__":
    main()
