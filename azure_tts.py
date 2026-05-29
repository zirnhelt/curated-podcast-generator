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
import re
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

# Cariboo region place-name pronunciations for OpenAI TTS (plain-text phonetic aliases).
# Imported in podcast_generator.py as AZURE_PRONUNCIATION_DICT for the OpenAI fallback path.
# IMPORTANT: no hyphens, no ALL-CAPS, no spaces as syllable separators — OpenAI TTS reads
# hyphens as audible pauses and spaces as full word gaps. Use single concatenated words.
PRONUNCIATION_DICT: dict[str, str] = {
    "Cariboo":        "caribou",
    "Quesnel":        "Kwenell",
    "Tŝilhqot'in":   "Tsilkohtin",
    "Secwépemc":      "Sekwepem",
    "Dakelh":         "Dahkel",
    "Nazko":          "Nazkoh",
    "Lac la Hache":   "Lack luh Hash",
    "Chilcotin":      "chilkohtin",
    "Anahim Lake":    "Anaheem Lake",
    "Alexis Creek":   "Alexis Creek",
    "Canim Lake":     "Kannim Lake",
    "100 Mile House": "One Hundred Mile House",
    "Tatla Lake":     "Tatla Lake",
}

# IPA pronunciations for Azure SSML <phoneme> tags — more precise than <sub alias>.
IPA_DICT: dict[str, str] = {
    "Cariboo":        "ˈkærɪbuː",
    "Quesnel":        "kwɛˈnɛl",
    "Tŝilhqot'in":   "tsɪlˈkoʊtɪn",
    "Secwépemc":      "sɛˈkwɛpɛm",
    "Dakelh":         "dɑˈkɛl",
    "Nazko":          "ˈnæzkoʊ",
    "Lac la Hache":   "lækləˈhæʃ",
    "Chilcotin":      "tʃɪlˈkoʊtɪn",
    "Anahim Lake":    "ˈænəhiːm leɪk",
    "Alexis Creek":   "əˈlɛksɪs kriːk",
    "Canim Lake":     "ˈkænɪm leɪk",
    "100 Mile House": "wʌn ˈhʌndrəd maɪl haʊs",
    "Tatla Lake":     "ˈtætlə leɪk",
}

# Per-speaker expression styles for <mstts:express-as> within each turn.
# Degree 1.0 = default; values 1.1–1.5 add moderate expressiveness.
# These mirror the classic character voices: Riley warm/engaged, Casey measured/thoughtful.
HOST_EXPRESSION: dict[str, dict[str, str]] = {
    "ava":    {"style": "cheerful",        "styledegree": "1.3"},
    "andrew": {"style": "newscast-casual", "styledegree": "1.1"},
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

    cfg = speechsdk.SpeechConfig(subscription=key, region=region)
    cfg.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm
    )
    _speech_config_cache = cfg
    return cfg


def apply_pronunciation(text: str) -> str:
    """XML-escape *text* and wrap known place names in IPA <phoneme> tags.

    Must be called on raw text — do not pre-escape the input.
    Returns an SSML-safe fragment (not a full document).
    """
    escaped = saxutils.escape(text)
    for word, ipa in IPA_DICT.items():
        escaped_word = saxutils.escape(word)
        phoneme_tag = f'<phoneme alphabet="ipa" ph="{ipa}">{escaped_word}</phoneme>'
        escaped = escaped.replace(escaped_word, phoneme_tag)
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
        ' xmlns:mstts="http://www.w3.org/2001/mstts"'
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
        expr = HOST_EXPRESSION.get(speaker)
        if expr:
            content = (
                f'<mstts:express-as style="{expr["style"]}" styledegree="{expr["styledegree"]}">'
                f"{processed}"
                f"</mstts:express-as>"
            )
        else:
            content = processed
        break_tag = pacing_tag_to_ssml(seg.get("gap_ms")) if i > 0 else ""
        turn_parts.append(f'{break_tag}<mstts:turn speaker="{speaker}">{content}</mstts:turn>')

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


def _count_words(text: str) -> int:
    """Count words in *text* using word-boundary tokenization."""
    return len(re.findall(r"\b\w+\b", text))


def synthesize_section(
    ssml: str,
    output_file: str | Path,
    speech_config,
    *,
    expected_word_count: int | None = None,
) -> None:
    """Synthesize *ssml* to *output_file* using the Azure Speech SDK.

    When *expected_word_count* is provided, the SDK's word-boundary events are
    used to count words actually synthesized and a warning is printed if the
    count differs from the expected value — catching silent word omissions like
    the "governance frameworks" incident without any extra API call.

    Raises RuntimeError if synthesis is cancelled or fails.
    """
    import azure.cognitiveservices.speech as speechsdk

    audio_cfg = speechsdk.audio.AudioOutputConfig(filename=str(output_file))
    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config, audio_config=audio_cfg
    )

    synthesized_word_count = 0

    if expected_word_count is not None:
        def _on_word_boundary(evt):
            nonlocal synthesized_word_count
            if evt.boundary_type == speechsdk.SpeechSynthesisBoundaryType.Word:
                synthesized_word_count += 1

        synthesizer.synthesis_word_boundary.connect(_on_word_boundary)

    result = synthesizer.speak_ssml_async(ssml).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        if expected_word_count is not None and synthesized_word_count != expected_word_count:
            diff = expected_word_count - synthesized_word_count
            print(
                f"  ⚠️  TTS word count mismatch: expected {expected_word_count}, "
                f"synthesized {synthesized_word_count} "
                f"({'missing' if diff > 0 else 'extra'} {abs(diff)} word(s))"
            )
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
        expected = sum(_count_words(seg["text"]) for seg in chunks[0])
        synthesize_section(ssml, output_file, speech_config, expected_word_count=expected)
        return

    # Multiple chunks — synthesize each and stitch with pydub
    import tempfile
    from pydub import AudioSegment
    from pydub.silence import detect_leading_silence

    def _trim_chunk_silence(segment, silence_thresh=-45, chunk_size=80):
        """Trim leading/trailing silence from a synthesized WAV chunk.

        Azure TTS adds ~200–400 ms of silence at the start and end of each
        synthesis call. Without trimming, those pad together with the explicit
        200 ms inter-chunk gap to produce dead air at chunk boundaries.
        """
        lead = detect_leading_silence(segment, silence_threshold=silence_thresh, chunk_size=chunk_size)
        trail = detect_leading_silence(segment.reverse(), silence_threshold=silence_thresh, chunk_size=chunk_size)
        end = len(segment) - trail
        return segment if end <= lead else segment[lead:end]

    chunk_audios: list[AudioSegment] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for idx, chunk in enumerate(chunks):
            chunk_file = Path(tmpdir) / f"chunk_{idx}.wav"
            ssml = build_section_ssml(chunk, voice_map=voice_map)
            expected = sum(_count_words(seg["text"]) for seg in chunk)
            synthesize_section(ssml, chunk_file, speech_config, expected_word_count=expected)
            audio = AudioSegment.from_file(str(chunk_file), format="wav")
            chunk_audios.append(_trim_chunk_silence(audio))

    combined = chunk_audios[0]
    gap = AudioSegment.silent(duration=200)
    for audio in chunk_audios[1:]:
        combined = combined + gap + audio

    combined.export(str(output_file), format="wav")
