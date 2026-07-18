#!/usr/bin/env python3
"""
Introducing the Show — one-off welcome episode generator

Generates a single short (~10-15 min) welcome episode for the main "Cariboo
Signals" feed, intended to be picked as the show's "episode to introduce the
show" in Apple Podcasts Connect. Riley and Casey introduce themselves, explain
how the show is actually made, and walk new listeners through the seven
recurring daily themes.

This is a one-off: it borrows its mechanics (turn-based script parsing, simple
audio assembly with intro/outro music and an intermission chime) from
generate_bespoke.py, but writes into the MAIN show's namespace
(podcasts/podcast_audio_*.mp3, citations_*.json, podcast-feed.xml) rather than
the separate "Deep Dives" bespoke feed.

Three forced stages — run them in order, reviewing each stage's output before
moving to the next (each stage reuses the previous stage's saved files rather
than regenerating them, so edits to the script are honored downstream):

    python generate_intro_episode.py --dry-run         # 1. generate + save script, print for review
    python generate_intro_episode.py --skip-publish    # 2. build audio + citations from that script
    python generate_intro_episode.py                   # 3. publish the reviewed audio + citations
"""

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

try:
    from pydub import AudioSegment
except ImportError as e:
    print(f"Missing required library: {e}")
    print("Install with: pip install pydub")
    sys.exit(1)

from config_loader import load_hosts_config, load_themes_config, load_podcast_config, load_credits_config, message_text
from generate_bespoke import (
    SCRIPT_MODEL,
    TARGET_SPEECH_DBFS,
    TARGET_MUSIC_DBFS,
    get_anthropic_client,
    get_openai_client,
    api_retry,
    parse_bespoke_script,
    generate_tts_segment,
    normalize_segment,
    trim_tts_silence,
    heuristic_gap_ms,
    _append_with_gap,
)

# ── Paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
PODCASTS_DIR = SCRIPT_DIR / "podcasts"

INTRO_MUSIC = SCRIPT_DIR / "cariboo-signals-intro.mp3"
OUTRO_MUSIC = SCRIPT_DIR / "cariboo-signals-outro.mp3"
INTERVAL_MUSIC = SCRIPT_DIR / "cariboo-signals-interval.mp3"

# Slug used for filenames and the RSS title (title-cases to "Introducing The Show")
SLUG = "introducing_the_show"
THEME_DISPLAY = "Introducing The Show"

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _pacific_today_str():
    """Today's date (YYYY-MM-DD) in Pacific time, matching the daily pipeline's date stamps."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Vancouver")
    except ImportError:
        import pytz
        tz = pytz.timezone("America/Vancouver")
    return datetime.now(tz).strftime("%Y-%m-%d")


# ── System prompt ──────────────────────────────────────────────────────────

LAND_ACKNOWLEDGMENT_BANK = [
    "We're coming to you from the Cariboo region, the traditional territories of the Secwépemc, Tŝilhqot'in, and Dakelh nations.",
    "Broadcasting from the unceded lands of the Secwépemc, Tŝilhqot'in, and Dakelh peoples in the Cariboo.",
    "Joining you today from the Cariboo, situated on the ancestral homelands of the Secwépemc, Tŝilhqot'in, and Dakelh nations.",
    "Speaking to you from the traditional territories of the Secwépemc, Tŝilhqot'in, and Dakelh nations here in the Cariboo region.",
    "Coming to you from the Cariboo, on lands that have been cared for by the Secwépemc, Tŝilhqot'in, and Dakelh peoples since time immemorial.",
    "Here in the Cariboo, on the traditional and unceded territories of the Secwépemc, Tŝilhqot'in, and Dakelh nations.",
    "Reaching you from the heart of the Cariboo, the homeland of the Secwépemc, Tŝilhqot'in, and Dakelh nations.",
]

BANNED_PATTERNS_BLOCK = """- No "I want to..." / "Let me..." narrated-intent openers — just do the thing, don't announce it
- No "Here's [anything]" sentence openers, in any variation — vary every sentence opening
- No "That's a fair [point/concern/distinction/whatever], but..." validation preambles before a counterpoint — counterpoints can simply begin
- No "Not X, but Y" contrastive-negation constructions ("Not just a feel-good story — it's...")
- No hedge-stacking ("not a completely unreasonable concern on its face")
- No debate-club vocabulary: "steelman" (say "the strongest version of that"), "circling back," "precisely" as filler
- No pre-announcing a conversational move ("A genuine question:", "A thread I'd like to pull on...", "Which points to...") — just make the move
- No commenting on language instead of making the point ("[X] is doing a lot of work in that sentence")"""


def _theme_walkthrough_lines(themes):
    lines = []
    for i, day in enumerate(WEEKDAY_NAMES):
        t = themes[str(i)]
        lines.append(f"- {day}: {t['name']} — {t['description']}")
    return "\n".join(lines)


def build_intro_system_prompt(hosts, themes, podcast_config, credits_config):
    riley, casey = hosts["riley"], hosts["casey"]
    structured = credits_config["structured"]
    land_ack_bank = "\n".join(f'  - "{p}"' for p in LAND_ACKNOWLEDGMENT_BANK)
    theme_lines = _theme_walkthrough_lines(themes)

    return f"""You are generating a special one-off welcome episode for {podcast_config['title']}, "{podcast_config['tagline']}." This episode exists for one purpose: it will be offered to brand-new listeners as their entry point into the show, so it has to be warm, clear, and an honest preview of what they're about to subscribe to.

HOSTS:
- Riley ({riley['pronouns']}): {riley['full_bio']}
- Casey ({casey['pronouns']}): {casey['full_bio']}

FORMAT:
- Speaker tags: **RILEY:** and **CASEY:** (bold name + colon, space before text)
- Pacing hints at the start of a turn — use them for BOTH hosts, spread across the WHOLE episode (not clustered on one speaker or one stretch): [overlap:-150] (or similar negative ms) for a quick interjection that cuts in before the other finishes, [pause:500] (or similar ms) for a considered beat before responding. Place several of each kind — early, middle, and late — so the pacing feels alive end to end, not just in one section.
- Exactly one [CHIME] marker on its own line — place it after the hosts have introduced themselves, explained how the show is made, and named the show's daily premise, but BEFORE the day-by-day theme walkthrough. It marks an intermission chime that splits the episode into two halves.

EPISODE ARC (~1,400-2,200 words total):

1. INTRODUCTIONS — SAY WHAT THEY ARE, PLAINLY, NOT WHO THEY'RE PRETENDING TO BE:
   - Riley and Casey state plainly, near the top — not as a twist saved for later — that they ARE AI-generated hosts / personas, not real people with hometowns, careers, or personal histories. Concretely, Riley must NOT say things like "I've got an engineering background, I grew up in the Cariboo" or "I'm Cariboo-based" — those are a human host's claims, and making them first (then "revealing" the AI angle later) reads as misdirection, not warmth. Instead, something like "I'm Riley — I'm one of the two AI personas built for this show, and my lens is the tech-optimist one" (paraphrased in her own voice, not read like a spec sheet). What's real and worth conveying vividly is the PERSPECTIVE each persona was built to carry — Riley's optimism-backed-by-data (paraphrase her stance from the bio above), Casey's skeptical show-me-the-maintenance-budget lens (paraphrase Casey's stance). The perspective is the real thing here, even when the person isn't — say so directly. Riley's opening lines in particular should sound bright, warm, and a little eager from the very first word — her optimism is a TONE as much as a stance, and a flat, heavy, or solemn opener undercuts it before she's said anything substantive; keep her opener light, quick, and glad-to-be-here, not weighty or declarative.
   - In that same breath — not as a separate, later disclaimer — they explain, plainly and with evident pride rather than hand-wringing, how the show is actually made: {structured['script_generation']} writes the scripts from the day's news and source material, a text-to-speech engine voices the two of them — the provider varies by episode ({structured['text_to_speech_openai']}, {structured['text_to_speech_azure']}, or {structured['text_to_speech_gemini']}) — and the finished audio is mixed together with music using a Python toolchain (pydub)
   - They can mention, in passing and without sounding like a press release, that {podcast_config['title']} self-assesses at "18/20 — Exemplary" against the TRACE standard (tracestandard.org) — a transparency framework that scores AI-made content on things like sourcing, consent, and accountability — the point being that nothing about how this show gets made is hidden
   - REQUIRED somewhere in the script (here, or in the sign-off if it flows more naturally there — but it must land once, in full, not get crowded out): they tell new listeners exactly how to reach the show — both the email, {podcast_config['email']}, AND the website. Write the website the way a host would actually SAY it out loud — something like "find us at cariboo signals dot C-A" — never as a literal URL string to be sounded out letter by letter (no "https," "www," or trailing slash; those read terribly aloud). Framed as a standing, genuine invitation to send corrections, feedback, or just say hello

2. LAND ACKNOWLEDGMENT — choose ONE of the following and adapt the phrasing naturally into the conversation (do not stack more than one, do not read it as a separate announcement):
{land_ack_bank}

3. THE SHOW'S PREMISE — in their own words, the hosts explain what the show actually is: daily AI-generated conversations about technology and society in rural BC, a new episode every day, organized around a rotating set of weekly themes

[CHIME]

4. WALKING THROUGH THE WEEK — Monday through Sunday, the seven themes that rotate through the week:
{theme_lines}

   For EACH of the seven themes: name it, briefly describe what it covers, and then have BOTH Riley and Casey preview — in one or two sentences each — how their own focus or angle differs on that subject. This is the heart of the episode: it's a new listener's first real taste of the show's two-perspective dynamic, theme by theme, not a dry list of topics. Riley is the technology optimist and empiricist (debate stance: {riley['debate_stance']}; she leans on questions like "{riley['recurring_questions'][0]}" and "{riley['recurring_questions'][2]}"). Casey is the community-first systems thinker and skeptic (debate stance: {casey['debate_stance']}; they lean on questions like "{casey['recurring_questions'][0]}" and "{casey['recurring_questions'][1]}"). For example: on Working Lands & Industry, Riley might preview leaning into adoption case studies and return-on-investment, while Casey previews pressing on who actually bears the costs of that transition. Vary which host previews first from theme to theme, and keep the pace brisk — seven themes is a lot of ground, so this should feel like a quick, lively preview reel, not a recitation.

5. SIGN-OFF — a genuine, unforced invitation for new listeners to dig into the back catalog and subscribe. If the email and website weren't already named earlier in the episode, land them here — a new listener should come away knowing exactly how to find and reach the show, full stop.

BANNED PATTERNS — these are mechanical AI speech habits and must not appear anywhere in the script (same standard as every other episode of this show):
{BANNED_PATTERNS_BLOCK}

EVIDENCE RULES:
- This is a welcome episode, not a news episode — there is no source material to cite and no news roundup
- Do not invent statistics, dates, audience numbers, or claims about the show's history that are not given to you above
- Speaking in general, accurate terms about the show's daily format and weekly theme rotation is correct and sufficient"""


def generate_intro_script(client, hosts, themes, podcast_config, credits_config):
    system_prompt = build_intro_system_prompt(hosts, themes, podcast_config, credits_config)
    user_prompt = (
        "Generate the complete welcome episode script now. Riley and Casey should sound like "
        "themselves throughout — Riley a little faster and more declarative, Casey blunter and "
        "drier — even though this is one continuous welcome conversation rather than a debate. "
        "By the end, a new listener should feel like they already know these two, and know "
        "exactly what to expect from the show and when. Do not pad or repeat yourself — every "
        "turn should move the welcome forward."
    )
    print(f"  Generating script (~1,400-2,200 words) with {SCRIPT_MODEL}...")
    response = api_retry(lambda: client.messages.create(
        model=SCRIPT_MODEL,
        max_tokens=8000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    ))
    return message_text(response)


# ── Audio assembly ─────────────────────────────────────────────────────────

def generate_intro_audio(script, output_path, hosts):
    """Assemble the welcome episode: intro music + turns ([CHIME] → intermission) + outro music."""
    if not get_openai_client():
        print("  OPENAI_API_KEY not set — skipping audio generation")
        return None

    turns = parse_bespoke_script(script)
    if not turns:
        print("  No speaker turns found in script")
        return None

    speech_turns = [t for t in turns if t["speaker"] != "__CHIME__"]
    print(f"  Parsed {len(speech_turns)} speaker turns")

    use_intro = INTRO_MUSIC.exists()
    use_outro = OUTRO_MUSIC.exists()
    use_chime = INTERVAL_MUSIC.exists()

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            combined = AudioSegment.empty()

            if use_intro:
                theme_full = normalize_segment(AudioSegment.from_mp3(str(INTRO_MUSIC)), TARGET_MUSIC_DBFS)
                combined = theme_full + AudioSegment.silent(duration=500)
                print(f"  Added intro music: {INTRO_MUSIC.name} ({len(theme_full)/1000:.1f}s, played in full)")

            prev_speaker = None
            tts_idx = 0
            for turn in turns:
                if turn["speaker"] == "__CHIME__":
                    if use_chime:
                        chime_raw = AudioSegment.from_mp3(str(INTERVAL_MUSIC))
                        chime = normalize_segment(chime_raw[:1450], TARGET_MUSIC_DBFS).fade_out(400)
                        combined += AudioSegment.silent(duration=300) + chime + AudioSegment.silent(duration=300)
                        print(f"  Added intermission chime ({len(chime)/1000:.1f}s)")
                    else:
                        combined += AudioSegment.silent(duration=800)
                        print("  Intermission chime file not found — inserted silence")
                    prev_speaker = None
                    continue

                tts_idx += 1
                print(f"  TTS {tts_idx}/{len(speech_turns)} ({turn['speaker']}: {len(turn['text'])} chars)")
                temp_file = os.path.join(tmpdir, f"turn_{tts_idx:03d}.mp3")
                generate_tts_segment(turn["text"], turn["speaker"], temp_file, hosts)
                speech = normalize_segment(AudioSegment.from_mp3(temp_file), TARGET_SPEECH_DBFS)
                speech = trim_tts_silence(speech)
                gap = turn.get("gap_ms")
                if gap is None:
                    gap = heuristic_gap_ms(turn["text"], prev_speaker, turn["speaker"])
                combined = _append_with_gap(combined, speech, gap)
                prev_speaker = turn["speaker"]

            if use_outro:
                outro = normalize_segment(AudioSegment.from_mp3(str(OUTRO_MUSIC)), TARGET_MUSIC_DBFS)
                combined += AudioSegment.silent(duration=500) + outro
                print(f"  Added outro music ({len(outro)/1000:.1f}s)")

        combined.export(str(output_path), format="mp3")
        duration_min = len(combined) / 1000 / 60
        size_mb = output_path.stat().st_size / 1024 / 1024
        print(f"  Audio: {duration_min:.1f} min, {size_mb:.1f} MB → {output_path.name}")
        return str(output_path)

    except Exception as e:
        print(f"  Error generating audio: {e}")
        return None


# ── Output files ───────────────────────────────────────────────────────────

def write_intro_script_file(script, date_str, output_dir, podcast_config):
    script_file = output_dir / f"podcast_script_{date_str}_{SLUG}.txt"
    with open(script_file, "w", encoding="utf-8") as f:
        f.write(f"# {podcast_config['title']} Podcast Script - {date_str}\n")
        f.write(f"# Theme: {THEME_DISPLAY}\n")
        f.write(f"# Generated: {date_str}\n\n")
        f.write(script)
    print(f"  Script → {script_file.name}")
    return script_file


def _read_script_file(script_file):
    """Read back a script written by write_intro_script_file, stripping its '#' header lines."""
    with open(script_file, encoding="utf-8") as f:
        lines = f.readlines()
    i = 0
    while i < len(lines) and (lines[i].startswith("#") or not lines[i].strip()):
        i += 1
    return "".join(lines[i:])


_SCRIPT_FILENAME_RE = re.compile(r"^podcast_script_(\d{4}-\d{2}-\d{2})_" + re.escape(SLUG) + r"\.txt$")


def _find_reviewed_script():
    """Locate the script saved by a prior --dry-run, regardless of its date stamp.

    This is a one-off episode, so there should only ever be one such file —
    looking it up this way means stage 2/3 don't need --date to match the
    stage that generated the script (e.g. reviewing across a day boundary).
    """
    matches = sorted(p for p in PODCASTS_DIR.glob(f"podcast_script_*_{SLUG}.txt") if _SCRIPT_FILENAME_RE.match(p.name))
    if not matches:
        return None, None
    script_file = matches[-1]
    return _SCRIPT_FILENAME_RE.match(script_file.name).group(1), script_file


def _credits_html_block(credits_config):
    c = credits_config["structured"]
    return (
        "<p><b>Credits</b><br>"
        f"Theme Song: {c['theme_song']}<br>"
        f"Content Curation &amp; Script: {c['content_curation']}<br>"
        f"TTS Voices: {c['text_to_speech_openai']}, {c['text_to_speech_azure']}, or {c['text_to_speech_gemini']} — varies by episode<br>"
        f"Cover Art: {c['cover_art']}<br>"
        f"Podcast Coordination: {c['coordination']}<br>"
        f"&#169; {c['copyright_year']} {c['copyright_holder']}. "
        f"Licensed under <a href=\"{c['license_url']}\">{c['license']}</a>.</p>"
    )


def write_intro_citations(date_str, output_dir, themes, credits_config, podcast_config):
    formatted_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %B %d, %Y")
    structured = credits_config["structured"]

    theme_items = "".join(
        f"<li><b>{WEEKDAY_NAMES[i]} — {themes[str(i)]['name']}:</b> {themes[str(i)]['description']}</li>"
        for i in range(7)
    )

    description = (
        "<p>New here? Start with this one. Riley and Casey introduce themselves, explain how "
        "the show is actually put together, and walk you through what to expect each day of "
        "the week.</p>"
        "<p><b>Meet the hosts:</b> Riley (tech optimist with an engineering background) and "
        "Casey (community-first skeptic of tech promises) — both AI-generated, and upfront "
        f"about it. {structured['script_generation']} writes the scripts, a text-to-speech "
        f"engine voices them ({structured['text_to_speech_openai']}, "
        f"{structured['text_to_speech_azure']}, or {structured['text_to_speech_gemini']} — "
        "varies by episode), and the "
        f"finished audio is mixed with music in Python (pydub). {podcast_config['title']} "
        "self-assesses at 18/20 (\"Exemplary\") on the Community Content Compact, a "
        "transparency framework for AI-made content.</p>"
        "<p><b>What to expect, day by day:</b></p>"
        f"<ul>{theme_items}</ul>"
        "<p>New episodes land every day — explore the back catalog, and subscribe to get "
        "tomorrow's as soon as it's ready.</p>"
    )
    description += _credits_html_block(credits_config)

    citations = {
        "episode": {
            "date": date_str,
            "formatted_date": formatted_date,
            "theme": THEME_DISPLAY,
            "title": f"{podcast_config['title']} - {THEME_DISPLAY}",
            # Tells generate_podcast_rss_feed() to mark this <itunes:episodeType>
            # as "trailer" rather than the default "full" — this is the show's
            # designated preview episode for new listeners in Apple Podcasts.
            "episode_type": "trailer",
            "description": description,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "models": {"script": SCRIPT_MODEL},
        },
        "credits": structured,
    }

    citations_file = output_dir / f"citations_{date_str}_{SLUG}.json"
    with open(citations_file, "w") as f:
        json.dump(citations, f, indent=2)
    print(f"  Citations → {citations_file.name}")
    return citations_file


# ── Publish ────────────────────────────────────────────────────────────────

def publish():
    from podcast_generator import generate_podcast_rss_feed, _regenerate_index_html, sync_site_to_r2
    print("\nPublishing...")
    generate_podcast_rss_feed()
    _regenerate_index_html()
    sync_site_to_r2()


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate the one-off 'Introducing the Show' welcome episode for Cariboo Signals, "
            "in three forced stages — each one builds on the previous stage's reviewed output:\n"
            "  1. --dry-run       generate + save the script, print it for review/editing\n"
            "  2. --skip-publish  build audio + citations from that reviewed script file\n"
            "  3. (no flags)      publish the reviewed audio + citations"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Stage 1: generate a fresh script, save it for review, and print it — stops there")
    parser.add_argument("--skip-publish", action="store_true",
                        help="Stage 2: build audio + citations from the saved script — stops before publishing")
    parser.add_argument("--date", default=None,
                        help="Stage 1 only: override the date stamp baked into the script's filename "
                             "(YYYY-MM-DD); defaults to today's Pacific date. Stages 2 and 3 reuse "
                             "whatever date the saved script was stamped with.")
    args = parser.parse_args()

    PODCASTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*50}")
    print("  Introducing the Show — one-off welcome episode")
    print(f"{'='*50}\n")

    hosts = load_hosts_config()
    themes = load_themes_config()
    podcast_config = load_podcast_config()
    credits_config = load_credits_config()

    # ── Stage 1: generate + save a fresh script for review ─────────────────
    if args.dry_run:
        date_str = args.date or _pacific_today_str()
        script_file = PODCASTS_DIR / f"podcast_script_{date_str}_{SLUG}.txt"

        client = get_anthropic_client()
        if not client:
            print("ANTHROPIC_API_KEY not set. Exiting.")
            sys.exit(1)

        print(f"Generating script (date stamp: {date_str})...")
        script = generate_intro_script(client, hosts, themes, podcast_config, credits_config)
        word_count = len(script.split())
        turn_count = script.count("**RILEY:**") + script.count("**CASEY:**")
        print(f"  Draft: {word_count} words, {turn_count} turns")
        write_intro_script_file(script, date_str, PODCASTS_DIR, podcast_config)

        print(f"\n{'='*50}")
        print(f"  SCRIPT (review/edit {script_file.name}, then run --skip-publish)")
        print(f"{'='*50}\n")
        print(script)
        return

    # Stages 2 and 3 both pick up whichever script stage 1 saved — there's
    # only ever one for this one-off episode, so its date stamp now governs
    # the audio/citations filenames too (no need for --date to match here).
    date_str, script_file = _find_reviewed_script()
    if not script_file:
        print(f"No reviewed script found (expected podcasts/podcast_script_<date>_{SLUG}.txt).")
        print("Run with --dry-run first to generate and review one.")
        sys.exit(1)

    audio_file = PODCASTS_DIR / f"podcast_audio_{date_str}_{SLUG}.mp3"
    citations_file = PODCASTS_DIR / f"citations_{date_str}_{SLUG}.json"

    # ── Stage 2: build audio + citations from the reviewed script ──────────
    if args.skip_publish:
        script = _read_script_file(script_file)
        print(f"Using reviewed script: {script_file.name}")

        print("\nGenerating audio...")
        generate_intro_audio(script, audio_file, hosts)

        print("\nWriting citations...")
        write_intro_citations(date_str, PODCASTS_DIR, themes, credits_config, podcast_config)

        print(f"\n--skip-publish set — review {audio_file.name}, then run with no flags to publish.")
        return

    # ── Stage 3: publish the reviewed audio + citations ────────────────────
    if not audio_file.exists() or not citations_file.exists():
        print(f"Missing reviewed audio/citations for {script_file.name}.")
        print("Run --skip-publish first to generate and review them.")
        sys.exit(1)

    print(f"Using reviewed script: {script_file.name}")
    print(f"Using reviewed audio:  {audio_file.name}")
    publish()

    print(f"\n{'='*50}")
    print("  Episode complete: Introducing The Show")
    print(f"  Output: podcasts/{audio_file.name}")
    print("  Feed:   podcast-feed.xml")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
