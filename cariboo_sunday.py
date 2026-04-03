#!/usr/bin/env python3
"""
cariboo_sunday.py — Cariboo Sunday Morning Radio Generator

Fetches the latest episodes from five CBC podcast RSS feeds: a news block
(CBC News: World Report, CBC BC Today, CBC Kamloops News) followed by cultural
programming (q with Tom Power, Unreserved).  Trims intros/outros, interspersed
with short Canadian indie music clips from Jamendo, and assembles everything
into a single MP3 for Sunday morning listening.

Casey hosts the show, adding commentary between segments, reading Cariboo weather,
and identifying each music track.  Casey's personality is loaded from
config/hosts.json so their voice stays consistent with the main Cariboo Signals
daily show.

This is the Sunday half of the Cariboo Weekends programming block:
  Saturday — Cariboo Saturday Morning (Riley hosts, CBC news focus)
  Sunday   — Cariboo Sunday Morning  (Casey hosts, CBC social/cultural focus)

Usage:
    python cariboo_sunday.py [--dry-run] [--config PATH] [--output PATH]

    --dry-run   Fetch feed metadata and print episode info without downloading audio.
    --config    Path to config JSON (default: config/cariboo_sunday.json).
    --output    Override output MP3 path.

Environment:
    JAMENDO_CLIENT_ID   Jamendo API client ID (or set jamendo_client_id in config).
                        Register free at https://devportal.jamendo.com
    OPENAI_API_KEY      Required for Casey's voice (TTS). Without it the show
                        assembles silently between segments.
    ANTHROPIC_API_KEY   Required for Casey's script lines. Without it fallback
                        templates are used.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Shared utilities — imported from cariboo_saturday so both shows stay in sync
# ---------------------------------------------------------------------------
from cariboo_saturday import (
    TARGET_SPEECH_DBFS,
    TARGET_MUSIC_DBFS,
    GAP_MS,
    GAP,
    HOSTS_CONFIG,
    normalize_segment,
    get_openai_client,
    get_anthropic_client,
    trim_tts_silence,
    fetch_episode_candidates,
    download_audio,
    trim_episode,
    fetch_jamendo_tracks,
    get_music_clip,
    load_music_from_dir,
)
from pydub import AudioSegment

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
DEFAULT_CONFIG = SCRIPT_DIR / "config" / "cariboo_sunday.json"

# ---------------------------------------------------------------------------
# Casey hosting — TTS + AI script generation
# ---------------------------------------------------------------------------

def generate_casey_line(context: str) -> str:
    """Use Claude to write a short natural spoken line for Casey.

    Casey's personality is loaded from config/hosts.json so their voice stays
    consistent with their character in the main Cariboo Signals daily show.
    Falls back to a plain empty string if the API is unavailable.
    """
    client = get_anthropic_client()
    if not client:
        return ""

    casey = HOSTS_CONFIG.get("casey", {})
    casey_bio = casey.get("full_bio", "community-focused radio host who knows the Cariboo region of BC well")
    casey_questions = "; ".join(casey.get("recurring_questions", []))

    prompt = (
        "You are writing a short spoken line for Casey, host of Cariboo Sunday Morning "
        "on cariboosignals.ca — part of the Cariboo Weekends programming block.\n\n"
        f"Casey's personality: {casey_bio}\n"
        + (f"Their recurring angles: {casey_questions}\n" if casey_questions else "")
        + "\nCasey sounds like a natural radio host — warm, thoughtful, and community-minded. "
        "Not a newsreader. No emojis, no stage directions, no quotation marks. "
        "Just the words they would say on air. Under 3 sentences.\n\n"
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
        print(f"  [WARN] Claude API error generating Casey line: {exc}")
        return ""


def casey_tts(text: str, tmp_dir: Path) -> AudioSegment | None:
    """Convert text to audio using Casey's voice (OpenAI TTS echo).

    Returns None if TTS is unavailable or text is empty.
    """
    if not text:
        return None
    client = get_openai_client()
    if not client:
        print("  [INFO] No OPENAI_API_KEY — skipping Casey commentary.")
        return None

    tmp_file = tmp_dir / f"casey_{abs(hash(text)) % 10_000_000}.mp3"
    try:
        response = client.audio.speech.create(
            model="tts-1",
            voice="echo",
            input=text,
            speed=1.0,
        )
        with open(tmp_file, "wb") as fh:
            fh.write(response.content)
        speech = AudioSegment.from_mp3(str(tmp_file))
        speech = trim_tts_silence(speech)
        return normalize_segment(speech, TARGET_SPEECH_DBFS)
    except Exception as exc:
        print(f"  [WARN] Casey TTS error: {exc}")
        return None


def casey_speak(context: str, tmp_dir: Path) -> AudioSegment | None:
    """Generate a Casey line via Claude, then convert to TTS audio.

    Returns None if either step is unavailable.
    """
    text = generate_casey_line(context)
    if not text:
        return None
    print(f"  [Casey] {text}")
    return casey_tts(text, tmp_dir)


def _casey_segment(audio: AudioSegment | None) -> AudioSegment:
    """Wrap a Casey TTS segment with short silence padding, or return empty."""
    if audio is None:
        return AudioSegment.empty()
    return GAP + audio + GAP


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def assemble_show(
    episodes: list[tuple[str, AudioSegment]],
    music_items: list[tuple[AudioSegment, dict]],
    weather_summary: str | None,
    config: dict,
    tmp_dir: Path,
    intermission_indices: set[int] | None = None,
) -> tuple[AudioSegment, list[dict]]:
    """Assemble the final Sunday show with Casey hosting.

    Structure:
        Casey: show open + weather
        Episode 1  (CBC News: World Report)
        [short music transition]
        Episode 2  (CBC BC Today)
        [short music transition]
        Episode 3  (CBC Kamloops News)
        [longer intermission music — intermission_after flag]
        Episode 4  (q with Tom Power)
        [short music transition]
        Episode 5  (Unreserved)
        Casey: sign-off
        Closing music (fade out)

    Any feed that fails to download is skipped; intermission_indices tracks which
    episode slots should be followed by a longer music break.
    """
    if intermission_indices is None:
        intermission_indices = set()

    combined = AudioSegment.empty()
    chapters: list[dict] = [{"startTime": 0, "title": "Introduction"}]

    # --- Casey: show opener + weather ---
    date_str = datetime.now().strftime("%A, %B %-d")
    weather_note = f" {weather_summary}" if weather_summary else ""

    # Split episodes into news block and cultural block so the opener accurately
    # describes both parts of the show.
    _news_keywords = ("World Report", "BC Today", "Kamloops", "News")
    news_names = [n for n, _ in episodes if any(kw in n for kw in _news_keywords)]
    cultural_names = [n for n, _ in episodes if not any(kw in n for kw in _news_keywords)]

    if news_names and cultural_names:
        _programming_note = (
            f"The news block covers {', '.join(news_names)}, "
            f"followed by cultural programming: {', '.join(cultural_names)}."
        )
    elif news_names:
        _programming_note = f"Today's show covers the news block: {', '.join(news_names)}."
    else:
        _programming_note = f"Today's cultural programming: {', '.join(cultural_names)}."

    opener_context = (
        f"Casey opens the Cariboo Sunday Morning show. Today is {date_str}.{weather_note} "
        f"{_programming_note} Casey welcomes listeners, mentions the weather naturally "
        f"(if provided), and briefly previews both the news block and the cultural segments."
    )
    opener = casey_speak(opener_context, tmp_dir)
    combined += _casey_segment(opener)

    music_iter = iter(music_items)

    for i, (name, audio) in enumerate(episodes):
        print(f"  Adding episode: {name} ({len(audio) // 1000}s)")
        chapters.append({"startTime": round(len(combined) / 1000, 1), "title": name})
        combined += normalize_segment(audio, config["speech_target_dbfs"])

        clip_item = next(music_iter, None)
        is_last = i == len(episodes) - 1

        if not is_last and clip_item is not None:
            clip, track_info = clip_item
            next_name = episodes[i + 1][0]

            # Casey: outro — longer for intermission break, brief for transitions
            if i in intermission_indices:
                outro_context = (
                    f"Casey wraps up the news block — the {name} segment was the last "
                    f"of the morning news. There's a longer music break coming before "
                    f"cultural programming with {next_name}. Casey gives a warm 2–3 sentence "
                    f"handoff: thanks the news team, invites listeners to relax into the music, "
                    f"and teases what's coming in the cultural hour."
                )
            else:
                outro_context = (
                    f"Casey briefly wraps up the {name} segment and says a music break "
                    f"is coming before {next_name}. Keep it to one short sentence."
                )
            outro = casey_speak(outro_context, tmp_dir)
            combined += _casey_segment(outro)

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

            # Casey: music ID + intro for next segment
            genres_str = (
                f", genres: {', '.join(track_info['genres'])}"
                if track_info.get("genres")
                else ""
            )
            track_id_context = (
                f"Casey IDs the music track that just played: '{track_info['name']}' "
                f"by {track_info['artist']}{genres_str}. "
                f"They give a brief natural mention of the artist (e.g. whether they're a "
                f"solo act, duo, band; any Cariboo/BC/Canadian connection if it fits), "
                f"then introduces the next segment: {next_name}."
            )
            track_id = casey_speak(track_id_context, tmp_dir)
            combined += _casey_segment(track_id)

        elif is_last:
            # No more episodes — closing music if available
            clip_item = next(music_iter, None)
            if clip_item is not None:
                clip, track_info = clip_item

                # Casey: sign-off before closing music
                signoff_context = (
                    "Casey signs off the Cariboo Sunday Morning show warmly, "
                    "thanks listeners, and says there's one last track to close out the morning."
                )
                signoff = casey_speak(signoff_context, tmp_dir)
                combined += _casey_segment(signoff)

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
                # No closing music — Casey signs off directly
                signoff_context = (
                    "Casey signs off the Cariboo Sunday Morning show warmly and thanks listeners."
                )
                signoff = casey_speak(signoff_context, tmp_dir)
                combined += _casey_segment(signoff)

        elif is_last and clip_item is None:
            combined += AudioSegment.silent(duration=2000)

    return combined, chapters


# ---------------------------------------------------------------------------
# Config / Main
# ---------------------------------------------------------------------------

def load_config(config_path: Path) -> dict:
    with open(config_path) as fh:
        return json.load(fh)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cariboo Sunday Morning Radio Generator")
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
    output_path = args.output or output_dir / f"cariboo_sunday_{date_str}.mp3"

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
    # Step 2: Fetch episode metadata (with fallback candidates)
    # -----------------------------------------------------------------------
    print("\n=== Fetching CBC podcast episodes ===")
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
    # Step 3: Download + trim episodes (with per-feed fallback)
    # -----------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        music_cache = output_dir / ".music_cache"
        music_cache.mkdir(exist_ok=True)

        print("\n=== Downloading and trimming episodes ===")
        episodes: list[tuple[str, AudioSegment]] = []
        intermission_indices: set[int] = set()

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
        duration_ms = config.get("music_transition_duration_ms", 20000)
        intermission_ms = config.get("music_intermission_duration_ms", 240_000)
        max_song_duration_ms = config.get("max_song_duration_ms", 240_000)
        music_target_dbfs = config.get("music_target_dbfs", TARGET_MUSIC_DBFS)

        # One transition clip per episode; slots after intermission_after feeds get
        # a longer clip. The last slot is the closing music after the final episode.
        transition_durations: list[int] = []
        for idx in range(len(episodes)):
            if idx in intermission_indices:
                transition_durations.append(intermission_ms)
                print(f"  [Music] Slot {idx}: intermission ({intermission_ms // 1000}s)")
            else:
                transition_durations.append(duration_ms)
        num_clips_needed = len(transition_durations)

        music_items: list[tuple[AudioSegment, dict]] = []
        music_dir_str = config.get("music_dir", "")
        if music_dir_str:
            music_dir = Path(music_dir_str)
            if music_dir.is_dir():
                print(f"\n=== Loading music from local dir: {music_dir} ===")
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
        # Step 5: Assemble with Casey hosting
        # -------------------------------------------------------------------
        print(f"\n=== Assembling show ({len(episodes)} episodes, {len(music_items)} music clips) ===")
        show, chapters = assemble_show(
            episodes,
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
        included_names = {name for name, _ in episodes}
        meta_episodes = [
            {
                "name": m["name"],
                "title": m["title"],
                "pub_date": m["pub_date"],
                "link": m.get("link", ""),
            }
            for cands in episode_candidates
            for m in cands[:1]
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
    print(f"  Music transitions: {len(music_items)}")
    if weather_summary:
        print(f"  Weather: {weather_summary}")


if __name__ == "__main__":
    main()
