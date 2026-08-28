#!/usr/bin/env python3
"""
TTS Evaluation Script: Azure Multi-Talker vs OpenAI TTS vs Gemini multi-speaker

Reads the most recent podcast script from podcasts/, extracts sample sections,
generates audio from each provider, and prints a comparison report.

Usage:
    python evaluate_tts.py --section all --output-dir /tmp/tts-eval
    python evaluate_tts.py --section news --skip-openai
    python evaluate_tts.py --section deep_dive --skip-azure --skip-gemini

Requirements:
    AZURE_SPEECH_KEY + AZURE_SPEECH_REGION  — for Azure path
    OPENAI_API_KEY                           — for OpenAI path
    GEMINI_API_KEY                           — for Gemini path (GEMINI_TTS_MODEL to override model)
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
    """Return {"script": ...} for the most recently saved podcast episode.

    Prefers podcast_data_*.json (local pipeline runs); falls back to the
    committed podcast_script_*.txt files so the eval works on a fresh clone.
    """
    candidates = sorted(podcasts_dir.glob("podcast_data_*.json"), reverse=True)
    if candidates:
        with open(candidates[0]) as f:
            return json.load(f)

    scripts = sorted(podcasts_dir.glob("podcast_script_*.txt"), reverse=True)
    if scripts:
        print(f"  Using committed script: {scripts[0].name}")
        return {"script": scripts[0].read_text()}

    return None


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
        _openai_speech_request,
        OPENAI_TTS_MODEL,
        TARGET_SPEECH_DBFS,
    )
    from azure_tts import PRONUNCIATION_DICT

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    print(f"    OpenAI model: {OPENAI_TTS_MODEL}")
    t0 = time.time()
    combined = AudioSegment.empty()
    prev_speaker = None

    from config_loader import strip_stage_directions

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, seg in enumerate(seg_list):
            clean = strip_stage_directions(seg["text"])
            for word, alias in PRONUNCIATION_DICT.items():
                clean = clean.replace(word, alias)

            # Same request the render path builds — model, voice, and either
            # `speed` or `instructions`. Hand-rolling a tts-1 request here meant
            # the one tool for auditioning a model swap rendered neither the
            # model under test nor the hosts' configured pace.
            request, _ = _openai_speech_request(seg["speaker"])
            resp = client.audio.speech.create(input=clean, **request)
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


def _generate_gemini_section(
    seg_list: list[dict],
    section_name: str,
    output_dir: Path,
) -> tuple[Path, float, float]:
    """Generate one Gemini multi-speaker call for the section.

    Returns (output_path, duration_s, elapsed_s).
    """
    from pydub import AudioSegment
    from gemini_tts import generate_gemini_tts_for_section
    from podcast_generator import normalize_segment, trim_tts_silence, TARGET_SPEECH_DBFS

    t0 = time.time()
    out_wav = output_dir / f"gemini_{section_name}.wav"
    generate_gemini_tts_for_section(seg_list, out_wav)
    elapsed = time.time() - t0

    audio = normalize_segment(
        trim_tts_silence(AudioSegment.from_file(str(out_wav), format="wav")),
        TARGET_SPEECH_DBFS,
    )
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


def _print_report(section: str, openai_result, azure_result, gemini_result=None) -> None:
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
    if gemini_result:
        path, dur, elapsed = gemini_result
        model = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
        rows.append((f"Gemini ({model.replace('gemini-', '')})", dur, elapsed, path))

    print(f"{'Provider':<22} {'Duration':>10} {'Latency':>10}")
    print("-" * 45)
    for name, dur, elapsed, path in rows:
        print(f"{name:<22} {dur:>9.1f}s {elapsed:>9.1f}s")

    print()
    for name, dur, elapsed, path in rows:
        print(f"  {name}: {path}")


def _probe_gemini_rungs(seg_list: list[dict], repeats: int) -> None:
    """Report which prompt shapes Gemini actually accepts, rung by rung.

    `finishReason: OTHER` comes back with promptTokenCount == totalTokenCount —
    the request was accepted and tokenized and the model produced nothing, which
    is a rejection of *what* was asked rather than of when. This isolates which
    part of the prompt is responsible by asking the same text with progressively
    less wrapped around it, several times each so a one-off 500 is not mistaken
    for a rejection. If a single rung turns out to be the culprit, fixing that is
    worth more than the whole retry ladder.

    Each row is one live synthesis call — this spends real API budget, which is
    why it is a flag rather than part of the default eval.
    """
    import gemini_tts

    print(f"\n▶ Gemini prompt-shape probe: {len(seg_list)} turns, "
          f"{_char_count(seg_list)} chars, {repeats}x per rung")
    print(f"  {'rung':<34} {'ok':>5}  detail")

    for i, rung in enumerate(gemini_tts.RETRY_LADDER):
        ok = 0
        errors: list[str] = []
        for attempt in range(repeats):
            try:
                gemini_tts._attempt(
                    seg_list, "", rung,
                    seed=gemini_tts.GEMINI_TTS_SEED + attempt,
                    read_timeout=gemini_tts._read_timeout_for(seg_list),
                )
                ok += 1
            except Exception as e:
                errors.append(type(e).__name__ + ": " + str(e)[:80])
        label = f"{i} {gemini_tts._rung_label(rung)}"
        print(f"  {label:<34} {ok:>2}/{repeats}  {errors[0] if errors else ''}")

    print("\n  Read it like this: rung 0 failing while a later rung passes names the")
    print("  prompt element Gemini is rejecting. Every rung failing equally is an")
    print("  outage or a quota wall, not a prompt problem — check the error text.")


def _probe_gemini_models(seg_list: list[dict], models: list[str], repeats: int) -> None:
    """Answer rate and latency per candidate model, on the real section request.

    The rung probe asks *what shape* Gemini will accept. This asks the two
    questions that actually decide whether an episode can ride on Gemini:
    which model answers, and how long an answer takes when it comes.

    Both were unanswerable from the nightly logs on 2026-08-28. The model was
    masked (sourced from a secret), and nothing recorded latency, so a flat
    120 s read timeout survived unexamined while three unanswered small
    requests spent a section budget between them. Run this before trusting a
    nightly run to a new model, and use the p95 to refit
    READ_TIMEOUT_MS_PER_CHAR.

    Each row is `repeats` live syntheses — this spends real API budget.
    """
    import gemini_tts

    chars = _char_count(seg_list)
    print(f"\n▶ Gemini model probe: {len(seg_list)} turns, {chars} chars, "
          f"{repeats}x per model")
    print(f"  read timeout in effect: {gemini_tts._read_timeout_for(seg_list)}s")
    print(f"\n  {'model':<34} {'ok':>6}  {'median':>8} {'slowest':>8}  detail")

    for model in models:
        latencies: list[float] = []
        errors: list[str] = []
        gemini_tts.set_model_override(model)
        for attempt in range(repeats):
            started = time.monotonic()
            try:
                gemini_tts._attempt(
                    seg_list, "", gemini_tts.RETRY_LADDER[0],
                    seed=gemini_tts.GEMINI_TTS_SEED + attempt,
                    read_timeout=gemini_tts._read_timeout_for(seg_list),
                )
                latencies.append(time.monotonic() - started)
            except Exception as e:
                errors.append(type(e).__name__ + ": " + str(e)[:70])
        gemini_tts.set_model_override(None)

        ok = f"{len(latencies)}/{repeats}"
        if latencies:
            ordered = sorted(latencies)
            median = f"{ordered[len(ordered) // 2]:.1f}s"
            slowest = f"{ordered[-1]:.1f}s"
        else:
            median = slowest = "—"
        print(f"  {model:<34} {ok:>6}  {median:>8} {slowest:>8}  "
              f"{errors[0] if errors else ''}")

    print("\n  Pin GEMINI_TTS_MODEL to whichever model answers most often — a model")
    print("  that cannot answer a real section is not made usable by the ladder.")
    print(f"  For the timeout: ms/char = slowest / {chars} chars * 1000, times a")
    print("  safety factor. A slowest well under the leash means the leash is loose.")


def main():
    parser = argparse.ArgumentParser(description="Compare OpenAI vs Azure TTS for Cariboo Signals")
    parser.add_argument(
        "--section",
        choices=["welcome", "news", "community_spotlight", "deep_dive", "all"],
        default="all",
    )
    parser.add_argument("--output-dir", default=os.path.join(tempfile.gettempdir(), "tts-eval"))
    parser.add_argument("--skip-openai", action="store_true")
    parser.add_argument("--skip-azure", action="store_true")
    parser.add_argument("--skip-gemini", action="store_true")
    parser.add_argument("--podcasts-dir", default="podcasts")
    parser.add_argument(
        "--probe-gemini", action="store_true",
        help="Instead of comparing providers, ask Gemini the same text with "
             "progressively less prompt around it, to find which element it rejects",
    )
    parser.add_argument(
        "--probe-models", action="store_true",
        help="Instead of comparing providers, measure answer rate and latency "
             "for each candidate Gemini model on a real section request",
    )
    parser.add_argument(
        "--probe-repeats", type=int, default=3,
        help="Calls per rung/model in probe modes (each one spends budget)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    podcasts_dir = Path(args.podcasts_dir)

    print(f"Loading latest podcast script from {podcasts_dir}/...")
    data = _find_latest_script(podcasts_dir)
    if not data:
        print(f"❌ No podcast_data_*.json or podcast_script_*.txt found in {podcasts_dir}/")
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

    if args.probe_gemini or args.probe_models:
        if not os.getenv("GEMINI_API_KEY"):
            print("❌ Gemini probes need GEMINI_API_KEY")
            sys.exit(1)
        import gemini_tts
        # dict.fromkeys dedupes while keeping order, for when both env vars name
        # the same model and there is no second thing to try.
        models = list(dict.fromkeys(
            (gemini_tts.GEMINI_TTS_MODEL, gemini_tts.GEMINI_TTS_FALLBACK_MODEL)
        ))
        for section in sections_to_eval:
            seg_list = all_segments.get(section, [])
            if not seg_list:
                print(f"  Skipping {section}: no segments found")
                continue
            if args.probe_models:
                _probe_gemini_models(seg_list, models, args.probe_repeats)
            if args.probe_gemini:
                _probe_gemini_rungs(seg_list, args.probe_repeats)
        return

    for section in sections_to_eval:
        seg_list = all_segments.get(section, [])
        if not seg_list:
            print(f"  Skipping {section}: no segments found")
            continue

        chars = _char_count(seg_list)
        estimated_azure_cost = chars / 1_000_000 * 22
        estimated_gemini_cost = chars / 1_000 * 0.04  # Flash TTS ≈ $0.04/1k chars
        print(f"\n▶ {section}: {len(seg_list)} turns, {chars} chars "
              f"(~${estimated_azure_cost:.4f} Azure, ~${estimated_gemini_cost:.4f} Gemini Flash)")

        openai_result = None
        azure_result = None
        gemini_result = None

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

        if not args.skip_gemini and os.getenv("GEMINI_API_KEY"):
            print(f"  Generating Gemini multi-speaker TTS for {section}...")
            try:
                gemini_result = _generate_gemini_section(seg_list, section, output_dir)
            except Exception as e:
                print(f"  ⚠️  Gemini failed: {e}")
        elif not args.skip_gemini:
            print("  Skipping Gemini: GEMINI_API_KEY not set")

        _print_report(section, openai_result, azure_result, gemini_result)

    print(f"\n✅ Evaluation complete. Files in: {output_dir}")
    print("Listen to the output files and compare naturalness at speaker transitions.")


if __name__ == "__main__":
    main()
