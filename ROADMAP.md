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
cannot reopen it. Anything written outside the markers is never touched.

<!-- reviews:begin -->

_Distilled from the daily reviews by `episode_review.py` — 7 open. Check a box to close one; it
comes back only if the reviews raise it 2 more times._

- [ ] **A credit-balance 400 is not the usage-limit wall, and every run pays for that.**
      2026-08-23: all three crons died with `Your credit balance is too low to access the
      Anthropic API` and exited 1, so the day went red and the episode only shipped from a
      manual dispatch at 14:42 UTC, six hours late. `_usage_limit_reset` keys on the string
      `usage limit`, which this message does not contain, so `check_api_budget()` printed
      "preflight inconclusive — continuing" and the run went on to spend 40 article body
      fetches, ~37 Brave enrichment calls and an agentic research call before failing at the
      script call — three times over, which is the exact waste the preflight was written to
      prevent (see its docstring on 2026-07-25). Match the credit-balance refusal too and exit
      `EXIT_BUDGET_EXHAUSTED`; the workflow already turns 75 into a skipped day with a warning
      instead of a failure.
- [ ] **The review goes quiet on exactly the days worth reviewing.** `main()` only fetches a job
      log from a run whose conclusion is `success`. On 2026-08-23 there was no such run, so the
      review published a bare trigger table and no narrative — three failures and not a word
      about why. Fall back to the newest failed run's log (the facts are all there: the 400
      above is in it), and keep the successful-run preference for ordinary days.
- [ ] **The review cannot see the run that made the episode when a cron did not.** It is gated
      on the third cron, so 2026-08-23's manual dispatch four hours later is absent from the
      write-up and from the archive. Either widen `fetch_runs` past `event: schedule` for the
      day, or make the review re-runnable for a date and re-publish over the same file (`--date`
      already exists; the index and feed update in place).
- [ ] **The review reports itself as an unfinished run.** The `review` job runs inside the 3:05
      AM cron's own workflow run, so every review to date ends with "Fallback 2 … in_progress"
      and the narrative reads it as a pending unknown ("a third in progress at review time",
      2026-08-20). `summarize_runs` knows `GITHUB_RUN_ID`; label that row as the reviewing run
      so the model stops treating it as a cliffhanger.
- [ ] **Decide what to do about scheduler drift.** Every trigger in the sample started late:
      +44/+29/+32 (08-20), +46/+31/+31 (08-21), +34/+20/+25 (08-22), +35/+22/+25 (08-23). This
      is GitHub's scheduled-run queue, not the pipeline, and the reviews report it as a fault
      every single day. Either move the crons earlier and accept the drift as the schedule, or
      keep the number and stop framing it as one — a fact reported daily as a problem and never
      acted on trains the reader to skip the paragraph.
- [ ] **Watch the four 2026-08-22 fixes on air.** None has been observed in a shipped episode:
      the runs the day after all died at the credit wall. The first green day's review is the
      check — citation alignment should land near 51%/55% rather than 5-8/15 and 0-1/deep dive,
      `tts_short_segments` should be ~2% of segments rather than 13 of 16, and the canary should
      only pin OpenAI after two timeouts per model.
- [ ] **Gemini has not rendered an episode in the review window.** 08-20, 08-21 and 08-22 all
      pinned OpenAI on canary read timeouts against `generativelanguage.googleapis.com`; 08-23
      never got that far. The probe item under Short-term is the diagnostic — the reviews now
      give it a daily before/after record, so run it and read the next week's reviews rather
      than re-reasoning about the ladder.

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
- [ ] Do something with the cull. The feed hands the pipeline roughly five times what a
      22-minute show can carry — 77 candidates against a 15-story roundup on 2026-08-23, 62
      dropped over budget and 9 more to the 3-story cluster cap — and every review narrates
      that as loss. The cut itself is right (airtime, not appetite) and dropped stories never
      reach citations, so they can resurface on a better-matched day. The open question is
      whether the day's cull is worth its own surface on the site, not whether the show
      should be longer.
