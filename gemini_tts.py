#!/usr/bin/env python3
"""
Gemini multi-speaker TTS integration (NotebookLM-style dialog rendering).

One generateContent call per script section renders the whole two-host
conversation with coherent cross-speaker prosody, replacing per-segment
synthesis + manual gap stitching. A style prompt from config/prompts.json
controls delivery, and whitelisted parenthetical stage directions in the
script text are performed rather than read aloud.

Plain REST via requests — no SDK dependency.

Requires:
  GEMINI_API_KEY   — Google AI Studio key
Optional:
  GEMINI_TTS_MODEL — default gemini-2.5-flash-preview-tts
"""

import base64
import os
import re
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import requests

# ponytail: reuse azure_tts's segment splitter instead of writing a second one
from azure_tts import PRONUNCIATION_DICT, _split_segments_by_char_limit
from config_loader import get_gemini_voice_for_host, load_hosts_config, load_prompts_config

# `or` (not a getenv default) so a present-but-empty env var — e.g. an unset
# workflow secret expanding to "" — still falls back to the default model.
# .strip() guards against a trailing-whitespace secret/variable value, which
# otherwise lands in the URL as a literal "%20" and Gemini 400s on it.
GEMINI_TTS_MODEL = (os.getenv("GEMINI_TTS_MODEL") or "").strip() or "gemini-2.5-flash-preview-tts"
GEMINI_TTS_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Each script section (and any char-limit chunk within it) is its own
# generateContent call. Without a pinned seed/temperature, every call samples
# delivery independently and the hosts' voices drift across sections. A fixed
# seed plus low temperature keeps timbre/pacing consistent call-to-call.
# Temperature is kept tight (favoring determinism over prosodic variety) since
# call-to-call drift is the dominant voice-consistency risk in this pipeline.
GEMINI_TTS_SEED = int(os.getenv("GEMINI_TTS_SEED", "42"))
GEMINI_TTS_TEMPERATURE = float(os.getenv("GEMINI_TTS_TEMPERATURE", "0.35"))

# Per-request transcript budget (chars). TTS models have a small context and a
# capped audio output length; ~8 500 chars ≈ 8–9 min of speech stays safely
# under both, while cutting the needless extra chunk (and extra independent
# sampling draw) that 6 000 caused for sections just over that mark. Longer
# sections are split at speaker-turn boundaries.
TRANSCRIPT_CHAR_LIMIT = 8_500

# Chars of the previous chunk/section's transcript carried forward as
# already-spoken context so the next call continues in the same voice
# instead of resampling delivery from a cold start.
CONTEXT_TAIL_CHARS = 400

# Fail-fast ceiling — no single section should ever approach this; hitting it
# means a parsing bug upstream, not a long section. Raise instead of spending.
MAX_REQUEST_CHARS = 40_000

# Output PCM format when the response omits a rate in its mime type
DEFAULT_SAMPLE_RATE = 24_000
SAMPLE_WIDTH_BYTES = 2  # s16le
INTER_CHUNK_GAP_MS = 200

# ~150 wpm ≈ 400 ms/word — same duration-ratio checksum as the OpenAI path
EXPECTED_MS_PER_WORD = 400

# Below this ratio the response isn't fast pacing, it's dropped content — a technically
# successful call whose audio is a fraction of what the transcript requires. Observed
# 2026-07-29: a retried news chunk (978 words) came back as 83s of audio (21% of the
# ~391s expected) and shipped in the published episode, because the duration check only
# printed a warning. Treated as retryable, same as a no-audio dud (below) — the per-attempt
# seed offset means a retry actually samples a different response instead of reproducing
# the same truncated one. The wider 0.80 threshold in _duration_check stays warning-only:
# quick back-and-forth banter genuinely renders faster than the flat 400 ms/word estimate,
# so it's expected to trip sometimes and isn't worth burning a retry over.
SEVERE_TRUNCATION_RATIO = 0.5

# (connect, read) timeouts in seconds. Worst case across all 3 attempts plus
# backoff is ~7 min, vs the 15 min a hung server cost with the old 600 s read
# timeout (2026-07-27 run: three ~5-min stalls before RemoteDisconnected).
REQUEST_CONNECT_TIMEOUT = 15
REQUEST_READ_TIMEOUT = 120

# Chars of an HTTP error body to surface. Gemini's structured error.details
# (e.g. the QuotaFailure block naming the exceeded quota) sits past the 300-char
# mark that logs used to truncate at.
ERROR_BODY_CHARS = 2_000


def get_gemini_api_key() -> str | None:
    """Return the Gemini API key, or None if not configured."""
    return os.environ.get("GEMINI_API_KEY") or None


def _display_name(host_key: str) -> str:
    """Speaker label used in the transcript and voice config (e.g. 'Riley')."""
    return load_hosts_config()[host_key].get("name", host_key.title())


def _style_prompt() -> str:
    return load_prompts_config().get("gemini_tts", {}).get("style_prompt", "")


def apply_pronunciation(text: str) -> str:
    """Substitute Cariboo place-name phonetic aliases (plain text, no SSML)."""
    for word, alias in PRONUNCIATION_DICT.items():
        text = text.replace(word, alias)
    return text


def build_transcript(segments: list[dict]) -> str:
    """Build the speaker-labeled transcript for one request."""
    lines = []
    for seg in segments:
        lines.append(f"{_display_name(seg['speaker'])}: {apply_pronunciation(seg['text'])}")
    return "\n".join(lines)


def _build_payload(segments: list[dict], context_tail: str = "") -> dict:
    """Build the generateContent request body for a section's segments."""
    speakers = list(dict.fromkeys(seg["speaker"] for seg in segments))
    if len(speakers) > 2:
        raise ValueError(f"Gemini multi-speaker TTS supports 2 speakers, got {speakers}")

    transcript = build_transcript(segments)
    style = _style_prompt()
    names = " and ".join(_display_name(s) for s in speakers)
    context = (
        f"CONTEXT — already spoken immediately before this, do not repeat, "
        f"continue in the exact same voice and energy:\n{context_tail}\n\n"
        if context_tail else ""
    )
    prompt = (
        (style + "\n\n" if style else "")
        + context
        + f"TTS the following conversation between {names}:\n{transcript}"
    )

    if len(speakers) == 1:
        speech_config = {
            "voiceConfig": {
                "prebuiltVoiceConfig": {"voiceName": get_gemini_voice_for_host(speakers[0])}
            }
        }
    else:
        speech_config = {
            "multiSpeakerVoiceConfig": {
                "speakerVoiceConfigs": [
                    {
                        "speaker": _display_name(s),
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {"voiceName": get_gemini_voice_for_host(s)}
                        },
                    }
                    for s in speakers
                ]
            }
        }

    return {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": speech_config,
            "temperature": GEMINI_TTS_TEMPERATURE,
            "seed": GEMINI_TTS_SEED,
        },
    }


def _log_speech_config(speech_config: dict) -> None:
    """Print which speech config is being sent, as proof of multi-speaker usage."""
    multi = speech_config.get("multiSpeakerVoiceConfig")
    if multi:
        voices = ", ".join(
            f"{c['speaker']}={c['voiceConfig']['prebuiltVoiceConfig']['voiceName']}"
            for c in multi["speakerVoiceConfigs"]
        )
        print(f"  [gemini-tts] multi-speaker: {voices}")
    else:
        voice = speech_config["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"]
        print(f"  [gemini-tts] single-speaker: {voice}")


def _synthesize_chunk(segments: list[dict], context_tail: str = "") -> tuple[bytes, int]:
    """One generateContent call. Returns (pcm_bytes, sample_rate).

    Retries transient failures (429/5xx/timeouts) twice with backoff.
    """
    api_key = get_gemini_api_key()
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")

    payload = _build_payload(segments, context_tail)
    _log_speech_config(payload["generationConfig"]["speechConfig"])
    prompt_chars = len(payload["contents"][0]["parts"][0]["text"])
    if prompt_chars > MAX_REQUEST_CHARS:
        raise RuntimeError(
            f"Gemini TTS request unexpectedly large ({prompt_chars} chars) — refusing to spend"
        )

    url = GEMINI_TTS_URL.format(model=GEMINI_TTS_MODEL)
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    for attempt in range(3):
        try:
            if attempt > 0:
                # The pinned seed makes generation deterministic, so a no-audio
                # dud (finishReason OTHER) reproduces byte-for-byte on retry
                # unless the seed changes too — observed 2026-07-28: 3/3
                # attempts returned the identical response (same token count).
                # Attempt 0 keeps the configured seed for normal-case voice
                # consistency; only retries perturb it.
                payload["generationConfig"]["seed"] = GEMINI_TTS_SEED + attempt

            # A ~8.5k-char TTS request renders in well under two minutes; a longer
            # wait means the preview model is hanging server-side (observed: ~5 min
            # stalls ended by Google closing the connection), so fail fast and retry
            # instead of holding the runner.
            resp = requests.post(
                url, headers=headers, json=payload, timeout=(REQUEST_CONNECT_TIMEOUT, REQUEST_READ_TIMEOUT)
            )
            if resp.status_code in (429, 500, 502, 503, 504):
                # Keep enough of the body to include error.details — a 429's
                # QuotaFailure names the exceeded quota and its limit, which is
                # the whole diagnosis; 300 chars cut it off exactly there.
                raise RuntimeError(f"Gemini TTS HTTP {resp.status_code}: {resp.text[:ERROR_BODY_CHARS]}")
            resp.raise_for_status()

            data = resp.json()
            usage = data.get("usageMetadata", {})
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            print(
                f"  [api] {ts} service=gemini-tts chars={prompt_chars} "
                f"total_tokens={usage.get('totalTokenCount', 0)}"
            )

            # A 200 with no inlineData (finishReason OTHER) is a known transient
            # server defect of the Gemini TTS models — retryable like a 5xx.
            try:
                part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
            except (KeyError, IndexError) as e:
                raise RuntimeError(
                    f"Gemini TTS response had no audio: {str(data)[:300]}"
                ) from e

            mime = part.get("mimeType", "")
            rate_match = re.search(r"rate=(\d+)", mime)
            sample_rate = int(rate_match.group(1)) if rate_match else DEFAULT_SAMPLE_RATE
            pcm = base64.b64decode(part["data"])

            duration = _duration_ratio(pcm, sample_rate, segments)
            if duration is not None and duration[0] < SEVERE_TRUNCATION_RATIO:
                ratio, words = duration
                raise RuntimeError(
                    f"Gemini TTS severely truncated audio: {words} words expected "
                    f"~{words * EXPECTED_MS_PER_WORD // 1000}s, got "
                    f"{len(pcm) / SAMPLE_WIDTH_BYTES / sample_rate:.0f}s ({ratio:.0%})"
                )
            return pcm, sample_rate
        except (requests.RequestException, RuntimeError) as e:
            if attempt < 2:
                delay = 5 * (2 ** attempt)
                print(f"  ⚠️  Gemini TTS retrying in {delay}s (attempt {attempt + 1}/2): {e}")
                time.sleep(delay)
            else:
                raise
    raise AssertionError("unreachable")  # pragma: no cover


def _duration_ratio(pcm: bytes, sample_rate: int, segments: list[dict]) -> tuple[float, int] | None:
    """(actual/expected duration ratio, word count), or None when too few words to judge."""
    words = sum(len(re.findall(r"\b\w+\b", seg["text"])) for seg in segments)
    if words < 10:
        return None
    actual_ms = len(pcm) / SAMPLE_WIDTH_BYTES / sample_rate * 1000
    expected_ms = words * EXPECTED_MS_PER_WORD
    return actual_ms / expected_ms, words


def _duration_check(pcm: bytes, sample_rate: int, segments: list[dict]) -> None:
    """Warn when audio is far shorter than the word count predicts (dropped text)."""
    duration = _duration_ratio(pcm, sample_rate, segments)
    if duration is None:
        return
    ratio, words = duration
    if ratio < 0.80:
        expected_ms = words * EXPECTED_MS_PER_WORD
        actual_ms = ratio * expected_ms
        print(
            f"  ⚠️  Gemini TTS duration check: expected ~{expected_ms // 1000:.0f}s "
            f"for {words} words, got {actual_ms // 1000:.0f}s ({ratio:.0%}) — possible omission"
        )


def generate_gemini_tts_for_section(
    segments: list[dict], output_file: str | Path, context_tail: str = ""
) -> str:
    """High-level entry: transcript build → synthesize → write WAV to output_file.

    Handles transcript character-limit chunking automatically; chunks are
    concatenated with a short silence between them. `context_tail` carries
    already-spoken text from the previous section into the first chunk here,
    so delivery continues rather than resampling cold; each subsequent chunk
    gets the previous chunk's tail the same way. Returns this section's own
    trailing transcript text for the caller to pass into the *next* section.
    """
    chunks = _split_segments_by_char_limit(segments, limit=TRANSCRIPT_CHAR_LIMIT)

    pcm_parts: list[bytes] = []
    sample_rate = DEFAULT_SAMPLE_RATE
    tail = context_tail
    for chunk in chunks:
        pcm, sample_rate = _synthesize_chunk(chunk, tail)
        _duration_check(pcm, sample_rate, chunk)
        pcm_parts.append(pcm)
        tail = build_transcript(chunk)[-CONTEXT_TAIL_CHARS:]

    gap = b"\x00" * int(sample_rate * SAMPLE_WIDTH_BYTES * INTER_CHUNK_GAP_MS / 1000)
    audio = gap.join(pcm_parts)

    with wave.open(str(output_file), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(SAMPLE_WIDTH_BYTES)
        wav.setframerate(sample_rate)
        wav.writeframes(audio)

    return tail
