#!/usr/bin/env python3
"""
TTS Evaluation Script: Azure Multi-Talker vs OpenAI TTS

Reads the most recent podcast script from podcasts/, extracts sample sections,
generates audio from both providers, and prints a comparison report.

Usage:
    python evaluate_tts.py --section all --output-dir /tmp/tts-eval
    python evaluate_tts.py --section news --skip-openai
    python evaluate_tts.py --section deep_dive --skip-azure

Requirements:
    AZURE_SPEECH_KEY + AZURE_SPEECH_REGION  — for Azure path
    OPENAI_API_KEY                           — for OpenAI path
"""

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure project root is on the path
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent))


def _find_latest_script(podcasts_dir: Path) -> dict | None:
    """Return the parsed JSON of the most recently saved podcast episode."""
    candidates = sorted(podcasts_dir.glob("podcast_data_*.json"), reverse=True)
    if not candidates:
        return None
    with open(candidates[0]) as f:
        return json.load(f)


def _load_segments(script_text: str) -> dict:
    """Parse a raw podcast script string into section segments."""
    from podcast_generator import parse_script_into_segments
    return parse_script_into_segments(script_text)


def _generate_openai_section(
    seg_list: list[dict],
    section_name: str,
    output_dir: Path,
) -> tuple[Path, float, float]:
    """Generate per-segment OpenAI TTS and stitch into one file.

    Returns (output_path, duration_s, elapsed_s).
    """
    from openai import OpenAI
    from pydub import AudioSegment
    from podcast_generator import (
        normalize_segment,
        trim_tts_silence,
        heuristic_gap_ms,
        _append_with_gap,
        TARGET_SPEECH_DBFS,
    )
    from config_loader import get_voice_for_host
    from azure_tts import PRONUNCIATION_DICT

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    t0 = time.time()
    combined = AudioSegment.empty()
    prev_speaker = None

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, seg in enumerate(seg_list):
            clean = seg["text"]
            for word, alias in PRONUNCIATION_DICT.items():
                clean = clean.replace(word, alias)

            voice = get_voice_for_host(seg["speaker"])
            resp = client.audio.speech.create(
                model="tts-1", voice=voice, input=clean, speed=1.0
            )
            tmp_mp3 = Path(tmpdir) / f"seg_{i:03d}.mp3"
            tmp_mp3.write_bytes(resp.content)
            speech = normalize_segment(
                trim_tts_silence(AudioSegment.from_mp3(str(tmp_mp3))), TARGET_SPEECH_DBFS
            )
            gap = seg.get("gap_ms")
            if gap is None:
                gap = heuristic_gap_ms(seg["text"], prev_speaker, seg["speaker"], section=section_name)
            combined = _append_with_gap(combined, speech, gap)
            prev_speaker = seg["speaker"]

    elapsed = time.time() - t0
    out_path = output_dir / f"openai_{section_name}.mp3"
    combined.export(str(out_path), format="mp3")
    return out_path, len(combined) / 1000, elapsed


def _generate_azure_section(
    seg_list: list[dict],
    section_name: str,
    output_dir: Path,
) -> tuple[Path, float, float]:
    """Generate one Azure Multi-Talker call for the section.

    Returns (output_path, duration_s, elapsed_s).
    """
    from pydub import AudioSegment
    from azure_tts import generate_azure_tts_for_section
    from podcast_generator import normalize_segment, trim_tts_silence, TARGET_SPEECH_DBFS

    t0 = time.time()
    out_wav = output_dir / f"azure_{section_name}.wav"
    generate_azure_tts_for_section(seg_list, out_wav)
    elapsed = time.time() - t0

    audio = normalize_segment(
        trim_tts_silence(AudioSegment.from_file(str(out_wav), format="wav")),
        TARGET_SPEECH_DBFS,
    )
    # Re-export as WAV at normalised level
    audio.export(str(out_wav), format="wav")
    return out_wav, len(audio) / 1000, elapsed


def _audio_stats(path: Path) -> dict:
    """Return basic audio stats for a file."""
    from pydub import AudioSegment
    ext = path.suffix.lstrip(".")
    audio = AudioSegment.from_file(str(path), format=ext)
    return {
        "duration_s": round(len(audio) / 1000, 1),
        "dbfs": round(audio.dBFS, 1),
    }


def _char_count(seg_list: list[dict]) -> int:
    return sum(len(s["text"]) for s in seg_list)


def _print_report(section: str, openai_result, azure_result) -> None:
    header = f"=== TTS Evaluation: {section} ==="
    print("\n" + header)
    print("-" * len(header))

    rows = []
    if openai_result:
        path, dur, elapsed = openai_result
        rows.append(("OpenAI (tts-1)", dur, elapsed, path))
    if azure_result:
        path, dur, elapsed = azure_result
        rows.append(("Azure Multi-Talker", dur, elapsed, path))

    print(f"{'Provider':<22} {'Duration':>10} {'Latency':>10}")
    print("-" * 45)
    for name, dur, elapsed, path in rows:
        print(f"{name:<22} {dur:>9.1f}s {elapsed:>9.1f}s")

    print()
    for name, dur, elapsed, path in rows:
        print(f"  {name}: {path}")


def main():
    parser = argparse.ArgumentParser(description="Compare OpenAI vs Azure TTS for Cariboo Signals")
    parser.add_argument(
        "--section",
        choices=["welcome", "news", "community_spotlight", "deep_dive", "all"],
        default="all",
    )
    parser.add_argument("--output-dir", default="/tmp/tts-eval")
    parser.add_argument("--skip-openai", action="store_true")
    parser.add_argument("--skip-azure", action="store_true")
    parser.add_argument("--podcasts-dir", default="podcasts")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    podcasts_dir = Path(args.podcasts_dir)

    print(f"Loading latest podcast script from {podcasts_dir}/...")
    data = _find_latest_script(podcasts_dir)
    if not data:
        print(f"❌ No podcast_data_*.json found in {podcasts_dir}/")
        sys.exit(1)

    script_text = data.get("script") or data.get("raw_script") or ""
    if not script_text:
        print("❌ Script field not found in podcast data JSON")
        sys.exit(1)

    print("Parsing script into sections...")
    all_segments = _load_segments(script_text)

    sections_to_eval = (
        ["welcome", "news", "community_spotlight", "deep_dive"]
        if args.section == "all"
        else [args.section]
    )

    for section in sections_to_eval:
        seg_list = all_segments.get(section, [])
        if not seg_list:
            print(f"  Skipping {section}: no segments found")
            continue

        chars = _char_count(seg_list)
        estimated_azure_cost = chars / 1_000_000 * 22
        print(f"\n▶ {section}: {len(seg_list)} turns, {chars} chars (~${estimated_azure_cost:.4f} Azure)")

        openai_result = None
        azure_result = None

        if not args.skip_openai and os.getenv("OPENAI_API_KEY"):
            print(f"  Generating OpenAI TTS for {section}...")
            try:
                openai_result = _generate_openai_section(seg_list, section, output_dir)
            except Exception as e:
                print(f"  ⚠️  OpenAI failed: {e}")
        elif not args.skip_openai:
            print("  Skipping OpenAI: OPENAI_API_KEY not set")

        if not args.skip_azure and os.getenv("AZURE_SPEECH_KEY"):
            print(f"  Generating Azure Multi-Talker TTS for {section}...")
            try:
                azure_result = _generate_azure_section(seg_list, section, output_dir)
            except Exception as e:
                print(f"  ⚠️  Azure failed: {e}")
        elif not args.skip_azure:
            print("  Skipping Azure: AZURE_SPEECH_KEY not set")

        _print_report(section, openai_result, azure_result)

    print(f"\n✅ Evaluation complete. Files in: {output_dir}")
    print("Listen to the output files and compare naturalness at speaker transitions.")


if __name__ == "__main__":
    main()
