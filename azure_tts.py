#!/usr/bin/env python3
"""
Azure Neural TTS integration with Multi-Talker support.

Provides section-level SSML synthesis via en-US-MultiTalker-Ava-Andrew:DragonHDLatestNeural.
One synthesis call per script section produces coherent prosody across speaker transitions,
replacing the per-segment OpenAI calls + manual gap stitching.

Requires:
  AZURE_SPEECH_KEY   — Azure Speech resource key
  AZURE_SPEECH_REGION — Azure region (e.g. "canadacentral", "westus2")
  pip install azure-cognitiveservices-speech>=1.37.0
"""

import os
import xml.sax.saxutils as saxutils
from pathlib import Path

MULTITALKER_MODEL = "en-US-MultiTalker-Ava-Andrew:DragonHDLatestNeural"

# Maps host keys to mstts:turn speaker identifiers used by the MultiTalker model
MULTITALKER_SPEAKER_MAP = {
    "riley": "ava",
    "casey": "andrew",
}

# Kept for backward-compatibility with any imports; not used in SSML generation
AZURE_VOICE_MAP = {
    "riley": "en-US-Ava:DragonHDLatestNeural",
    "casey": "en-US-Andrew:DragonHDLatestNeural",
}

# Conservative per-request SSML char limit (Azure caps at ~10 000 chars of SSML)
SSML_CHAR_LIMIT = 8_000

# Cariboo region place-name pronunciations injected via SSML <sub alias="..."> tags
PRONUNCIATION_DICT: dict[str, str] = {
    "Quesnel":        "Kweh-NELL",
    "Tŝilhqot'in":   "Tsill-KO-tin",
    "Secwépemc":      "Seh-KWEP-em",
    "Dakelh":         "Dah-KELH",
    "Nazko":          "NAZ-koh",
    "Lac la Hache":   "Lack-la-HASH",
    "Anahim Lake":    "AN-a-heem Lake",
    "Alexis Creek":   "a-LEX-is Creek",
    "Canim Lake":     "KAN-im Lake",
    "100 Mile House": "One-Hundred Mile House",
    "Tatla Lake":     "TAT-la Lake",
}

_speech_config_cache = None


def get_azure_speech_config():
    """Return a cached SpeechConfig, or None if credentials are absent."""
    global _speech_config_cache
    if _speech_config_cache is not None:
        return _speech_config_cache

    key = os.environ.get("AZURE_SPEECH_KEY")
    region = os.environ.get("AZURE_SPEECH_REGION")
    if not key or not region:
        return None

    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError:
        raise ImportError(
            "azure-cognitiveservices-speech is not installed. "
            "Run: pip install azure-cognitiveservices-speech>=1.37.0"
        )

    # Use the cognitive-services endpoint directly so preview voices (e.g.
    # MultiTalker) are reachable. The SDK's default region-derived endpoint
    # uses {region}.tts.speech.microsoft.com, which may route to a different
    # cluster where preview voices are unavailable.
    if region.startswith("http"):
        endpoint = region.rstrip("/")
    else:
        endpoint = f"https://{region}.api.cognitive.microsoft.com"
    cfg = speechsdk.SpeechConfig(subscription=key, endpoint=endpoint)
    cfg.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm
    )
    _speech_config_cache = cfg
    return cfg


def apply_pronunciation(text: str) -> str:
    """XML-escape *text* and wrap known place names in <sub alias="..."> tags.

    Must be called on raw text — do not pre-escape the input.
    Returns an SSML-safe fragment (not a full document).
    """
    escaped = saxutils.escape(text)
    for word, alias in PRONUNCIATION_DICT.items():
        escaped_word = saxutils.escape(word)
        sub_tag = f'<sub alias="{saxutils.escape(alias)}">{escaped_word}</sub>'
        escaped = escaped.replace(escaped_word, sub_tag)
    return escaped


def pacing_tag_to_ssml(gap_ms: int | None) -> str:
    """Convert a parsed gap_ms value to an SSML <break> element.

    Multi-Talker handles natural inter-speaker timing, so None gaps are dropped.
    Negative (overlap) values can't be expressed in SSML and are also dropped.
    """
    if gap_ms is None or gap_ms <= 0:
        return ""
    return f'<break time="{gap_ms}ms"/>'


def _build_ssml_doc(inner: str) -> str:
    return (
        '<speak version="1.0"'
        ' xmlns="http://www.w3.org/2001/10/synthesis"'
        ' xmlns:mstts="https://www.w3.org/2001/mstts"'
        ' xml:lang="en-US">'
        f"{inner}"
        "</speak>"
    )


def build_section_ssml(
    segments: list[dict],
    voice_map: dict[str, str] | None = None,
    style: str = "conversational",
) -> str:
    """Build a complete <speak> SSML document for one podcast section.

    Uses the MultiTalker format: a single <voice> element wrapping
    <mstts:dialog> with <mstts:turn speaker="ava/andrew"> per segment.
    The voice_map parameter is unused but kept for API compatibility.
    """
    turn_parts: list[str] = []
    for i, seg in enumerate(segments):
        speaker = MULTITALKER_SPEAKER_MAP.get(seg["speaker"], "ava")
        processed = apply_pronunciation(seg["text"])
        if i > 0:
            break_el = pacing_tag_to_ssml(seg.get("gap_ms"))
            if break_el:
                turn_parts.append(break_el)
        turn_parts.append(f'<mstts:turn speaker="{speaker}">{processed}</mstts:turn>')

    inner = (
        f'<voice name="{MULTITALKER_MODEL}">'
        f"<mstts:dialog>"
        + "".join(turn_parts)
        + "</mstts:dialog>"
        + "</voice>"
    )
    return _build_ssml_doc(inner)


def _split_segments_by_char_limit(
    segments: list[dict],
    limit: int = SSML_CHAR_LIMIT,
) -> list[list[dict]]:
    """Partition segments into sub-lists whose SSML stays under *limit* chars."""
    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0

    for seg in segments:
        # Rough SSML size estimate: text + ~120 chars of tags per segment
        seg_size = len(seg["text"]) + 120
        if current and current_chars + seg_size > limit:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(seg)
        current_chars += seg_size

    if current:
        chunks.append(current)

    return chunks


def synthesize_section(
    ssml: str,
    output_file: str | Path,
    speech_config,
) -> None:
    """Synthesize *ssml* to *output_file* using the Azure Speech SDK.

    Raises RuntimeError if synthesis is cancelled or fails.
    """
    import azure.cognitiveservices.speech as speechsdk

    audio_cfg = speechsdk.audio.AudioOutputConfig(filename=str(output_file))
    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config, audio_config=audio_cfg
    )
    result = synthesizer.speak_ssml_async(ssml).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return

    if result.reason == speechsdk.ResultReason.Canceled:
        details = speechsdk.SpeechSynthesisCancellationDetails(result)
        raise RuntimeError(
            f"Azure TTS cancelled: {details.reason} — {details.error_details}"
        )

    raise RuntimeError(f"Azure TTS unexpected result: {result.reason}")


def generate_azure_tts_for_section(
    segments: list[dict],
    output_file: str | Path,
    voice_map: dict[str, str] | None = None,
) -> None:
    """High-level entry: SSML build → synthesize → write WAV to output_file.

    Handles SSML character-limit chunking automatically; chunks are concatenated
    with a 200 ms break between them using pydub.
    """
    speech_config = get_azure_speech_config()
    if speech_config is None:
        raise ValueError(
            "Azure TTS credentials not configured. "
            "Set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION."
        )

    chunks = _split_segments_by_char_limit(segments)

    if len(chunks) == 1:
        ssml = build_section_ssml(chunks[0], voice_map=voice_map)
        synthesize_section(ssml, output_file, speech_config)
        return

    # Multiple chunks — synthesize each and stitch with pydub
    import tempfile
    from pydub import AudioSegment

    chunk_audios: list[AudioSegment] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for idx, chunk in enumerate(chunks):
            chunk_file = Path(tmpdir) / f"chunk_{idx}.wav"
            ssml = build_section_ssml(chunk, voice_map=voice_map)
            synthesize_section(ssml, chunk_file, speech_config)
            chunk_audios.append(AudioSegment.from_file(str(chunk_file), format="wav"))

    combined = chunk_audios[0]
    gap = AudioSegment.silent(duration=200)
    for audio in chunk_audios[1:]:
        combined = combined + gap + audio

    combined.export(str(output_file), format="wav")
