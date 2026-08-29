#!/usr/bin/env python3
"""
Gemini multi-speaker TTS integration (NotebookLM-style dialog rendering).

One generateContent call per script section renders the whole two-host
conversation with coherent cross-speaker prosody, replacing per-segment
synthesis + manual gap stitching. A style prompt from config/prompts.json
controls delivery, and whitelisted inline [tag] stage directions in the
script text are performed rather than read aloud.

Plain REST via requests — no SDK dependency.

Requires:
  GEMINI_API_KEY   — Google AI Studio key
Optional:
  GEMINI_TTS_MODEL — default gemini-3.1-flash-tts-preview
"""

import base64
import os
import re
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import requests

# ponytail: reuse azure_tts's segment splitter instead of writing a second one
from azure_tts import PRONUNCIATION_DICT, _split_segments_by_char_limit
from config_loader import (
    get_gemini_audio_profile_for_host,
    get_gemini_voice_for_host,
    load_hosts_config,
    load_prompts_config,
    strip_stage_directions,
)

# `or` (not a getenv default) so a present-but-empty env var — e.g. an unset
# workflow secret expanding to "" — still falls back to the default model.
# .strip() guards against a trailing-whitespace secret/variable value, which
# otherwise lands in the URL as a literal "%20" and Gemini 400s on it.
GEMINI_TTS_MODEL = (os.getenv("GEMINI_TTS_MODEL") or "").strip() or "gemini-3.1-flash-tts-preview"

# Second Gemini TTS model, tried after the primary has failed several times.
# Same prebuilt voice names and the same multi-speaker API, so falling here
# keeps Riley and Casey sounding like themselves — the episode loses a model,
# not its voice.
#
# It is the 2.5 flash model the show rendered on for months, deliberately: the
# primary is now a preview model this repo has never run a night on, so the
# fallback's job is to be the known-good one. A wrong model name, a preview
# withdrawn, or a 3.1 outage then costs the episode nothing at all — the canary
# probes the primary, fails, probes this, and pins it for the show. Pro TTS used
# to sit here; it costs more than flash and was never reached on a night flash
# could serve, so it is now an env-var away (GEMINI_TTS_FALLBACK_MODEL) rather
# than the default second spend.
GEMINI_TTS_FALLBACK_MODEL = (
    os.getenv("GEMINI_TTS_FALLBACK_MODEL") or ""
).strip() or "gemini-2.5-flash-preview-tts"

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

# Continuity note for a chunk/section that is not the episode's first, so the
# next call opens mid-flow instead of resampling delivery from a cold start.
#
# This used to be the previous section's *verbatim* transcript tail (400 chars),
# labelled "CONTEXT — already spoken immediately before this, do not repeat".
# Handing a TTS model a block of real dialogue and asking it not to say the
# words is a request it honours only most of the time: on 2026-08-17 the welcome
# section read the whole cold open aloud before its own first line, so the
# episode opened with the teaser twice — 92.8 s of audio for a 969-char
# transcript that six prior Gemini episodes had rendered in 65–76 s, an excess
# matching the 25.5 s cold open. Same prompt shape, same pro model, and six of
# seven days were clean, so there is no wording that makes this safe: the fix is
# to stop putting speakable text in the prompt that is not meant to be spoken.
# Never reintroduce verbatim prior dialogue here.
CONTINUATION_NOTE = (
    "This continues a conversation already in progress. Open mid-flow at the "
    "same energy as an episode already underway, with no fresh introduction."
)

# Prompt scaffolding. Gemini's own guidance for multi-speaker synthesis is to
# fence the spoken text off from everything around it: the direction goes above
# a hard delimiter and only what follows it is speech. Everything this pipeline
# has been bitten by on this endpoint is a boundary failure — the cold open read
# aloud twice (see CONTINUATION_NOTE), a stage direction spoken as dialogue — so
# the request now says where the transcript starts rather than leaving the model
# to infer it from a colon at the end of a sentence.
AUDIO_PROFILE_HEADER = "### AUDIO PROFILE"
PERFORMANCE_NOTES_HEADER = "### PERFORMANCE NOTES"
TRANSCRIPT_MARKER = "#### TRANSCRIPT"

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

# (connect, read) timeouts in seconds, vs the 15 min a hung server cost with the
# old 600 s read timeout (2026-07-27 run: three ~5-min stalls before
# RemoteDisconnected). The overall ladder is bounded by SECTION_BUDGET_S below.
REQUEST_CONNECT_TIMEOUT = 15
# A read timeout is only honest if it is scaled to the size of the request.
# A flat 120 s was the same leash for a 350-char cold open and an 8 500-char
# deep-dive chunk, and on 2026-08-28 that is what cost the episode: three
# unanswered requests of 354, 975 and 975 chars each burned the full 120 s and
# returned nothing, which spent the 420 s section budget in two attempts. The
# successful take that night answered a 1 173-char request in ~16 s, and the
# canary in well under 30 — a call that has not answered in several times its
# own expected time is not going to.
#
# Generation time tracks the length of the audio produced, so the transcript's
# character count is the scale. Constants are PROVISIONAL — fitted to one
# measured take plus the 8 500-char chunk ceiling — and deliberately generous
# (~3x observed). Refit them from the `latency=` field now logged on every call:
# pair it against `chars=` across a few episodes, the same way _SPEECH_RATE_FITS
# was fitted from the transcript sidecars. Until then expect the floor to be
# loose rather than tight, which costs a wasted wait rather than a lost take.
READ_TIMEOUT_MIN_S = int(os.getenv("GEMINI_TTS_READ_TIMEOUT_MIN_S", "45"))
READ_TIMEOUT_MAX_S = int(os.getenv("GEMINI_TTS_READ_TIMEOUT_MAX_S", "120"))
READ_TIMEOUT_MS_PER_CHAR = float(os.getenv("GEMINI_TTS_READ_TIMEOUT_MS_PER_CHAR", "40"))


def _transcript_chars(segments: list[dict]) -> int:
    """Spoken characters in *segments* — the scale generation time tracks."""
    return sum(len(seg["text"]) for seg in segments)


def _read_timeout_for(segments: list[dict]) -> int:
    """Read timeout for a request carrying *segments*, clamped to [MIN, MAX].

    The clamp is what makes this safe to shorten: the ceiling is the old flat
    value, so the largest chunks wait exactly as long as they always have, and
    only the small requests — where the ladder was wasting whole minutes on
    silence — get a shorter leash.
    """
    scaled = _transcript_chars(segments) * READ_TIMEOUT_MS_PER_CHAR / 1000
    return int(min(READ_TIMEOUT_MAX_S, max(READ_TIMEOUT_MIN_S, scaled)))

# Chars of an HTTP error body to surface. Gemini's structured error.details
# (e.g. the QuotaFailure block naming the exceeded quota) sits past the 300-char
# mark that logs used to truncate at.
ERROR_BODY_CHARS = 2_000


class SpendCapError(RuntimeError):
    """Gemini refused because the project is out of money for the month.

    A subclass of RuntimeError so every existing handler still catches it; the
    type is what lets the ladder and the canary tell a wall from a throttle.
    """


# Gemini answers a spent spend-cap and an ordinary per-minute throttle with the
# same 429 RESOURCE_EXHAUSTED, so the status code cannot separate them and the
# wording has to. Note the asymmetry with _carries_no_shape_verdict's treatment
# of a bare 429: there, refusing to re-ask a throttle costs an episode its
# voices, so a 429 is re-asked. Here the project is out of money until the month
# rolls over or a human raises the cap, and every later probe gets the same
# answer — on 2026-08-29 that was four canary probes across two models, each
# refused in 0.2s, and it would have been four more every night to Sept 1.
_SPEND_CAP_RE = re.compile(
    r"spend(?:ing)? cap"
    r"|billing account .{0,40}(?:disabled|closed|not active)"
    r"|exceeded your current quota.{0,80}billing",
    re.IGNORECASE,
)


def _is_spend_cap(error: Exception) -> bool:
    """True when Gemini refused for want of money rather than want of capacity."""
    return bool(_SPEND_CAP_RE.search(str(error)))


class _Rung(NamedTuple):
    """One attempt in the retry ladder: how long to wait, and what to ask for."""

    backoff_s: int
    keep_context: bool
    keep_style: bool
    keep_cues: bool
    fallback_model: bool


# The ladder climbed on failure. Two things were wrong with the flat 5 s/10 s,
# same-request-three-times retry it replaces:
#
# 1. The backoff was an order of magnitude too short. Every observed failure
#    persisted across all three attempts spread over one to three minutes —
#    these are capacity windows, not blips (2026-08-04 welcome: 500, 500, then
#    a no-audio dud; 2026-08-07 preamble: 500 then two read timeouts). The
#    render step has a 40-minute budget and was using six to nine.
#
# 2. Only the seed varied, and `finishReason: OTHER` is not a sampling problem.
#    It comes back with promptTokenCount == totalTokenCount — the request was
#    accepted and tokenized and the model produced nothing, which is a
#    rejection of what was asked, not of when it was asked. Reseeding
#    reproduced it exactly (2026-08-05: two attempts, identical 272 tokens).
#
# So each rung changes the *shape* of the request, shedding the least
# load-bearing text first. Ordering is deliberately consistency-first: the
# prebuilt voices are pinned by speechConfig on every rung, so what degrades is
# delivery nuance, never who the hosts sound like. The fallback model with the
# full prompt (rung 3) is therefore tried *before* the primary model with a
# bare transcript (rung 4) — a different model reading the real direction stays
# closer to the show than the right model reading stripped text.
RETRY_LADDER: tuple[_Rung, ...] = (
    #     backoff  context  style  cues   fallback_model
    _Rung(0,       True,    True,  True,  False),
    _Rung(15,      False,   True,  True,  False),
    _Rung(45,      False,   False, True,  False),
    _Rung(90,      True,    True,  True,  True),
    _Rung(90,      False,   False, False, True),
)

# Wall-clock ceiling for one chunk's whole ladder, backoffs included. Without it
# five rungs of read timeouts plus backoff could hold a section for ~15 min and
# a six-section episode would blow the 40-minute render step — the class of
# failure this repo has been bitten by before. A new attempt is only started if
# it fits.
SECTION_BUDGET_S = float(os.getenv("GEMINI_TTS_SECTION_BUDGET_S", "420"))

# Optional absolute deadline for all Gemini work in a run, set by the caller via
# set_render_deadline(). SECTION_BUDGET_S bounds one chunk; this bounds the sum,
# so a provider that dies *after* the canary passed cannot eat the whole render
# step one section at a time.
_render_deadline: float | None = None

# Model every request uses, once the canary has established which one answers.
# None means "follow the ladder's own primary/fallback choice".
_model_override: str | None = None

# Retry rungs that actually produced this run's audio, for the caller to report.
# gemini_tts cannot import podcast_generator.degrade() without a circular
# import, so degradations are collected here and drained by the caller. A silent
# fallback is the failure mode the run report exists to prevent.
_degradations: list[str] = []

# Canary: one tiny synthesis that decides, before any audio exists, whether this
# episode is a Gemini episode at all.
#
# Its leash is the render's own floor, not a shorter one: the canary must never
# be stricter than the render it vouches for. At 30 s it could fail a
# slow-but-alive endpoint that the render would have waited out at 45, and the
# cost of that false negative is the whole episode's voices.
CANARY_READ_TIMEOUT = READ_TIMEOUT_MIN_S
CANARY_RETRY_DELAY_S = 10
# Attempts per candidate model, but only against a request that went
# unanswered — see _canary_probe.
CANARY_ATTEMPTS = 2
# Two speakers, because the probe has to ask the question the render asks. A
# single-speaker probe exercises singleSpeakerVoiceConfig and a multi-speaker
# section exercises multiSpeakerVoiceConfig — different request shapes against
# an endpoint whose failures are shape-sensitive. On 2026-08-28 the one-turn
# canary passed and the same model then failed three multi-speaker sections in
# a row, so the episode was pinned to a provider that could not render it.
# Still under the 10 words _duration_ratio needs before it will judge a clip,
# so the truncation guard cannot report a healthy provider as dead.
CANARY_SEGMENTS = [
    {"speaker": "riley", "text": "Level check, one two.", "gap_ms": None},
    {"speaker": "casey", "text": "Two one, check.", "gap_ms": None},
]


def get_gemini_api_key() -> str | None:
    """Return the Gemini API key, or None if not configured."""
    return os.environ.get("GEMINI_API_KEY") or None


def _display_name(host_key: str) -> str:
    """Speaker label used in the transcript and voice config (e.g. 'Riley')."""
    return load_hosts_config()[host_key].get("name", host_key.title())


def _style_prompt() -> str:
    return load_prompts_config().get("gemini_tts", {}).get("style_prompt", "")


def _tag_instruction() -> str:
    """The never-speak-a-tag rule, kept out of the sheddable style prompt.

    It used to be the last sentence of style_prompt, which meant the rung that
    dropped the style also dropped the only thing telling the model that
    `[thoughtfully]` is direction rather than dialogue — while still sending the
    tags. The rule now travels with the tags: emitted whenever the rung keeps
    cues, absent when it strips them.
    """
    return (load_prompts_config().get("gemini_tts", {})
            .get("stage_directions", {}).get("tag_instruction", ""))


def _audio_profile_block(speakers: list[str]) -> str:
    """`### AUDIO PROFILE` — one line per speaker, naming who the voice is.

    Voices are pinned by speechConfig, so this is not what selects them; it is
    what tells the model how the pinned voice is meant to carry a line.
    """
    lines = [
        f"{_display_name(s)}: {get_gemini_audio_profile_for_host(s)}".rstrip(": ")
        for s in speakers
    ]
    return AUDIO_PROFILE_HEADER + "\n" + "\n".join(lines)


def _performance_notes_block(
    speakers: list[str], continuing: bool, rung: _Rung
) -> str:
    """`### PERFORMANCE NOTES` — the show's direction plus this call's rules."""
    names = " and ".join(_display_name(s) for s in speakers)
    # A one-turn section (the cold open is usually a single host) is not a
    # conversation, and asking for one between a single named person is a
    # malformed request the model has to interpret. This is also the call that
    # failed most often in the week of 2026-08-01.
    if len(speakers) == 1:
        notes = [f"One voice throughout, read aloud by {names}."]
    else:
        notes = [
            f"A conversation between {names}, alternating exactly as the "
            "speaker labels below set it out."
        ]
    if rung.keep_style and (style := _style_prompt()):
        # The style prompt carries its own leading dashes, so it lands as
        # sibling bullets rather than one wrapped paragraph.
        notes.extend(style.split("\n"))
    if rung.keep_cues and (tags := _tag_instruction()):
        notes.append(tags)
    # A directive, never quotable dialogue — see CONTINUATION_NOTE.
    if continuing and rung.keep_context:
        notes.append(CONTINUATION_NOTE)
    bullets = [n if n.startswith("- ") else f"- {n}" for n in notes]
    return PERFORMANCE_NOTES_HEADER + "\n" + "\n".join(bullets)


def apply_pronunciation(text: str) -> str:
    """Substitute Cariboo place-name phonetic aliases (plain text, no SSML)."""
    for word, alias in PRONUNCIATION_DICT.items():
        text = text.replace(word, alias)
    return text


def build_transcript(segments: list[dict], keep_cues: bool = True) -> str:
    """Build the speaker-labeled transcript for one request.

    keep_cues=False strips the whitelisted `[thoughtfully]`-style tags, the way
    the OpenAI and Azure paths always do — a retry rung for when Gemini appears
    to be rejecting the request rather than failing to serve it.
    """
    lines = []
    for seg in segments:
        text = seg["text"] if keep_cues else strip_stage_directions(seg["text"])
        lines.append(f"{_display_name(seg['speaker'])}: {apply_pronunciation(text)}")
    return "\n".join(lines)


def _build_payload(
    segments: list[dict],
    continuing: bool = False,
    rung: _Rung = RETRY_LADDER[0],
    seed: int | None = None,
) -> dict:
    """Build the generateContent request body for a section's segments.

    *rung* selects how much of the prompt to include; the first rung is the
    full-quality request and is what every successful render uses.
    """
    speakers = list(dict.fromkeys(seg["speaker"] for seg in segments))
    if len(speakers) > 2:
        raise ValueError(f"Gemini multi-speaker TTS supports 2 speakers, got {speakers}")

    transcript = build_transcript(segments, keep_cues=rung.keep_cues)
    blocks = []
    if rung.keep_style:
        blocks.append(_audio_profile_block(speakers))
    blocks.append(_performance_notes_block(speakers, continuing, rung))
    blocks.append(f"{TRANSCRIPT_MARKER}\n\n{transcript}")
    prompt = "\n\n".join(blocks)

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
            "seed": GEMINI_TTS_SEED if seed is None else seed,
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


def set_render_deadline(seconds: float | None) -> None:
    """Bound all Gemini work from now to *seconds*, or None to lift the bound."""
    global _render_deadline
    _render_deadline = None if seconds is None else time.monotonic() + seconds


def set_model_override(model: str | None) -> None:
    """Pin every request to *model*, or None to follow the ladder's own choice."""
    global _model_override
    _model_override = model


def drain_degradations() -> list[str]:
    """Return and clear the degradations recorded since the last drain."""
    global _degradations
    drained, _degradations = _degradations, []
    return drained


def _model_for(rung: _Rung) -> str:
    """Model this rung should call, honouring an override the canary established."""
    if _model_override:
        return _model_override
    return GEMINI_TTS_FALLBACK_MODEL if rung.fallback_model else GEMINI_TTS_MODEL


def _rung_label(rung: _Rung) -> str:
    """Human-readable description of what a rung sheds, for logs and the report."""
    dropped = [
        name
        for name, kept in (
            ("context", rung.keep_context),
            ("style", rung.keep_style),
            ("cues", rung.keep_cues),
        )
        if not kept
    ]
    return f"model={_model_for(rung)}, dropped={'+'.join(dropped) or 'nothing'}"


def _budget_allows(
    started: float, budget: float, backoff_s: int, read_timeout: int
) -> bool:
    """True when another attempt — its backoff *and* its own cost — still fits.

    Reserving the attempt's read timeout as well as its backoff is what makes
    the ladder's reach honest. Counting the backoff alone let an attempt start
    at 255 s into a 420 s budget and then run to the ceiling, so the "out of
    budget" message named three attempts when the third never had room to
    finish.

    *read_timeout* is this request's own scaled timeout rather than the ceiling:
    reserving 120 s for a request that will be abandoned after 45 under-counts
    what the budget can still afford, which is how a section that had room for
    two more tries was handed back after two.
    """
    now = time.monotonic()
    cost = backoff_s + read_timeout
    if _render_deadline is not None and now + cost >= _render_deadline:
        return False
    return (now - started) + cost <= budget


def _carries_no_shape_verdict(error: Exception) -> bool:
    """True when the failure says nothing about *what* was asked.

    Only one failure on this endpoint is a verdict on the request's shape:
    `finishReason: OTHER`, which comes back tokenized (promptTokenCount ==
    totalTokenCount) having produced no audio — the request was accepted, read,
    and refused. Everything else is the transport or the service:

    - a read timeout or dropped connection never got an answer at all;
    - a 429 or 5xx is the service declining to serve *right now*. Gemini answers
      an ordinary per-minute rate limit with the same 429 RESOURCE_EXHAUSTED it
      uses for a spent quota, and on 2026-08-26 two of three crons had both
      canary candidates rejected outright on a 429 that a wait would very
      likely have cleared.

    Shedding context, style and cues cannot fix any of those, and shedding is
    not free: two dead prompt-shedding rungs cost a full read timeout each and
    pushed the model rungs out of the section budget entirely. So they route to
    a model change (or a plain re-ask), and only a real shape verdict walks the
    prompt-shedding rungs.

    A genuinely spent quota costs one extra probe here before it is believed.
    That is the right side to err on: refusing to re-ask a rate limit spends a
    whole episode's voices to save one tiny call.

    A spend cap is the exception to the 429 rule and is checked first: it is a
    verdict, just not one about the prompt's shape. No rung, no backoff and no
    model on the same project reaches past it.
    """
    if isinstance(error, SpendCapError):
        return False
    if isinstance(error, (requests.Timeout, requests.ConnectionError)):
        return True
    return bool(re.search(r"Gemini TTS HTTP (429|5\d\d)", str(error)))


def _next_model_rung(after: int, failed_model: str) -> int | None:
    """Index of the next rung that would call a different model, or None.

    None means every remaining rung would re-ask the model that just went
    unanswered — either because the ladder has no model change left, or because
    the canary pinned one model for the episode. Both are reasons to hand the
    section back now rather than spend the rest of the budget confirming it.
    """
    for i in range(after + 1, len(RETRY_LADDER)):
        if _model_for(RETRY_LADDER[i]) != failed_model:
            return i
    return None


def _attempt(
    segments: list[dict],
    continuing: bool,
    rung: _Rung,
    seed: int,
    read_timeout: int,
) -> tuple[bytes, int]:
    """One generateContent call for *rung*. Returns (pcm_bytes, sample_rate)."""
    api_key = get_gemini_api_key()
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")

    model = _model_for(rung)
    payload = _build_payload(segments, continuing, rung=rung, seed=seed)
    prompt_chars = len(payload["contents"][0]["parts"][0]["text"])
    _log_speech_config(payload["generationConfig"]["speechConfig"])

    # A ~8.5k-char TTS request renders in well under two minutes; a longer wait
    # means the model is hanging server-side (observed: ~5 min stalls ended by
    # Google closing the connection), so fail fast and let the ladder retry
    # instead of holding the runner.
    call_started = time.monotonic()
    try:
        resp = requests.post(
            GEMINI_TTS_URL.format(model=model),
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=(REQUEST_CONNECT_TIMEOUT, read_timeout),
        )
    except Exception:
        # The one number that says whether a timeout was a hung server or a
        # leash set too short. Without it the read timeout can only ever be
        # guessed at, which is how a flat 120 s survived unexamined.
        print(
            f"  [api] service=gemini-tts model={model} chars={prompt_chars} "
            f"latency={time.monotonic() - call_started:.1f}s "
            f"limit={read_timeout}s outcome=unanswered"
        )
        raise
    elapsed = time.monotonic() - call_started
    if resp.status_code in (429, 500, 502, 503, 504):
        print(
            f"  [api] service=gemini-tts model={model} chars={prompt_chars} "
            f"latency={elapsed:.1f}s limit={read_timeout}s "
            f"outcome=http-{resp.status_code}"
        )
        # Keep enough of the body to include error.details — a 429's
        # QuotaFailure names the exceeded quota and its limit, which is the
        # whole diagnosis; 300 chars cut it off exactly there.
        message = f"Gemini TTS HTTP {resp.status_code}: {resp.text[:ERROR_BODY_CHARS]}"
        # Recognized once, here, where the body is still in hand — so every
        # caller can tell a wall from a throttle by catching a type.
        if _is_spend_cap(message):
            raise SpendCapError(message)
        raise RuntimeError(message)
    resp.raise_for_status()

    data = resp.json()
    usage = data.get("usageMetadata", {})
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(
        f"  [api] {ts} service=gemini-tts model={model} chars={prompt_chars} "
        f"total_tokens={usage.get('totalTokenCount', 0)} "
        f"latency={elapsed:.1f}s limit={read_timeout}s"
    )

    # A 200 with no inlineData (finishReason OTHER) is a known defect of the
    # Gemini TTS models — retryable, though the ladder varies the request shape
    # rather than just re-asking, since it comes back with zero output tokens.
    try:
        part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Gemini TTS response had no audio: {str(data)[:300]}") from e

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


def _synthesize_chunk(
    segments: list[dict],
    continuing: bool = False,
    budget_s: float | None = None,
) -> tuple[bytes, int]:
    """Climb RETRY_LADDER until a rung yields audio. Returns (pcm, sample_rate).

    Raises the last error once the ladder or the time budget is exhausted; the
    caller then falls the section back to another provider.
    """
    if not get_gemini_api_key():
        raise ValueError("GEMINI_API_KEY not set")

    # Checked once, before the ladder, against the largest (full-quality) rung:
    # an oversized request means a parsing bug upstream, not a transient fault,
    # so it must fail fast rather than be retried through minutes of backoff.
    prompt_chars = len(
        _build_payload(segments, continuing)["contents"][0]["parts"][0]["text"]
    )
    if prompt_chars > MAX_REQUEST_CHARS:
        raise RuntimeError(
            f"Gemini TTS request unexpectedly large ({prompt_chars} chars) — refusing to spend"
        )

    budget = SECTION_BUDGET_S if budget_s is None else budget_s
    read_timeout = _read_timeout_for(segments)
    started = time.monotonic()
    last_error: Exception | None = None

    # `attempt` paces the ladder — backoff, seed and the cap on total calls.
    # `rung_index` chooses the request's shape, and only tracks `attempt` while
    # the failures are rejections; a transport failure moves it independently
    # (or not at all), because rewording an unanswered request is not a retry
    # strategy.
    attempt = 0
    rung_index = 0
    while attempt < len(RETRY_LADDER):
        rung = RETRY_LADDER[rung_index]
        if attempt:
            pacing = RETRY_LADDER[attempt].backoff_s
            if not _budget_allows(started, budget, pacing, read_timeout):
                print(
                    f"  ⚠️  Gemini TTS out of time budget "
                    f"— giving the section back to the caller"
                )
                break
            print(
                f"  ⚠️  Gemini TTS retrying in {pacing}s "
                f"(attempt {attempt + 1}/{len(RETRY_LADDER)}, {_rung_label(rung)}): {last_error}"
            )
            time.sleep(pacing)

        try:
            # The pinned seed makes generation deterministic, so re-asking with
            # the same seed reproduces a no-audio dud byte-for-byte (2026-07-28:
            # 3/3 identical responses). Attempt 0 keeps the configured seed for
            # normal-case voice consistency; only retries perturb it.
            pcm, sample_rate = _attempt(
                segments, continuing, rung,
                seed=GEMINI_TTS_SEED + attempt,
                read_timeout=read_timeout,
            )
        except (requests.RequestException, RuntimeError) as e:
            last_error = e
            if isinstance(e, SpendCapError):
                # Every rung and every model rides the same project. Hand the
                # section back now and let the per-section OpenAI fallback take
                # it, rather than spending the budget confirming the cap.
                print("  ⚠️  Gemini TTS project spend cap reached — no rung reaches past it")
                break
            if _carries_no_shape_verdict(e):
                # No verdict on the request: go straight to a rung that changes
                # the model, and when there is none, simply ask the same thing
                # again. What must not happen is shedding context and style —
                # that spent a full read timeout a rung on a shape that was
                # never the problem
                # and left the budget empty before any model rung was reached
                # (every Cariboo Signals episode of August 2026).
                #
                # Re-asking is worth the attempt: the 2026-08-13 probe measured
                # ~53% success per call across all five rungs, so a timeout is a
                # flaky endpoint rather than a dead one, and a second identical
                # ask is close to a coin flip. The budget, not the ladder, is
                # what bounds this.
                failed_model = _model_for(rung)
                nxt = _next_model_rung(rung_index, failed_model)
                if nxt is None:
                    print(f"  ⚠️  Gemini TTS did not serve {failed_model} — asking again unchanged")
                else:
                    print(f"  ⚠️  Gemini TTS did not serve {failed_model} — changing model")
                    rung_index = nxt
            else:
                rung_index = min(rung_index + 1, len(RETRY_LADDER) - 1)
            attempt += 1
            continue

        if attempt:
            _degradations.append(
                f"section synthesized on retry {attempt} ({_rung_label(rung)}) "
                "— delivery may differ from the rest of the episode"
            )
        return pcm, sample_rate

    raise last_error or RuntimeError("Gemini TTS exhausted its retry ladder")


def _canary_probe(model: str) -> bool:
    """Whether *model* answers one tiny synthesis, re-asking an unanswered one.

    The ladder's own rule (see _carries_no_shape_verdict) applied to the probe: a
    rejection is a verdict on the request and re-asking it is waste, but a read
    timeout is a verdict on nothing. Every canary failure of the week of
    2026-08-17 was a read timeout, and the 2026-08-13 probe measured 8 of 15
    identical calls answering — so the old single attempt was a coin flip whose
    losing side moved a whole episode onto OpenAI's voices. Two flips cost at
    most one extra tiny synthesis and CANARY_RETRY_DELAY_S against a 1500 s
    render budget.
    """
    for attempt in range(CANARY_ATTEMPTS):
        if attempt:
            time.sleep(CANARY_RETRY_DELAY_S)
        try:
            _attempt(
                CANARY_SEGMENTS, "", RETRY_LADDER[0],
                seed=GEMINI_TTS_SEED,
                read_timeout=CANARY_READ_TIMEOUT,
            )
            return True
        except SpendCapError:
            # Propagates past the remaining candidates: the cap is a property of
            # the project, not of the model, so probing the fallback asks the
            # same question of the same wall.
            raise
        except Exception as e:
            print(f"  ⚠️  Gemini TTS canary failed on {model}: {e}")
            if not _carries_no_shape_verdict(e):
                return False
    return False


def canary() -> str | None:
    """Model that answers a tiny synthesis right now, or None if Gemini is unusable.

    Whether an episode is a Gemini episode is decided once, here, before any
    audio exists. The per-section provider fallback it front-runs is what
    shipped three of seven episodes in the week of 2026-08-01 with a Gemini cold
    open and an OpenAI show — a genuinely mixed-voice episode, which is a worse
    outcome for the listener than never reaching for Gemini at all. Deciding up
    front makes that unrepresentable.

    Trying the fallback model too means a flash-only outage costs a model rather
    than the Gemini sound, and the model that answers is pinned for the whole
    episode so the voice cannot change mid-show.
    """
    if not get_gemini_api_key():
        return None

    # dict.fromkeys dedupes while keeping order, for when both env vars name the
    # same model and there is no second thing to try.
    candidates = list(dict.fromkeys((GEMINI_TTS_MODEL, GEMINI_TTS_FALLBACK_MODEL)))
    # Say which models this run will try, before trying them. The failure
    # messages name a model each, but nothing said what the run was configured
    # with, so a wrong or withdrawn model name looked exactly like an outage.
    print(f"  Gemini TTS candidates: {' then '.join(candidates)}")

    for i, model in enumerate(candidates):
        if i:
            time.sleep(CANARY_RETRY_DELAY_S)
        # Pin the candidate for the probe itself, so _attempt calls the model
        # being tested rather than the ladder's default.
        set_model_override(model)
        try:
            passed = _canary_probe(model)
        except SpendCapError as e:
            # The cap belongs to the project, so the remaining candidates are
            # behind the same wall and CANARY_ATTEMPTS buys nothing. One probe
            # answers for the whole run instead of four (2026-08-29).
            print(f"  ⚠️  Gemini TTS spend cap reached on {model}: {e}")
            print("  ⏭️  Skipping remaining Gemini candidates — the cap is project-wide")
            _degradations.append(
                "Gemini project spend cap reached — no model on this project can "
                "render until the cap is raised or the month rolls over"
            )
            set_model_override(None)
            return None
        if not passed:
            continue

        # Passing on the primary leaves the override clear, so a section that
        # struggles later can still climb to the fallback model — a model change
        # mid-episode keeps the same prebuilt voices, which is a far smaller
        # break than dropping the show onto OpenAI's. Passing only on the
        # fallback means the primary is down for this run, so pin the fallback
        # and stop spending attempts on a model already known to be failing.
        set_model_override(None if i == 0 else model)
        print(f"  ✅ Gemini TTS canary passed on {model}")
        return model

    set_model_override(None)
    return None


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
    segments: list[dict], output_file: str | Path, continuing: bool = False
) -> bool:
    """High-level entry: transcript build → synthesize → write WAV to output_file.

    Handles transcript character-limit chunking automatically; chunks are
    concatenated with a short silence between them. `continuing` tells this call
    that speech has already aired, so delivery opens mid-flow rather than
    resampling cold; every chunk after the first is a continuation by
    definition. Returns True for the caller to pass into the *next* section.

    It is deliberately a flag and not the previous section's text: carrying the
    text is what made the 2026-08-17 welcome read the cold open aloud (see
    CONTINUATION_NOTE).
    """
    chunks = _split_segments_by_char_limit(segments, limit=TRANSCRIPT_CHAR_LIMIT)

    pcm_parts: list[bytes] = []
    sample_rate = DEFAULT_SAMPLE_RATE
    for chunk in chunks:
        pcm, sample_rate = _synthesize_chunk(chunk, continuing)
        _duration_check(pcm, sample_rate, chunk)
        pcm_parts.append(pcm)
        continuing = True

    gap = b"\x00" * int(sample_rate * SAMPLE_WIDTH_BYTES * INTER_CHUNK_GAP_MS / 1000)
    audio = gap.join(pcm_parts)

    with wave.open(str(output_file), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(SAMPLE_WIDTH_BYTES)
        wav.setframerate(sample_rate)
        wav.writeframes(audio)

    return True
