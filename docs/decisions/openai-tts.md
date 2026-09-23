# OpenAI TTS: the nightly provider

*Moved verbatim from CLAUDE.md on 2026-09-23. CLAUDE.md keeps the standing rules; this file keeps the incidents and the reasoning behind them. Read it before changing the code it describes.*

## TTS Providers

**OpenAI (default):** `nova` (Riley) + `echo` (Casey), per-segment synthesis, parallel rendering. Each segment is checked against `_expected_speech_ms` (`369 ms/word − 642 ms`, speed-normalised — fitted to the 688 segments of the ten episodes rendered 2026-08-13..22, whose transcript sidecars carry each segment's real duration) and re-synthesized once below 0.80 of it. **Refit those constants against the sidecars rather than assuming a rate:** the flat 400 ms/word they replace described nothing the show has produced and re-rendered ~14 complete segments a night, each retry landing within 2% of the take it was doubting.

`OPENAI_TTS_MODEL` selects the model, defaulting to **`tts-1`**. The legacy pair
(`tts-1`, `tts-1-hd`) honours the per-host `speed` multiplier from `hosts.json`;
the steerable models (`gpt-4o-mini-tts`) take an `instructions` string instead,
which is what `hosts.json`'s long-dormant `voice_instructions` was written for —
authored, wired through `get_voice_instructions_for_host`, imported, and never
called, because `tts-1` has no parameter to send it to. `_openai_speech_request`
owns that split so the render path and `evaluate_tts.py` build the same request.

### Why the default came back to tts-1

The steerable model was the default for three episodes (2026-08-23..25) and was
reverted. The direction it buys is real; what it costs is not recoverable by
better wording.

- **`speed` is not supported there** (accepted, ignored), so Casey lost his 1.1x.
  Paired against the script's turns, the sidecars put him at **369 ms/word
  against 320 on tts-1** — 15% slower than the show has been since launch, and
  for the first time slower than Riley, inverting the pace contrast the deadpan
  read depends on. Restoring it would need ffmpeg `atempo` after synthesis.
- **The acoustic scene is sampled per request** — mic distance, room tone,
  register. `TTS_SEGMENT_MAX_CHARS` is 500, so a 30-second turn is 2–4
  independent calls and the scene can change *inside one turn*: the "distant,
  disjointed" complaint that ended the trial. A per-call sample is not made
  deterministic by instructions text, which is why this is a revert and not a
  prompt fix.

**Before trying a steerable model again**, one of those has to be untrue: an
acoustic scene that holds across calls, or a turn that fits in one call. Audition
it with `python evaluate_tts.py --section deep_dive --skip-gemini`,
which renders the pipeline's real request — never a nightly run.

**`_SPEECH_RATE_FITS` is keyed by model**, because a speech rate is a property of
the model. `tts-1`'s row is the solid one (688 segments, ten episodes);
`gpt-4o-mini-tts` carries a provisional row measured off the three episodes of
the trial. A model with no row borrows tts-1's and `_speech_rate_fit` raises
`render/borrowed-speech-rate` once per run, so the report says the word-omission
check is uncalibrated rather than implying it passed. Fit a new row the same way
— pair `podcasts/video_timeline_*.json` turn durations against the script's turns
in order and refit `ms/word` and intercept. Until then expect the retry rate to be
wrong in one direction or the other; a mis-sized floor costs a re-render, which is
why borrowing beats skipping.

### Per-take checksums

Every take is checked twice before it joins the mix, because a bad take is not
distinguishable from a good one by the fact that the API returned 200.

- **Duration** (`generate_tts_for_segment`): a ratio under 0.80 against `_expected_speech_ms`
  means words were dropped. Retry once, keep the longer take.
- **Amplitude** (`_is_silent_take`): a take can come back well-formed, the right length for
  its text, and **completely silent** — 2026-08-16 shipped 27 s of digital silence in the
  middle of the deep dive. Nothing downstream caught it: `trim_tts_silence` returns an
  entirely silent clip untouched at full length *by design*, `normalize_segment` leaves zeros
  as zeros, and the duration ratio was ~1.0 because the length was right. Peak level is the
  only signal that separates the two. Retry once; two silent takes raise `SilentTakeError`.

**A turn that will not render is cut, never shipped as silence** — keeping it produces dead
air of exactly the same length, which is worse to listen to and invisible in every duration
the pipeline records. The caller drops the chunk and calls `degrade("render/silent-take")`,
so the words are missing from the audio but the run report names them. The music overlap is
tracked as a `pending_overlap_ms` rather than keyed on `i == 0`, so dropping the turn that
would have opened a section hands the overlap to whichever turn actually starts it instead
of leaving the music to fade out into a gap.

The same check runs on whole-section (Gemini) renders, where it raises into the
existing per-section OpenAI fallback — a silent section is this failure minutes wide.
