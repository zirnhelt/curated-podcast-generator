#!/usr/bin/env python3
"""
French episode generator — adapt a script and synthesize a full episode

Adapts an English daily episode script into natural, broadcast-neutral Canadian
French (fr-CA) via Claude, then synthesizes a full episode with Azure Neural TTS
Dragon HD voices (Aurélie/Sylvie fr-CA, Kaël/Thierry fr-FR) and the standard
Cariboo Signals theme songs (intro, interval stings, outro).

Two stages, reviewed in order:

    python generate_french_prototype.py --dry-run     # 1. adapt + save FR script for review
    python generate_french_prototype.py               # 2. synthesize full episode
    python generate_french_prototype.py --publish     # 2+3. synthesize + update RSS feed
"""

import argparse
import re
import sys
import tempfile
import xml.sax.saxutils as saxutils
from email.utils import formatdate
from pathlib import Path
import time

try:
    from pydub import AudioSegment
except ImportError as e:
    print(f"Missing required library: {e}")
    print("Install with: pip install pydub")
    sys.exit(1)

from config_loader import load_prompts_config
from generate_bespoke import (
    SCRIPT_MODEL,
    TARGET_SPEECH_DBFS,
    TARGET_MUSIC_DBFS,
    get_anthropic_client,
    api_retry,
    normalize_segment,
    trim_tts_silence,
    heuristic_gap_ms,
    _append_with_gap,
)
from podcast_generator import parse_script_into_segments
from azure_tts import get_azure_speech_config, synthesize_section

# ── Paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
PODCASTS_DIR = SCRIPT_DIR / "podcasts"
OUTPUT_DIR = PODCASTS_DIR / "french_prototype"

INTRO_MUSIC    = SCRIPT_DIR / "cariboo-signals-intro.mp3"
INTERVAL_MUSIC = SCRIPT_DIR / "cariboo-signals-interval.mp3"
OUTRO_MUSIC    = SCRIPT_DIR / "cariboo-signals-outro.mp3"

INTERVAL_DURATION_MS = 1200
INTERVAL_FADE_MS     = 400
SECTION_GAP_MS       = 400   # gap between speech and music sting

# Dragon HD voices: Aurélie (fr-CA Sylvie) and Kaël (fr-FR Thierry).
# Each entry carries both the Azure voice name and its locale for the SSML
# xml:lang attribute — Sylvie is fr-CA, Thierry is fr-FR.
FRENCH_VOICE_MAP = {
    "riley": {"voice": "fr-CA-Sylvie:DragonHDLatestNeural", "locale": "fr-CA"},
    "casey": {"voice": "fr-FR-Thierry:DragonHDLatestNeural", "locale": "fr-FR"},
}

_SOURCE_SCRIPT_RE = re.compile(r"^podcast_script_(\d{4}-\d{2}-\d{2})_(.+)\.txt$")
_FR_SCRIPT_RE = re.compile(r"^script_fr_(\d{4}-\d{2}-\d{2})_(.+)\.txt$")


# ── Stage 0: locate and read the English source script ─────────────────────

def _find_source_script(override=None):
    """Return the English script to adapt: an explicit override, or the most
    recently generated daily episode script under podcasts/."""
    if override:
        path = Path(override)
        if not path.is_absolute():
            path = PODCASTS_DIR / override
        if not path.exists():
            return None
        return path

    candidates = []
    for p in PODCASTS_DIR.glob("podcast_script_*.txt"):
        m = _SOURCE_SCRIPT_RE.match(p.name)
        if m:
            candidates.append((m.group(1), p))
    if not candidates:
        return None
    return max(candidates)[1]


def _read_script_body(path):
    """Read a script file, stripping its leading '# ...' header/comment lines."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    i = 0
    while i < len(lines) and (lines[i].startswith("#") or not lines[i].strip()):
        i += 1
    return "".join(lines[i:])


def _extract_theme_name(path):
    """Pull the human-readable theme name from a script's '# Theme: ...' header line."""
    for line in path.read_text(encoding="utf-8").splitlines()[:5]:
        m = re.match(r"#\s*Theme:\s*(.+)", line.strip())
        if m:
            return m.group(1).strip()
    return "Cariboo Signals"


# ── Stage 1: adapt the script into Canadian French ─────────────────────────

def localize_script_to_french(client, english_script, theme_name):
    prompt_cfg = load_prompts_config()["french_localization"]
    prompt = prompt_cfg["template"].format(theme_name=theme_name, script=english_script)

    print(f"  Adapting script into Canadian French with {SCRIPT_MODEL}...")
    response = api_retry(lambda: client.messages.create(
        model=SCRIPT_MODEL,
        max_tokens=12000,
        messages=[{"role": "user", "content": prompt}],
    ))
    return response.content[0].text.strip()


def write_french_script_file(french_script, date_str, theme_slug, podcast_title):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"script_fr_{date_str}_{theme_slug}.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# {podcast_title} — adaptation française — {date_str}\n")
        f.write(f"# Adapted from: podcast_script_{date_str}_{theme_slug}.txt\n\n")
        f.write(french_script)
    print(f"  French script → {out_path.relative_to(SCRIPT_DIR)}")
    return out_path


def _find_french_script():
    """Locate the most recently saved adapted script."""
    if not OUTPUT_DIR.exists():
        return None, None, None
    matches = sorted(
        (p for p in OUTPUT_DIR.glob("script_fr_*.txt") if _FR_SCRIPT_RE.match(p.name)),
        key=lambda p: p.stat().st_mtime,
    )
    if not matches:
        return None, None, None
    path = matches[-1]
    m = _FR_SCRIPT_RE.match(path.name)
    return m.group(1), m.group(2), path


# ── Stage 2: synthesize with Azure Dragon HD voices + theme songs ──────────

def _build_single_voice_ssml(text, voice_name, lang):
    """Wrap one turn of plain text in a minimal single-voice SSML document."""
    inner = f'<voice name="{voice_name}">{saxutils.escape(text)}</voice>'
    return (
        '<speak version="1.0"'
        ' xmlns="http://www.w3.org/2001/10/synthesis"'
        ' xmlns:mstts="http://www.w3.org/2001/mstts"'
        f' xml:lang="{lang}">{inner}</speak>'
    )


def synthesize_french_sample(segments, output_mp3):
    """Synthesize a full French episode with intro/interval/outro music.

    Returns (output_mp3, duration_sec) on success, or (None, 0) on failure.
    """
    speech_config = get_azure_speech_config()
    if speech_config is None:
        print("  ❌ AZURE_SPEECH_KEY / AZURE_SPEECH_REGION not set — cannot synthesize audio")
        return None, 0

    # Load theme music
    music_missing = [p for p in [INTRO_MUSIC, INTERVAL_MUSIC, OUTRO_MUSIC] if not p.exists()]
    if music_missing:
        print(f"  ⚠️  Music files not found: {[p.name for p in music_missing]} — episode will have no music")
        use_music = False
        intro_music = interval_clip = outro_music = None
    else:
        use_music = True
        intro_music  = normalize_segment(AudioSegment.from_mp3(str(INTRO_MUSIC)),    TARGET_MUSIC_DBFS)
        interval_clip = normalize_segment(
            AudioSegment.from_mp3(str(INTERVAL_MUSIC))[:INTERVAL_DURATION_MS], TARGET_MUSIC_DBFS
        ).fade_out(INTERVAL_FADE_MS)
        outro_music  = normalize_segment(AudioSegment.from_mp3(str(OUTRO_MUSIC)),    TARGET_MUSIC_DBFS)

    section_order = [
        ("welcome",            "Welcome / Accueil"),
        ("news",               "News Roundup / Revue de l'actualité"),
        ("community_spotlight","Community Spotlight / Vitrine communautaire"),
        ("deep_dive",          "Deep Dive / Analyse approfondie"),
    ]

    total_turns = sum(len(segments.get(key) or []) for key, _ in section_order)
    turn_idx = 0

    # Render each section's TTS speech into its own AudioSegment
    section_audios = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        for key, label in section_order:
            seg_list = segments.get(key) or []
            if not seg_list:
                continue
            print(f"  {label}: {len(seg_list)} turns")
            section_audio = AudioSegment.empty()
            prev_speaker = None

            for turn in seg_list:
                turn_idx += 1
                speaker = turn["speaker"]
                entry = FRENCH_VOICE_MAP.get(speaker, FRENCH_VOICE_MAP["riley"])
                voice_name = entry["voice"]
                locale = entry["locale"]
                wav_path = Path(tmpdir) / f"turn_{turn_idx:03d}.wav"
                print(f"    [{turn_idx}/{total_turns}] {speaker} ({voice_name}): {len(turn['text'])} chars")

                ssml = _build_single_voice_ssml(turn["text"], voice_name, lang=locale)
                synthesize_section(ssml, wav_path, speech_config)

                speech = normalize_segment(
                    trim_tts_silence(AudioSegment.from_file(wav_path, format="wav")),
                    TARGET_SPEECH_DBFS,
                )
                gap = turn.get("gap_ms")
                if gap is None:
                    gap = heuristic_gap_ms(turn["text"], prev_speaker, speaker)
                section_audio = _append_with_gap(section_audio, speech, gap)
                prev_speaker = speaker

            section_audios[key] = section_audio

    # Stitch sections with theme music
    section_gap = AudioSegment.silent(duration=SECTION_GAP_MS)
    combined = AudioSegment.empty()
    if use_music:
        combined = intro_music + section_gap

    section_keys = [key for key, _ in section_order if key in section_audios]
    for i, key in enumerate(section_keys):
        combined += section_audios[key]
        is_last = (i == len(section_keys) - 1)
        if not is_last:
            if use_music:
                combined += section_gap + interval_clip + section_gap
            else:
                combined += AudioSegment.silent(duration=600)

    if use_music:
        combined += section_gap + outro_music

    combined.export(str(output_mp3), format="mp3")
    duration_ms = len(combined)
    duration_min = duration_ms / 1000 / 60
    size_mb = output_mp3.stat().st_size / 1024 / 1024
    print(f"  Episode audio: {duration_min:.1f} min, {size_mb:.1f} MB → {output_mp3.name}")
    return output_mp3, duration_ms // 1000


# ── Stage 3 (optional): update the French RSS feed ─────────────────────────

def write_french_rss_entry(date_str, theme_name, theme_slug, audio_filename,
                            duration_sec, podcast_config):
    """Prepend a new episode item to podcast-fr-feed.xml (creates the feed if absent)."""
    feed_path = SCRIPT_DIR / "podcast-fr-feed.xml"
    base_url = podcast_config.get("url", "https://podcast.cariboosignals.ca/")
    audio_url = f"{base_url}podcasts/french/{audio_filename}"
    pub_date = formatdate(time.time())

    dur_min = duration_sec // 60
    dur_sec_part = duration_sec % 60

    new_item = (
        f'  <item>\n'
        f'    <title>Cariboo Signals en français — {saxutils.escape(theme_name)} — {date_str}</title>\n'
        f'    <pubDate>{pub_date}</pubDate>\n'
        f'    <guid isPermaLink="false">cariboo-signals-fr-{date_str}_{theme_slug}</guid>\n'
        f'    <description>Aurélie et Kaël explorent la technologie et la société dans les '
        f'communautés rurales du Cariboo. Thème du jour : {saxutils.escape(theme_name)}.</description>\n'
        f'    <enclosure url="{saxutils.escape(audio_url)}" length="0" type="audio/mpeg"/>\n'
        f'    <itunes:duration>{dur_min}:{dur_sec_part:02d}</itunes:duration>\n'
        f'    <itunes:explicit>false</itunes:explicit>\n'
        f'    <itunes:episodeType>full</itunes:episodeType>\n'
        f'  </item>\n'
    )

    if not feed_path.exists():
        content = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">\n'
            '<channel>\n'
            '<title>Cariboo Signals en français</title>\n'
            f'<link>{base_url}</link>\n'
            '<language>fr-CA</language>\n'
            '<copyright>© 2026 Erich Zirnhelt. Sous licence CC BY-NC 4.0.</copyright>\n'
            '<itunes:author>Erich Zirnhelt</itunes:author>\n'
            '<itunes:summary>Cariboo Signals en français — technologie et société dans les '
            'communautés rurales de la région du Cariboo avec Aurélie et Kaël.</itunes:summary>\n'
            f'<itunes:image href="{base_url}cariboo-signals.png"/>\n'
            '<itunes:explicit>false</itunes:explicit>\n'
            '<itunes:type>episodic</itunes:type>\n'
            f'<lastBuildDate>{pub_date}</lastBuildDate>\n'
            + new_item
            + '</channel>\n</rss>\n'
        )
    else:
        content = feed_path.read_text(encoding="utf-8")
        # Insert new item before the first existing <item> or before </channel>
        marker = re.search(r"<item>|</channel>", content)
        if marker:
            pos = marker.start()
            content = content[:pos] + new_item + content[pos:]
        else:
            content = content.replace("</rss>", new_item + "</rss>")
        # Update lastBuildDate
        content = re.sub(
            r"<lastBuildDate>[^<]*</lastBuildDate>",
            f"<lastBuildDate>{pub_date}</lastBuildDate>",
            content,
        )

    feed_path.write_text(content, encoding="utf-8")
    print(f"  French RSS feed → {feed_path.relative_to(SCRIPT_DIR)}")
    return feed_path


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate a French (fr-CA) Cariboo Signals episode with Dragon HD voices:\n"
            "  1. --dry-run   adapt an English script into Canadian French, save it, print it\n"
            "  2. (no flags)  synthesize full episode audio from the saved French script\n"
            "  2. --publish   synthesize + update podcast-fr-feed.xml for web publishing"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Stage 1: adapt the source script into French and save it for review")
    parser.add_argument("--source", default=None,
                        help="dry-run only: filename (under podcasts/) of the English script to adapt; "
                             "defaults to the most recently generated daily episode script")
    parser.add_argument("--publish", action="store_true",
                        help="After synthesis, update podcast-fr-feed.xml for web deployment")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print("  Cariboo Signals en français — Aurélie & Kaël")
    print(f"{'='*60}\n")

    from config_loader import load_podcast_config
    podcast_config = load_podcast_config()

    # ── Stage 1: adapt + save the French script for review ─────────────────
    if args.dry_run:
        source_path = _find_source_script(args.source)
        if not source_path:
            print("No English episode script found under podcasts/ to adapt.")
            print("Pass --source <filename> to point at one explicitly.")
            sys.exit(1)

        m = _SOURCE_SCRIPT_RE.match(source_path.name)
        if not m:
            print(f"'{source_path.name}' doesn't match the expected podcast_script_<date>_<theme>.txt pattern.")
            sys.exit(1)
        date_str, theme_slug = m.group(1), m.group(2)
        theme_name = _extract_theme_name(source_path)

        client = get_anthropic_client()
        if not client:
            print("ANTHROPIC_API_KEY not set. Exiting.")
            sys.exit(1)

        print(f"Source episode: {source_path.name}")
        print(f"Theme: {theme_name}\n")

        english_script = _read_script_body(source_path)
        french_script = localize_script_to_french(client, english_script, theme_name)

        word_count = len(french_script.split())
        turn_count = french_script.count("**RILEY:**") + french_script.count("**CASEY:**")
        print(f"  Adapted draft: {word_count} words, {turn_count} turns")
        out_path = write_french_script_file(french_script, date_str, theme_slug, podcast_config["title"])

        print(f"\n{'='*60}")
        print(f"  ADAPTED SCRIPT (review/edit {out_path.name}, then run with no flags to synthesize)")
        print(f"{'='*60}\n")
        print(french_script)
        return

    # ── Stage 2: synthesize from the reviewed French script ────────────────
    date_str, theme_slug, script_path = _find_french_script()
    if not script_path:
        print(f"No adapted French script found in {OUTPUT_DIR.relative_to(SCRIPT_DIR)}/.")
        print("Run with --dry-run first to generate and review one.")
        sys.exit(1)

    print(f"Using reviewed French script: {script_path.relative_to(SCRIPT_DIR)}")
    french_script = _read_script_body(script_path)

    segments = parse_script_into_segments(french_script)
    if not segments["welcome"] or not segments["news"] or not segments["deep_dive"]:
        print("⚠️  Segment parsing found gaps (welcome/news/deep_dive) — the adapted script may have")
        print("    altered a **RILEY:**/**CASEY:** tag or a segment marker. Check the file before re-running.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_mp3 = OUTPUT_DIR / f"sample_audio_fr_{date_str}_{theme_slug}.mp3"

    riley_voice = FRENCH_VOICE_MAP["riley"]["voice"]
    casey_voice = FRENCH_VOICE_MAP["casey"]["voice"]
    print(f"\nSynthesizing with Dragon HD voices:")
    print(f"  Aurélie (Riley) → {riley_voice}")
    print(f"  Kaël   (Casey)  → {casey_voice}\n")

    result, duration_sec = synthesize_french_sample(segments, output_mp3)

    if not result:
        sys.exit(1)

    print(f"\n{'='*60}")
    print("  Episode ready")
    print(f"  Audio:  {output_mp3.relative_to(SCRIPT_DIR)}")
    print(f"  Script: {script_path.relative_to(SCRIPT_DIR)}")
    print(f"{'='*60}\n")

    if args.publish:
        theme_name = _extract_theme_name(script_path)
        write_french_rss_entry(
            date_str, theme_name, theme_slug,
            output_mp3.name, duration_sec, podcast_config,
        )
        print("  RSS feed updated. Deploy with: workflow publish step or manual R2 sync.")


if __name__ == "__main__":
    main()
