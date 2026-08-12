# Cariboo Signals Podcast - Roadmap

## Current State
- Daily generation at 5 AM Pacific via GitHub Actions
- Fetches scored articles from RSS feed system
- Two AI hosts: Riley (tech systems) and Casey (community development)
- 7 rotating weekly themes
- Music interludes via Sumo AI theme (intro/interval/outro)
- OpenAI TTS for host voices (nova, echo)

## Working Well
- Music interludes integrated via pydub
- Script polishing pass reduces repetition between segments
- Configuration externalized to config/ directory (hosts, themes, credits, interests)
- Three rotation layers: daily theme, super-cycle focus, and a weekly anchor question all seven
  deep dives circle (`weekly_anchor.py`; preview with `python weekly_anchor.py --preview 12`)
- Deduplication against last 7 days of episodes
- Episode memory for continuity (21-day window)
- Citations system tracks sources per episode
- Indigenous territory acknowledgment in descriptions
- RSS feed with proper XML escaping

## Short-term
- [ ] **Run the Gemini prompt-shape probe** — `TTS Eval` workflow with `probe_gemini: true`
      (or `python evaluate_tts.py --probe-gemini`). The retry ladder's rung ordering is
      currently an untested hypothesis: it assumes `finishReason: OTHER` is a rejection of
      the style prompt / cues / context tail. If rung 0 fails while a later rung passes,
      that names the culprit and the fix is a one-line config change worth more than the
      whole ladder. If every rung fails equally it's an outage or a quota wall instead.
- [ ] Confirm which Gemini quota tier `GEMINI_API_KEY` is on. Free-tier AI Studio TTS RPM is
      very low and back-to-back section calls could be throttled and returned as 500s, which
      would make all the retry work treat a quota wall as flakiness.
- [ ] Audit the TTS credit across the stage boundary. `run_publish_stage` is a separate
      process where `_tts_providers_rendered` is empty, so `get_tts_credit()` falls back to
      the env flag — check it cannot clobber the mixed credit the render stage wrote
      (`"Gemini TTS and OpenAI TTS"`) with a flag-derived guess.
- [ ] Submit to Apple Podcasts (see [docs/submit-apple-podcasts.md](docs/submit-apple-podcasts.md))
  - [ ] Upgrade cover art to 1400x1400+ pixels (Apple minimum)
  - [ ] Replace placeholder email in config/podcast.json
  - [ ] Submit RSS feed at podcastsconnect.apple.com
- [ ] Submit to Spotify, Amazon Music, Pocket Casts
- [ ] Clean up backup and old generator scripts from root directory
- [ ] Reduce technical jargon for general audiences
- [ ] Theme-based filtering on website index page

## Medium-term
- [ ] **Review the first LLM-generated anchor batch.** The seeded pool covers 11 weeks
      (through 2026-W44); `top_up_pool()` writes the first generated questions to
      `podcasts/weekly_anchor_state.json` after that. Read them before they air — the
      no-repeat guard enforces a fresh `dimension` but nothing yet checks that a generated
      question is actually answerable through all seven themes.
- [ ] Consider whether the anchor should feed `format_debate_memory_for_prompt`. The id is
      already recorded on each debate memory entry; nothing reads it yet, and the must-differ
      bucket keys on (theme, focus) only.
- [ ] **Two-phase render: split synthesis from assembly.** Today synthesis is interleaved
      with pydub assembly, which is *why* the provider fallback has to be mid-episode and
      can leave the show in two voices. Render every section to WAV in one pass, then
      assemble. The canary makes the mixed episode unrepresentable at the *start* of a
      render; this makes it unrepresentable full stop, including when Gemini dies mid-show.
      Unlocks the two items below.
- [ ] Persist section WAVs across stages, so re-running `--stage render` reuses the sections
      Gemini already landed and retries only the gaps — turning a 40-minute all-or-nothing
      render into incremental convergence, and making a workflow-level retry worth having.
- [ ] Consider a Vertex AI endpoint as a second Gemini capacity pool (separate from AI
      Studio for the same models, so it dodges AI-Studio-only outages). Costs a
      service-account setup — only worth it if the probe shows outages rather than rejections.
- [ ] Consider bisect-on-failure: a section that fails twice splits at a speaker boundary
      and renders halves, bounded to two levels. Deferred until the cheaper rungs are
      measured — it may prove unnecessary.
- [ ] Permanent episode memory with weighted recency (replace 21-day hard limit)
- [ ] Local holidays and events integration in episode openings
- [ ] Evolving stories context - flag when covering updates to previously discussed topics
- [ ] Better theme-to-article matching (currently just takes top 4 scored articles)

## Long-term / Speculative
- [ ] Listener feedback loop - topic requests or engagement signals shape future episodes
- [ ] Cross-project: shared interest/scoring config between RSS and Podcast systems
- [ ] Monetization: podcast sponsorships, premium episodes
- [ ] Multi-show support - same infrastructure, different regional focuses
- [ ] Decouple rendering from the daily deadline — bank Gemini sections opportunistically in
      a separate scheduled job with generous retries, and let the daily run assemble what
      landed, using OpenAI only for what never did. Needs the two-phase render and section
      WAV persistence first.
- [ ] Consider Gemini for holistic podcast generation - may be possible on free tier
- [ ] Consider pydub for music integration and reducing API calls to Claude
