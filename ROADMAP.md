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
- Daily editorial review of the run itself (`episode_review.py`), published to the site and
  carried on `episode-reviews.xml` — the section below is distilled from it

## From the daily reviews

`episode_review.py` narrates each night's run — what it did, what broke, and what it chose
when something broke, and then distils that night into the items below. Each names the
evidence so it can be closed on a fact rather than a feeling. Findings the reviews surfaced
and that are already fixed (citation matching, the TTS duration checksum's fitted constants,
the canary's second ask, Brave's two payload shapes, labelling the facts handed to the
narrative) are not repeated here — see 0a4c019 and 1ed384e.

**This block is maintained by `episode_review.py`.** It rewrites everything between the two
markers after each night's review, so edits to an item's text are overwritten. What a human
says here is said by checking a box: a checked item is closed, and the ledger
(`podcasts/roadmap_ledger.json`) remembers that, so a review mentioning it again tomorrow
cannot reopen it. An item tied to a signal (a `degrade()` row, a metric past its line) also
closes itself once that signal has been absent from the run's facts for three days. Anything
written outside the markers is never touched.

<!-- reviews:begin -->

_Distilled from the daily reviews by `episode_review.py` (2026-09-25..2026-10-19) — 1 open. An
item with a signal closes itself once the signal has been absent for 3 days. Check a box to
close one; it comes back only if the reviews raise it 2 more times._

- [ ] **Deep dive citations matched at 33% versus roundup at 80%.** On September 13, the roundup
      section matched 12 of 15 citations (80%), but the deep dive section matched only 1 of 3
      (33%). The gap suggests a different verification pathway or a shortfall in source
      retrieval for longer-form segments. The debate question on Roberts Bank Terminal 2
      proceeded despite this disparity. Audit the deep dive citation matching logic and the
      Brave API call sequence to confirm whether thin article bodies or budget exhaustion
      degraded the deep dive's source alignment. (signal `citations:deep-dive`; seen in 4
      reviews, latest 2026-10-19)

<!-- reviews:end -->

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
- [x] Submit to Apple Podcasts (see [docs/submit-apple-podcasts.md](docs/submit-apple-podcasts.md))
  - [x] Upgrade cover art to 1400x1400+ pixels (Apple minimum) — 3000x3000
  - [x] Replace placeholder email in config/podcast.json
  - [x] Submit RSS feed at podcastsconnect.apple.com
- [x] Submit to Spotify
- [ ] Submit to Amazon Music, Pocket Casts
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
- [ ] **Nightly voice-card drift check.** The weekly review now scores each host against their
      `voice_style` card (`hosts.json`). Once a few weekly reviews are in, decide whether drift
      deserves a nightly signal in `episode_review.py` (`run_signals`), e.g. `voice-card:tic`
      (one card move in most of a host's turns) and `voice-card:anecdote` (a host speaking from
      a life of their own). Count them in Python off the script where a pattern holds; a new
      Haiku finding only if it doesn't. Don't build it until the weekly reviews show the drift
      is real.
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
- [ ] Do something with the cull. The feed hands the pipeline roughly five times what a
      22-minute show can carry — 77 candidates against a 15-story roundup on 2026-08-23, 62
      dropped over budget and 9 more to the 3-story cluster cap — and every review narrates
      that as loss. The cut itself is right (airtime, not appetite) and dropped stories never
      reach citations, so they can resurface on a better-matched day. The open question is
      whether the day's cull is worth its own surface on the site, not whether the show
      should be longer.
