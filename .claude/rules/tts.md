---
paths:
  - "podcast_generator.py"
  - "gemini_tts.py"
  - "tests/test_gemini_tts.py"
  - "tests/test_audio_assembly.py"
  - "tests/test_tts_duration_retry.py"
  - "config/pronunciations.json"
  - ".github/workflows/tts-eval.yml"
---
<!-- Moved verbatim from CLAUDE.md on 2026-11-02. Claude Code loads this file when a
     matching path is read or edited. Keep each rule to one or two lines; the story goes
     in docs/decisions/. -->

### TTS — see [docs/decisions/openai-tts.md](../../docs/decisions/openai-tts.md)

**OpenAI `tts-1` is the nightly provider**: `nova` (Riley) + `echo` (Casey), per-turn, in parallel. `OPENAI_TTS_MODEL` selects the model. The steerable `gpt-4o-mini-tts` was tried and reverted: no `speed`, and the acoustic scene resampled mid-turn.
- `_SPEECH_RATE_FITS` is keyed by model. Refit it from the transcript sidecars, never by assumption.
- Every take is checked for duration (`_expected_speech_ms`, retry below 0.80) and amplitude (`_is_silent_take`). **A turn that won't render is cut, never shipped as silence**, and the cut is `degrade()`d.
- **Roundup grouping** (`turn_gap_ms`): a reaction sits closer to the story it answers than to the next one — reactions capped at `NEWS_REACTION_GAP_MAX_MS`, story intros floored at `NEWS_STORY_GAP_MS` (higher when the same voice continues). A tag only raises a story break.
- **Never filter turns by length**: the prompt asks for "Right." / "How so?" backchannels. Chunks of one turn join with `TTS_CHUNK_GAP_MS`, and never split inside an initialism.
- Credits name every provider that actually rendered audio (`_compose_tts_credit`). `get_active_tts_provider()` is the routing answer and must not be used for a credit.

**Gemini multi-speaker TTS is parked** — [docs/decisions/gemini-tts.md](../../docs/decisions/gemini-tts.md) holds its exit criterion and 45 KB of history.
- The code, tests and TTS Eval workflow stay, and `tts_provider=gemini` still dispatches.
- Don't tune, refit or extend it while parked.
- **Never put prior dialogue into a TTS prompt.**

**Nation and community names** (`config/pronunciations.json`): respellings follow the BC government's pronunciation guide (built with the nations), never a guess; the community is the final authority. A respelling derived from the guide's entries for the same language (Tl'esqox, from Tl'etinqox) is the producer's call and is recorded as derived. A name with Indigenous orthography (ŝ ś ʔ ū, a 7 after a letter, an apostrophe inside a capitalised word) left after the table is a `script/pronunciation` degrade (`unaliased_indigenous_names`).
