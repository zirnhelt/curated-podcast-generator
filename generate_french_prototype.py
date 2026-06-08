#!/usr/bin/env python3
"""
French (fr-CA) episode prototype — adapt a script and synthesize a sample

One-off exploration for offering Cariboo Signals in Canadian French alongside
the English show. Takes an already-generated English daily episode script,
adapts it into natural, broadcast-neutral Canadian French via Claude, and
synthesizes a sample using Azure Neural TTS fr-CA voices.

This is a LISTENING SAMPLE, not a publishable episode: no music interludes,
no RSS feed changes, no R2 sync, nothing written outside
podcasts/french_prototype/. The point is to judge translation quality, host
voice fit, and — especially — whether the fr-CA accent reads as "neutral" to
a Canadian audience, before deciding whether to build out a twin French feed.

Two stages, reviewed in order (mirrors generate_intro_episode.py):

    python generate_french_prototype.py --dry-run   # 1. adapt + save FR script for review
    python generate_french_prototype.py             # 2. synthesize sample audio from that script
"""

import argparse
import re
import sys
import tempfile
import xml.sax.saxutils as saxutils
from pathlib import Path

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

# Canadian French (fr-CA) neural voices — the standard broadcast-neutral
# locale, distinct from France French (fr-FR) or regional joual. Sylvie/
# Antoine are Azure's standard fr-CA announcer-style voices; check the Azure
# Speech voice gallery for newer fr-CA options (availability changes) before
# committing beyond this prototype.
FRENCH_VOICE_MAP = {
    "riley": "fr-CA-SylvieNeural",
    "casey": "fr-CA-AntoineNeural",
}
FRENCH_LOCALE = "fr-CA"

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
            candidates.append((m.group(1), p))  # (date_str, path) — date_str sorts lexically = chronologically
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
        f.write(f"# {podcast_title} — adaptation française (prototype) — {date_str}\n")
        f.write(f"# Adapted from: podcast_script_{date_str}_{theme_slug}.txt\n\n")
        f.write(french_script)
    print(f"  French script → {out_path.relative_to(SCRIPT_DIR)}")
    return out_path


def _find_french_script():
    """Locate the most recently saved adapted script (there should be only one per run)."""
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


# ── Stage 2: synthesize a sample with fr-CA Azure Neural voices ────────────

def _build_single_voice_ssml(text, voice_name, lang=FRENCH_LOCALE):
    """Wrap one turn of plain text in a minimal single-voice SSML document.

    The MultiTalker dialog format in azure_tts.py is en-US only — fr-CA needs
    standard single-voice synthesis, one turn per call (mirrors the OpenAI
    per-segment fallback path used for the English show).
    """
    inner = f'<voice name="{voice_name}">{saxutils.escape(text)}</voice>'
    return (
        '<speak version="1.0"'
        ' xmlns="http://www.w3.org/2001/10/synthesis"'
        ' xmlns:mstts="http://www.w3.org/2001/mstts"'
        f' xml:lang="{lang}">{inner}</speak>'
    )


def synthesize_french_sample(segments, output_mp3):
    speech_config = get_azure_speech_config()
    if speech_config is None:
        print("  ❌ AZURE_SPEECH_KEY / AZURE_SPEECH_REGION not set — cannot synthesize fr-CA audio")
        return None

    section_order = [
        ("welcome", "Welcome / Accueil"),
        ("news", "News Roundup / Revue de l'actualité"),
        ("community_spotlight", "Community Spotlight / Vitrine communautaire"),
        ("deep_dive", "Deep Dive / Analyse approfondie"),
    ]

    combined = AudioSegment.empty()
    section_gap = AudioSegment.silent(duration=600)
    prev_speaker = None
    turn_idx = 0
    total_turns = sum(len(segments.get(key) or []) for key, _ in section_order)

    with tempfile.TemporaryDirectory() as tmpdir:
        for key, label in section_order:
            seg_list = segments.get(key) or []
            if not seg_list:
                continue
            print(f"  {label}: {len(seg_list)} turns")
            if len(combined) > 0:
                combined += section_gap

            for turn in seg_list:
                turn_idx += 1
                speaker = turn["speaker"]
                voice_name = FRENCH_VOICE_MAP.get(speaker, FRENCH_VOICE_MAP["riley"])
                wav_path = Path(tmpdir) / f"turn_{turn_idx:03d}.wav"
                print(f"    [{turn_idx}/{total_turns}] {speaker} ({voice_name}): {len(turn['text'])} chars")

                ssml = _build_single_voice_ssml(turn["text"], voice_name)
                synthesize_section(ssml, wav_path, speech_config)

                speech = normalize_segment(
                    trim_tts_silence(AudioSegment.from_file(wav_path, format="wav")),
                    TARGET_SPEECH_DBFS,
                )
                gap = turn.get("gap_ms")
                if gap is None:
                    gap = heuristic_gap_ms(turn["text"], prev_speaker, speaker)
                combined = _append_with_gap(combined, speech, gap)
                prev_speaker = speaker

    combined.export(str(output_mp3), format="mp3")
    duration_min = len(combined) / 1000 / 60
    size_mb = output_mp3.stat().st_size / 1024 / 1024
    print(f"  Sample audio: {duration_min:.1f} min, {size_mb:.1f} MB → {output_mp3.name}")
    return output_mp3


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate a sample French (fr-CA) adaptation of a Cariboo Signals episode, "
            "for listening review only — never published to any feed:\n"
            "  1. --dry-run   adapt an English script into Canadian French, save it, print it\n"
            "  2. (no flags)  synthesize sample audio from that saved French script"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Stage 1: adapt the source script into French and save it for review — stops there")
    parser.add_argument("--source", default=None,
                        help="Stage 1 only: filename (under podcasts/) or path of the English script to adapt; "
                             "defaults to the most recently generated daily episode script")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print("  French (fr-CA) episode prototype — Cariboo Signals")
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

    # ── Stage 2: synthesize a sample from the reviewed French script ───────
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

    print(f"\nSynthesizing sample with fr-CA voices "
          f"(Riley → {FRENCH_VOICE_MAP['riley']}, Casey → {FRENCH_VOICE_MAP['casey']})...")
    result = synthesize_french_sample(segments, output_mp3)

    if result:
        print(f"\n{'='*60}")
        print("  Sample ready for listening review")
        print(f"  Audio:  {output_mp3.relative_to(SCRIPT_DIR)}")
        print(f"  Script: {script_path.relative_to(SCRIPT_DIR)}")
        print(f"{'='*60}\n")
        print("Listen for: natural Canadian French delivery, whether the accent reads as")
        print("'neutral' (vs. too Parisian or too regionally marked), pacing/gaps, and")
        print("pronunciation of Cariboo place names (the existing English pronunciation")
        print("dictionaries don't transfer to French — that's expected at this stage).")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
