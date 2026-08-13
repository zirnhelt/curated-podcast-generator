# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Role and Style

Direct, technical, efficient. No fluff. No apologies. Get straight to the technical solution. Explain the "why" behind significant architectural decisions briefly before writing code.

Apply the **ponytail** decision ladder before writing any code — stop at the first rung that satisfies the task:
1. Does this need to exist? (YAGNI — skip it)
2. Does the standard library handle it?
3. Is there a native platform feature?
4. Is an installed dependency already doing this?
5. Can it be one line?
6. Only then: write the minimum that works.

Mark shortcuts with `# ponytail:` comments naming the simpler path chosen. Safety, security, data-loss handling, and accessibility are never cut.

## Workflow

1. Analyze the request.
2. If the request is unclear, ask for clarification immediately.
3. Propose the technical solution (short).
4. Implement the solution.
5. Summarize changes, highlighting any new dependencies or breaking changes.

## Commands

```bash
# Run all tests
python -m pytest tests/ -v

# Run a single test file
python -m pytest tests/test_podcast_generator.py -v

# Run a single test
python -m pytest tests/test_psa_selector.py::TestPSASelector::test_round_robin -v

# Local development run (requires .env or exported API keys)
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# ffmpeg must be installed (apt install ffmpeg / brew install ffmpeg)
python podcast_generator.py                    # both stages (default)

# Run one stage at a time
python podcast_generator.py --stage script     # curate + write script, no TTS spend
python podcast_generator.py --stage render     # TTS + assembly only, no publishing
python podcast_generator.py --stage publish    # transcript, feeds, index.html, R2 sync
python podcast_generator.py --stage recover    # re-render past episodes missing audio
python podcast_generator.py --stage audio      # recover + render + publish (back-compat)

# Re-render or re-publish a past or hand-edited script
python podcast_generator.py --stage render --date 2026-07-24
python podcast_generator.py --stage publish --date 2026-07-24
python podcast_generator.py --stage audio --script podcasts/podcast_script_2026-07-24_theme.txt

# See the per-segment status table locally (CI writes it to the job summary)
GITHUB_STEP_SUMMARY=/tmp/summary.md python podcast_generator.py --stage publish

# Bespoke episode
python generate_bespoke.py --tag <topic-tag>
```

**Note:** `tests/` is in `.gitignore`. Use `git add -f tests/` when committing test changes.

Tests require no API keys — `tests/conftest.py` installs lightweight stubs for `anthropic`, `openai`, `pydub`, and `azure` at import time.

**State-file isolation:** the live memory/state JSON files in `podcasts/` (PSA rotation, episode/debate memory, article holding) are production data committed daily by CI — code under test that persists state will rewrite them in place. An autouse fixture in `tests/conftest.py` already redirects `psa_selector.PSA_STATE_FILE` to a tmp copy; any new test (or code path) that touches a `podcasts/` state file must get the same treatment (monkeypatch the path/`PODCASTS_DIR` into `tmp_path`). After any local test run, check `git status` — a modified state file is test leakage to be reverted (`git checkout -- podcasts/<file>`), never committed.

## Architecture

### High-Level Flow

This is a daily AI podcast generator for **Cariboo Signals**, a two-host show (Riley & Casey) covering rural BC tech and community topics. The pipeline runs on GitHub Actions and deploys audio + RSS to GitHub Pages.

**Daily run (`podcast_generator.py`):**
1. Idempotency check — exits if today's episode already exists in the RSS feed
2. Pull scored articles from sibling repo `super-rss-feed` (fetches `feed-podcast-{dayname}.json` from its GitHub Pages URL)
3. Deduplicate against last 7 days of citations (`dedup_articles.py`, optionally Cohere embeddings via `cohere_enrichment.py`)
4. Cluster same-story articles; super-cycle routing (release matured held articles, hold off-theme ones for their focus day); select top stories + theme/focus-matched deep-dive articles
5. Claude generates raw two-host script → Claude polishes script (flow, repetition). Length QA: scripts under `TARGET_SCRIPT_WORDS` (~22-min floor) get one expand retry; under `MIN_SCRIPT_WORDS` after retry the run aborts. Target runtime 22–25+ min.
6. Writes citations JSON and every memory/state file, saves the script to `podcasts/podcast_script_{date}_{theme}.txt`
   — **end of the script stage** (`run_script_stage`); the workflow commits and pushes here
7. OpenAI TTS (or Azure Neural TTS) renders each speaker segment in parallel
8. pydub assembles: cold open teaser (10–20 s, before the music) → intro → welcome → interval → news roundup → interval → deep dive debate → outro
9. Writes transcript + RSS entry, pushes commit, deploys to `gh-pages`

### Stages and Segments

The pipeline is split at two levels: **stages** are separate processes with a git commit
between them, **segments** are named phases inside one process with their own failure policy.

#### Stages (`--stage`)

| Stage | Entry point | Steps | Notes |
|-------|-------------|-------|-------|
| `script` | `run_script_stage` | 1–6 | Where the API spend lives |
| `recover` | `run_recover_stage` | — | `_recover_orphaned_episodes`, 3-day lookback |
| `render` | `run_render_stage` | 7–8 | TTS + pydub assembly + sidecars |
| `publish` | `run_publish_stage` | 9 | Transcript, RSS, tts-test feed, `index.html`, R2 |
| `audio` | `run_audio_stage` | 7–9 | `recover` + `render` + `publish` (back-compat) |
| `all` | — | 1–9 | Default; behaves like the original single-process run |

The stages fail differently, which is the whole reason they are separate. Script generation
is where the API spend lives; rendering is where the runner dies (an unbounded-memory ffmpeg
render once OOM-killed the VM); publishing fails on credentials, network and disk, and used
to force a full 40-minute re-render to retry. The daily workflow commits between each, so a
failure costs only its own stage.

Because stages are separate processes, some values cannot ride in locals and are carried in
the script file's `#` header instead, read back by `read_script_metadata`:
- **`# Theme:`** — the feed can override the weekday theme, which changes the filename slug,
  so the audio stages must never recompute it. Audio paths are derived from the script's own
  filename (`_episode_paths`).
- **`# Brave:`** — gates one sentence in the spoken credits. Scripts predating this header
  degrade to `no`.
- **`# Anchor:`** — the week's anchor question, which is named on air and appears in the
  episode description. Whitespace-collapsed to one line, since the header parser reads one
  key per line. Scripts predating this header degrade to `None`.

#### Segments (`segment()`)

Every phase inside a stage runs in a `with segment(name, critical=...)` block — a context
manager, not an extracted function, so wrapping existing code changes no variable lifetimes.

- **`critical=True`** (default): the exception propagates; pass `exit_code=` to convert it
  into a distinct process exit status instead of a traceback.
- **`critical=False`**: the exception is swallowed and the run continues. **The caller must
  pre-assign the block's outputs to their fallback value before the `with`** — a non-critical
  segment must never be the only place a downstream variable gets bound.
- `SystemExit` always passes through untouched, so the deliberate aborts keep their codes.

Roughly: article acquisition, script generation and saving the script are critical; weather,
Brave research, PSA selection, polish, cold open, quality scoring, publishing surfaces and
every individual state-file write are not. Each memory/state file gets its own segment — a
failure partway through the persistence run used to mark seeds and email consumed while
leaving three memory files unwritten, with nothing in the log naming which.

#### Handled degradations (`degrade()`)

`segment()` can only downgrade a phase whose exception *escapes* the block, but most
fallbacks handle their own: the TTS provider fallback, music-less mode, a missing R2
credential, an episode dropped from the feed. The phase then finished "successfully"
having produced a materially different result, and the run went green — on 2026-08-02 a
whole episode was re-rendered on OpenAI after Gemini died, visible only in stdout.

`degrade(name, detail)` records that. Passing the enclosing segment's own name downgrades
that phase in place; any other name gets its own row, which is how a fallback with no
segment of its own still reaches the table. Repeat calls under one name merge, so a
failure inside a per-episode loop is one row rather than fifty. Every call emits a
`::warning::` annotation.

**When you add a fallback, add a `degrade()` call.** A silent fallback is the failure mode
this exists to prevent — the fallback itself is usually right, the silence never is.
`run_publish_stage` derives `EXIT_PUBLISH_DEGRADED` from these records, so a publish
surface that swallows its own failure makes that exit code unreachable.

`write_run_report()` appends a per-segment table (status, duration, error) to
`$GITHUB_STEP_SUMMARY`, printing to stdout when that is unset. It is called from a `finally`
in `main()`, so a crashed run still reports which segment died.

#### Exit codes

| Code | Meaning |
|------|---------|
| 75 | `EXIT_BUDGET_EXHAUSTED` — Anthropic spend cap; the workflow skips the day as a warning |
| 76 | `EXIT_NO_ARTICLES` — upstream feed gave us nothing usable; the workflow skips the day as a warning |
| 77 | `EXIT_RENDER_FAILED` — no audio produced; the run goes red |
| 78 | `EXIT_PUBLISH_DEGRADED` — audio is safe, one or more publish surfaces failed |

#### Committing between stages

Every workflow that commits uses the `./.github/actions/commit-push` composite action —
never an inline `git add`/`commit`/`push` block. It stages each pathspec separately (a
single unmatched glob used to abort the whole `git add` and stage nothing), commits only
when the index is non-empty, then rebases with `--autostash` and retries the push three
times. `fatal: 'true'` makes a push that never lands fail the step; the default is a
`::warning::`.

`--autostash` is load-bearing: the render and publish stages rewrite tracked files the
commit step does not stage, and a plain `git pull --rebase` refuses to start against a
dirty tree. That refusal was swallowed by `|| true`, which sent the push out un-rebased —
the 2026-07-26 triple render and the 2026-08-02 sidecar failure.

**If a stage writes a tracked file, some step must stage it.** `index.html` was
regenerated on every publish and staged by nothing, so it sat permanently dirty and broke
the rebase on every single run.

#### Atomic state writes

Every memory/state/feed write goes through `config_loader.atomic_write_text` /
`atomic_write_json` (temp file + `os.replace`). It lives in `config_loader` so
`psa_selector` can share it without a circular import. This is not optional: the loaders
swallow a truncated JSON as `{}`, so a crash mid-write silently discarded a 35- or 90-day
history, and a truncated `podcast-feed.xml` breaks every podcast client at once.

**Memory state** (JSON files in `podcasts/`):
- `episode_memory.json` — 35-day sliding window for story continuity (spans a full 4-week super cycle; entries record the day's focus slug)
- `host_personality_memory.json` — Evolving host traits
- `debate_memory.json` — 90-day window to avoid repeating debate angles; must-differ filter keys on (theme, focus)
- `psa_rotation_state.json` — Round-robin PSA org rotation state
- `article_holding.json` — Super-cycle holding pen + aired-early callback ledger
- `weekly_anchor_state.json` — This week's pinned anchor question + the no-repeat ledger (ids forever, dimensions for 26 weeks)

### Configuration System (`config_loader.py`)

All content is externalized to `config/` JSON files; loaders are LRU-cached (single load per process). No hard-coded strings — all messaging, personalities, and themes live in `config/`.

| File | Purpose |
|------|---------|
| `podcast.json` | Title, RSS metadata, TRACE accountability scores |
| `hosts.json` | Riley & Casey — bios, voices, personalities, debate stances |
| `themes.json` | 7 rotating daily themes (Mon–Sun), keywords, editorial lenses |
| `super_cycles.json` | Multi-week focus rotations within each daily theme (slug, keywords, lens per focus) |
| `weekly_anchors.json` | Seeded pool of weekly anchor questions (question, dimension, premise, optional `pin_week`) |
| `prompts.json` | All Claude prompt templates (~100 KB, cached in one call) |
| `interests.txt` | Article relevance scoring rubric (primary/secondary/avoid) |
| `blocklist.json` | Excluded domains and keywords |
| `psa_organizations.json` | Community org roster + weekday assignments |
| `disciplines.json` | Topic hierarchy for news roundup grouping |

### Themes

Seven rotating daily themes indexed by weekday (0=Mon):
- 0 Mon: Arts, Culture & Digital Storytelling
- 1 Tue: Working Lands & Industry
- 2 Wed: Gear, Gadgets & Practical Tech
- 3 Thu: Indigenous Lands & Innovation
- 4 Fri: Wild Spaces & Outdoor Life
- 5 Sat: Cariboo Local Affairs (longer episode, 15 articles)
- 6 Sun: Science, Wonder & the Natural World

### Super Cycles (`config/super_cycles.json`)

Each daily theme (except Saturday, deliberately uncycled) rotates through a multi-week **focus** — e.g. Tuesday cycles agriculture → forestry → mining → tourism, one focus per week. Friday runs a 3-week cycle, all other cycled days 4-week. The cycle position is calendar-derived (`(date.toordinal() // 7) % cycle_length` per weekday via `get_focus_for_day`) — stateless, idempotent on re-runs, predictable ahead of time.

- **Selection:** the deep dive prefers focus-matching articles; a thin focus week (<3 matches) degrades to plain theme selection (logged `focus_fallback`). The focus lens is appended to the theme lens in the script prompt.
- **Subtlety:** the focus is deliberately unannounced on air — it shapes selection and emphasis only. Hosts name and acknowledge the weekday theme, never a rotating sub-theme; every focus-derived prompt block carries a do-not-announce instruction.
- **Article holding (`route_articles_for_focus`):** off-theme, non-urgent articles matching an upcoming focus within 14 days are held in `podcasts/article_holding.json` and released (flagged `_held_from`, framed as "earlier this week") on their focus day. Urgent ones (`_boosted_score ≥ 85`) air same-day in the bonus bucket (never deep-dive) and are remembered in the aired-early ledger for an on-air callback when their focus day arrives. Holding never shrinks the pool below the roundup + deep-dive budget.
- **Repeat-topic guard (`format_prior_coverage_for_prompt`):** local word-overlap check of deep-dive titles against recent episode topics and debate questions; on a match, hosts are instructed to acknowledge the earlier discussion and center what's new. Evolving-story context carries the same instruction.

### Weekly Anchor Questions (`weekly_anchor.py`, `config/weekly_anchors.json`)

The third rotation layer, above the daily theme and the super-cycle focus. One open question
per ISO week — "Why is everyone in tech so sad?" — that all seven deep dives circle from their
own theme's angle. Selected by `select_anchor()` in the non-critical `script/anchor` segment,
rendered into the script prompt's `{anchor_block}` by `format_anchor_for_prompt()`.

Unlike the focus, the anchor **is named on air**: the focus is a curation device, the anchor is
an editorial idea and the reason to listen more than one day a week.

- **Idempotency is bought with state, not the calendar.** `get_focus_for_day` is stateless
  because the rotation is a pure function of the date; an anchor cannot be, because the pool is
  eventually LLM-generated. Instead the week's choice is **pinned** in
  `podcasts/weekly_anchor_state.json` on the first run of the ISO week, and every later run that
  week — including a re-render days later — reads that record back unchanged. A second question
  appearing mid-week is the failure this prevents.
- **No repetition works on `dimension`, not wording.** Each question is tagged with the
  dimension of experience it opens (`labour-meaning`, `scale`, `trust`, …). An `id` is spent
  forever; a `dimension` has a 26-week cooldown. Keying the guard on the dimension is what stops
  a generated question returning as a paraphrase. Both checks are local — no API call.
- **Pool with LLM top-up.** `config/weekly_anchors.json` ships 11 seeded questions in order.
  `top_up_pool()` fires when eligible entries drop below `MIN_POOL_REMAINING` — before the pool
  empties, so a failed top-up costs a warning rather than the week's anchor — conditioned on
  every question and dimension already used. Roughly one call every 11 weeks.
- **`pin_week`** forces a question onto a specific ISO week. A **future** pin is never taken
  early; an **overdue** pin still runs, so shipping late does not bury a question that was
  scheduled deliberately.
- **Framing, never selection.** Article selection is untouched — the anchor is a lens for
  reading whatever the theme and focus already chose. One Claude call per week generates the
  seven per-weekday angles; a failure degrades to an anchor with `framings: {}`, which still
  works.
- **The escape hatch is load-bearing.** The prompt block ends with an instruction to drop the
  anchor entirely when the day's material does not genuinely reach it. Seven days orbiting one
  question is a standing invitation to manufacture connections — the same failure the roundup's
  `_NEVER_ANNOUNCE` block headers exist to prevent, with a much stronger pull. There is a test
  asserting that instruction is present.
- `weekly_anchor` cannot import `degrade()` without a circular import, so it records
  degradations and `run_script_stage` drains them via `_report_anchor_degradations()`. **A new
  fallback here must append to `_degradations`** or it will not reach the run report.
- Preview the schedule without spending or writing state: `python weekly_anchor.py --preview 12`.

### TTS Providers

**OpenAI (default):** `nova` (Riley) + `echo` (Casey), per-segment synthesis, parallel rendering.

**Azure Neural TTS (optional, `USE_AZURE_TTS=1`):** Multi-Talker model for coherent prosody across speaker transitions. SSML with `<phoneme>` IPA tags for Cariboo place names. 8,000-char conservative SSML chunk limit. Set `AZURE_TTS_PARALLEL=1` to generate both providers for comparison.

**Gemini multi-speaker TTS (optional, `USE_GEMINI_TTS=1`, wins over Azure):** `gemini_tts.py` renders each section's whole two-host conversation in one `generateContent` call (NotebookLM-style prosody) via REST — needs `GEMINI_API_KEY`; `GEMINI_TTS_MODEL` overrides the default flash model. A style prompt plus whitelisted `(cue)` stage directions live in `config/prompts.json` under `gemini_tts`; the polish pass only adds cues when Gemini is active, and the OpenAI/Azure paths strip them. Credits on every surface resolve through `get_active_tts_provider()` — the provider that actually rendered the audio wins (an OpenAI fallback is credited as OpenAI). Compare providers with `python evaluate_tts.py`.

#### Getting Gemini through a whole episode

An episode is 6–9 independent Gemini calls, so per-call reliability compounds — in the week of 2026-08-01, seven of seven episodes fell back to OpenAI at or before the welcome section, and three shipped a Gemini cold open with an OpenAI show. Four mechanisms exist to stop that, in the order they fire:

- **Canary (`gemini_tts.canary()`).** One tiny throwaway synthesis before any audio exists, run from `generate_audio_from_script`. It decides the provider for the whole episode: a failed canary pins OpenAI up front, which is what makes a mixed-voice episode *unrepresentable* rather than merely unlikely. It probes the fallback model too, and pins it only if the primary is the one that's down.
- **Retry ladder (`RETRY_LADDER`).** Each rung changes the *shape* of the request, not just the seed — `finishReason: OTHER` returns `promptTokenCount == totalTokenCount`, i.e. a rejection of what was asked, which reseeding cannot fix. Rungs shed the context tail, then the style prompt, then the cues. Backoff (0/15/45/90/90 s) is sized to outlast the minutes-long capacity windows the old 5 s/10 s ladder always died inside.
- **Model ladder.** `GEMINI_TTS_FALLBACK_MODEL` (default pro TTS) is tried at rung 3, *before* the primary model with a bare transcript: voices are pinned by `speechConfig` on every rung, so a model change keeps the hosts sounding like themselves while a stripped prompt loses the direction. Pro costs more than flash, which is why it sits behind three primary failures.
- **Failure-shape routing (`_is_transport_failure`).** The rung order above assumes a *rejection*. A read timeout or dropped connection carries no verdict on the prompt, so on one the ladder skips straight to a rung that changes the model, and stops outright when `_model_override` means every remaining rung would re-ask the model that just went silent. Without this the two prompt-shedding rungs cost 120 s each and pushed the model rungs out of `SECTION_BUDGET_S` entirely — three timeouts spend 120+15+120+45+120 = 420 s, the budget exactly, which is why every August 2026 episode fell back to OpenAI mid-show and no model rung ever ran.
- **Budgets.** `SECTION_BUDGET_S` bounds one chunk's ladder; `set_render_deadline()` (called with `GEMINI_RENDER_DEADLINE_S`) bounds all Gemini work in a render, so a provider that dies *after* the canary passed cannot eat the 40-minute render step one section at a time. `_budget_allows` reserves the attempt's own read timeout as well as its backoff, so a retry that cannot finish inside the budget is never started.

**Ordering rule:** degrade delivery nuance before voice identity. Anything that changes *who the hosts sound like* is the last resort, which is why the whole-episode OpenAI decision is made up front rather than drifted into mid-show.

`gemini_tts` cannot import `degrade()` without a circular import, so it records degradations and the render path drains them via `_report_gemini_degradations()`. **A new fallback in `gemini_tts` must append to `_degradations`** or it will not reach the run report.

Diagnose a rejection with `python evaluate_tts.py --probe-gemini` (or the `probe_gemini` input on the `TTS Eval` workflow, which writes the table to the job summary): it asks the same text with progressively less prompt around it. Rung 0 failing while a later rung passes names the element Gemini is rejecting; every rung failing equally is an outage or a quota wall.

### Cohere Enrichment (`cohere_enrichment.py`)

Optional (`USE_COHERE=1`). Three stages:
1. Evolving-story detection via embedding cosine similarity (threshold 0.88) against 7-day citations
2. Intra-batch clustering to suppress duplicate articles (threshold 0.85)
3. Deep-dive reranking via Cohere Rerank endpoint

All public functions return `None` when disabled; callers fall back to string-matching transparently.

### Bespoke Episodes (`generate_bespoke.py`)

Long-form debate episodes triggered manually or when 3+ content seeds share the same tag (`seed.py`). Same Riley & Casey personalities but no news roundup — entire episode is a deep dive. Output goes to `podcasts/bespoke/`. Optional Brave Search expansion for source gathering.

### PSA Selection (`psa_selector.py`)

Event-driven: 7-day lookahead for awareness dates. Round-robin fallback cycling through `psa_organizations.json` with 28-day minimum between repeats per org. State persisted to `psa_rotation_state.json`.

### Sibling Repository

`super-rss-feed` scores and categorizes articles, publishing `feed-podcast-{dayname}.json` to its GitHub Pages URL. The podcast generator fetches this at runtime. Deploy order matters: super-rss-feed must deploy before the podcast generator runs. See `SIBLING_REPOS.md` for integration details.

## API Cost Discipline

Treat API budget as a first-class constraint on every change.

- **Default to the cheapest model.** Escalate (Haiku → Sonnet → Opus) only when demonstrably required — justify explicitly. Opus is only used for review escalation when deep-dive sourcing is thin (<3 articles).
- **Prompt compression is mandatory.** Strip filler and redundant context before sending.
- **Cache aggressively.** Use Anthropic `cache_control` headers for large static context (system prompts, article bodies, tags) reused across calls.
- **Batch where possible.** Combine small tasks into one API call instead of N round-trips.
- **Never call an API when local logic suffices.** Dedup, filtering, formatting, classification — do it in Python first.
- **Log token usage.** Every call that returns usage metadata must log it. No silent spending.
- **Fail fast on runaway cost.** Unexpectedly large token counts should raise, not proceed.
- **Review diffs for cost regressions.** Call out any prompt/pipeline change that increases per-run token usage.

## Project Constraints

- Python 3.11+, PEP 8, type hints on all functions
- Idempotent scripts where possible
- Refactor existing files rather than creating new ones
- Keep dependencies minimal — check `requirements.txt` before adding anything
- `tests/` is gitignored; use `git add -f tests/` to stage test files
