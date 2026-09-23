# Operations: scheduling, stages, money, Brave spend and the roadmap

*Moved verbatim from CLAUDE.md on 2026-09-23. CLAUDE.md keeps the standing rules; this file keeps the incidents and the reasoning behind them. Read it before changing the code it describes.*

## Who starts the run

The pipeline runs on GitHub Actions; the *trigger* comes from Cloudflare. GitHub's
cron is best-effort — it delays scheduled workflows under load and drops the tick
outright once the delay passes the next window — and a trigger arriving after the
6:30 AM Pacific listener wakeup has missed the day. So the 1:05 / 2:05 / 3:05 AM
Pacific ladder is now five Cron Triggers on a Worker (`cloudflare/scheduler/`,
shared with `super-rss-feed`) that `workflow_dispatch` the workflow with a
`run_slot` input.

Only the trigger moved. Workers cannot host the pipeline — 128 MB, no
subprocesses, no ffmpeg, and audio assembly alone peaks at 300–600 MB.

**The Worker starts runs; it does not decide whether one is needed.**
`check-episode` still owns that, and must stay the only implementation of it: a
second copy in the Worker is a second source of truth for "did today ship?",
whose failure mode when the two drift is a silently skipped day.

**`daily-podcast.yml` keeps one GitHub cron** — `5 11 * * *`, 4:05 AM Pacific —
as the backstop for the *Worker* being down. It costs ~20 s on a normal night
and is what keeps the schedule from depending on one vendor. Do not remove it,
and do not add ladder slots back to it.

**Anything that used to branch on `github.event.schedule` must now read
`inputs.run_slot`.** The `review` job is the one that already did, and it is the
quiet failure to watch for: the episode ships fine without it, so a broken gate
shows up only as a roadmap that stopped updating.

Deploy with **Actions → Deploy Cloudflare Scheduler**; there is no local wrangler
path. See `cloudflare/scheduler/README.md` for the token scopes, the recorded PAT
expiry, and the rollback.

## Stages and Segments

The pipeline is split at two levels: **stages** are separate processes with a git commit
between them, **segments** are named phases inside one process with their own failure policy.

### Stages (`--stage`)

| Stage | Entry point | Steps | Notes |
|-------|-------------|-------|-------|
| `script` | `run_script_stage` | 1–6 | Where the API spend lives |
| `recover` | `run_recover_stage` | — | `_recover_orphaned_episodes`, 3-day lookback |
| `render` | `run_render_stage` | 7–8 | TTS + pydub assembly + sidecars |
| `publish` | `run_publish_stage` | 9 | Transcript, RSS, `index.html`, R2 |
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
- **`# Weather:`** — gates the spoken weather-provider credit. The weather check is read on
  air in the welcome, and the episode description had credited Open-Meteo since the segment
  existed while the spoken credits never did (2026-08-17). Gated on the flag rather than on
  config, because on a day the fetch fails there is no weather segment to credit. Scripts
  predating this header degrade to `no`.
  **A day the fetch fails now says so.** `fetch_weather()` handles its own failure and returns
  `None`, so `script/weather` finished clean and the run report called the phase fine on an
  episode with no weather check in it — the exact silent fallback `degrade()` exists to catch.
  Open-Meteo is free, keyless and normally answers in well under a second, and had no retry at
  all until all five locations hit their 10 s read timeout inside one window on 2026-09-06; it
  now re-asks once on a transport failure (never on a malformed body, which would come back
  malformed) and `degrade()`s when the sweep comes back empty.
- **`# Anchor:`** — the week's anchor question, which is named on air and appears in the
  episode description. Whitespace-collapsed to one line, since the header parser reads one
  key per line. Scripts predating this header degrade to `None`.

### Segments (`segment()`)

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

### Handled degradations (`degrade()`)

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

### Exit codes

| Code | Meaning |
|------|---------|
| 75 | `EXIT_BUDGET_EXHAUSTED` — Anthropic usage limit; the workflow skips the day as a warning |
| 76 | `EXIT_NO_ARTICLES` — upstream feed gave us nothing usable; the workflow skips the day as a warning |
| 77 | `EXIT_RENDER_FAILED` — no audio produced; the run goes red |
| 78 | `EXIT_PUBLISH_DEGRADED` — audio is safe, one or more publish surfaces failed |
| 79 | `EXIT_CREDITS_EXHAUSTED` — a provider is out of credits; the run goes **red** |

**75 and 79 are the same outage to the listener and a different one to the operator**, which
is the whole reason they are separate codes. A usage limit lifts itself on a stated date, so
skipping the day quietly is right. An empty credit balance lifts only when a human tops it up
— and a `::warning::` on a green run reaches nobody. On 2026-08-26 three crons skipped exactly
that way with both TTS providers dry, and the outage was found by hand hours later. 79 goes
red so GitHub's own failed-run notification does the alerting: the alert is an exit code, not
a service to build and keep alive.

### Preflighting the money (`check_api_budget`, `_check_tts_budget`)

`_billing_wall()` recognizes both walls across all three providers, because each words it
differently and none of them says "usage limit" — `_usage_limit_reset` alone matched only
Anthropic's, so the 2026-08-23 credit-balance 400 read as "preflight inconclusive" and each of
the three crons spent 40 article fetches, ~37 Brave lookups and a research call before dying at
the script call. **Match on the credit wording, never the status code**: Gemini answers an
ordinary per-minute rate limit with the same 429 `RESOURCE_EXHAUSTED`, and reading that as a
wall would skip a day a retry would have shipped.

**TTS is preflighted in the script stage, not at the render**, because the script stage is
where both the money and the day's state go: its commit rotates the PSA, consumes seeds and the
email queue, pins the week's anchor, and marks every chosen article as cited, which is what
stops dedup offering them again. On 2026-08-26 all of that was spent for an episode that could
never air.

OpenAI is the universal fallback, so its health alone answers "can this day ship?" — the common
path is one ~1-character synthesis, well under a hundredth of a cent. **The configured primary
is probed only once OpenAI is already walled** (Azure, removed 2026-09-23, was never aborted
on: a subscription has no equivalent cheap probe). Skipping a day that Gemini would have rendered is worse than
the wasted run this exists to prevent, so the abort fires only when nothing left can render.

### Committing between stages

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

### Atomic state writes

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
- `phrase_ledger.json` — 21-episode rolling phrase-frequency window + the burned list
- `roadmap_ledger.json` — findings distilled from the daily reviews, with their recurrence
  counts and closed/retired records; renders the managed block of `ROADMAP.md`
- `native_land_cache.json` — place name → coordinates + territory names. A lookup
  cache, not history: losing it costs repeat lookups, never a claim

## Brave spend (`_brave_search`, `_BRAVE_WALLS`, the three call budgets)

**Two plans, two meters** (since 2026-08-29). Search is $5/1000 requests against a
self-imposed **$10 monthly** spend limit — 2,000 requests — and Answers is $4/1000 queries plus
$5/MTok each way, held to its **monthly free credit** with no paid overage. Both refuse past
their limit rather than billing on, so a 402 can arrive on any day of the month. The pipeline's
job is to spend each month's calls on work that reaches the listener, and to stop instantly
once one of them has nothing left.

**The fallback crons are the multiplier that spends a month.** The workflow fires at 1:05, 2:05
and 3:05 Pacific; the later two exit on the idempotency check and cost nothing — unless the
first run failed *after* spending, when the day costs three full sets of calls (2026-08-23).
Triple-cron days are the pathology the per-run ceilings exist to bound, not the ordinary one.

**The Search cap is shared with `super-rss-feed`, and the estimate here was a third of the
truth.** Brave's per-key export for 2026-09-01..22 put one Search key — used by both repos —
at ~103 requests a day, ~2,260 for the period, already past $10 at list price. This file said
a normal day was ~16. The feed's own log accounts for ~47 a night, which left the podcast at
40–55, above the 28 its two ceilings allow: `_filter_sparse_news_articles` searched through
`_brave_search` directly, once per thin article, and body fetching stops at 40 of a ~80-article
pool, so everything past #40 bought an unmetered search. It now tries the feed's free
`_excerpt` first and charges the rest to the SEARCH budget. **Only the two rate-limit wrappers
may call `_brave_search`.** Read the per-key export in the Brave dashboard before trusting any
figure in this section, including this one.

**A 402 is a wall and closes that meter for the run** (`_is_brave_billing_wall`,
`_trip_brave_wall(error, meter)`, `_brave_walled(meter)`). Unlike a 429 a 402 has no throttle
reading — Brave words it plainly, `current_spend` past `usage_limit`. The Answers endpoint had
disabled itself on a rejection since it was written; Search had no equivalent, so on 2026-08-29
the Search plan hit its cap (then $15) on the **first call of the run** and the pipeline made 17
more, every one refused. The wall is checked inside `_brave_search` rather than in the rate-limit
wrappers, so the paths that call straight through (`_resolve_script_questions_with_brave`) get
it too.

**Two plans means two keys.** A Brave subscription token is scoped to one plan — subscribing to
a second requires generating a key under it — so the Search token does not authenticate against
Answers. `_brave_answers_key()` reads `BRAVE_ANSWERS_API_KEY` and falls back to
`BRAVE_SEARCH_API_KEY`, which is what every deployment had set and what is right while one plan
serves both endpoints. **`_brave_summarize` resolves its own key rather than taking the
caller's**: every caller in the pipeline holds the Search token, so passing it through was how a
wrong-subscription request would have looked deliberate. A 401/403 is a verdict on the key
(`_is_brave_auth_failure`), not on the payload — it disables the endpoint without spending the
second request shape, and the degradation names the env var to set rather than reporting an
outage.

**The two walls are separate because the two plans are.** They shared one flag while they
shared one meter, and keeping that after the split would cost an episode its research twice
over — the 2026-08-29 Search cap would have closed an Answers plan that had just been paid for.
**A spent meter is now a reason to ask the other one, not to give up:** once the Search plan
is out — walled, or over its deep-dive budget (`_brave_deep_dive_open`) —
`_web_search_tool_executor` routes every query to Answers regardless of the `mode` the model
asked for, and snippets remain the documented fallback when Answers is the one that is out.

**A dead search endpoint must not be reported as an editorial finding.** The agentic research
pass's only tool is web search, so with *both* meters closed it is skipped rather than run to a
`NONE` it was always going to reach — on 2026-08-29 it made four refused searches and printed
`No research warranted for this deep dive`, which reads as a judgment about the material. One
meter going out is not that, since the executor asks the other, so both the pre-gate and the
mid-pass `NONE` attribution read `_brave_research_available()` rather than a single flag.

**Three budgets, because the three kinds of call are not worth the same.**

| Budget | Path | Nature |
|--------|------|--------|
| `BRAVE_SEARCH_CALL_LIMIT` | `_fetch_article_body` thin-body backfill | **Speculative** — runs over up to 40 *pre-curation* candidates, of which ~15 air |
| `BRAVE_DEEP_DIVE_CALL_LIMIT` | research, deep-dive enrichment, script-question resolution | **Demand-driven** — runs on material already selected |
| `BRAVE_ANSWERS_CALL_LIMIT` | `_brave_summarize` — the same demand-driven paths, on the other plan | **Credit-bound** — one small monthly credit to spread over ~30 days of runs |

The first two were one counter until 2026-08-29 (`_brave_deep_dive_rate_limit` was written for
this and never called), and **the speculative path runs first** — so any single limit would have
been spent entirely on backfill for stories the roundup then dropped, before the deep dive
asked for anything. Splitting them is what makes a limit safe to set at all; both defaulted
to `0` (disabled) and bounded nothing. The defaults (12/16) bound a runaway day rather than a
normal one — 2026-08-29 used 10 and ~6 — so a budget that bites is a signal the pool was
unusually thin, and it says so in the run report. The demand-driven ceiling went 10→16 for the
election roster sweep (`EVENT_RESEARCH_SEARCH_LIMIT`, see the `event_focus` section), which
fires only on a day whose `event_focus` carries a `research` brief; on every other day nothing
asks for the headroom.

**Answers is never reached from the speculative path.** A synthesized prose answer is the wrong
instrument for thin-body backfill and the expensive one to run over 40 pre-curation candidates,
so only the demand-driven callers ask it. Its budget counts **every request sent**, not each
query answered — a shape probe is metered like an answer — and the default of 8 comes to ~250
queries and ~$0.99 in query fees on a normal month, ~750 and ~$2.98 on a month full of
triple-cron days. **The token half of that price is unmeasured, and it is the whole headroom
left in the credit**, so every call logs the `usage` block Brave returns
(`_log_api_call("brave-answers", …)`); refit the limit off a measured month the way
`_SPEECH_RATE_FITS` was refitted from the sidecars, not off appetite.

**The remaining lever is structural, not a limit:** the backfill spends up to two queries per
article (title, then URL) across 40 candidates before curation cuts to 15. Moving it after
curation is not free — `theme_adjacent` classification reads the body — so it is a real
change, not a config tweak.

## Daily Review → Roadmap (`episode_review.py`, `podcasts/roadmap_ledger.json`)

The review narrates each night's run; the distillation turns it into work. That distillation
was being done by hand (7d3fd10, four reviews in), which is the part that stops happening.

**Recurrence is the signal, and it is counted locally.** What made the hand-written section
worth reading was not any one night's narrative — it was that the same items came back. So
the Claude call is narrow: one day's labelled facts in, candidate findings out. The dedup,
the counting and the rendering are Python, because they are arithmetic and a model that can
restate a number can also restate it wrong (the same reason `render_numbers_table` is
templated). One Haiku call a night, ~3.5k input tokens, schema-constrained via
`json_output_config`.

**An item reaches ROADMAP.md on its `ROADMAP_MIN_OCCURRENCES`'th sighting, not its first.**
One bad night is an incident. This is also what makes "return an empty list" a safe answer
for the model, and the prompt says so twice — a distiller that must find something finds
something, and a roadmap that grows every night is one nobody reads.

**The file is read before it is written.** `episode_review.py` owns only what lies between
`<!-- reviews:begin -->` and `<!-- reviews:end -->`; everything else in ROADMAP.md is
untouched, and a file missing the markers is left alone entirely. A human answers by checking
a box: `harvest_checked` closes that item and resets its counter, so tomorrow's review
mentioning it again cannot reopen it — but a problem that is genuinely still happening earns
its way back after `ROADMAP_MIN_OCCURRENCES` more sightings. Never blocklisted, never
resurrected on one mention.

- **Seeding, not competing.** `parse_section` is the inverse of `render_section`, so the
  hand-written items became the ledger's first entries on first run rather than being
  duplicated by a second list underneath them. It also means no id is ever written into the
  markdown — an item is matched back by its title.
- **Findings are keyed on signals, because ids and titles both drift.** The model coins the
  id, and the same finding came back as `credit-balance-preflight` and
  `credit-balance-not-usage-limit` in testing. Title matching was the fallback, and titles
  carry the night's numbers: by 2026-09-23 the ledger held three items for the Brave body
  budget ("48 roundup articles", "55 roundup articles", "recurring"), five for deep-dive
  citations and seven for script expansion, 24 open in all. `run_signals` reads stable keys off the facts —
  `degraded:<segment>` for each `degrade()` row, `short-script`, `citations:<segment>` below
  `CITATION_FLOOR`, `ai-tells-shipped`, `voice-ratio` outside `VOICE_RATIO_BAND`,
  `run-failed` — and the schema pins each finding's `signal` to that day's set plus `other`.
  `_match` goes signal, then id, then a `difflib` ratio ≥ 0.72 on the title; a signalled
  finding never title-matches an item under a different signal.
- **The first sighting's wording is kept for the life of the item.** A detail rewritten
  nightly is a daily diff on a file nobody asked to change. For the same reason the block is
  in ledger order rather than sorted by recurrence, and its header dates the *reviews that
  produced the items shown* rather than the run — dating it by the run put a one-line diff on
  ROADMAP.md every night, which is how a generated file teaches its reader to skip it.
- **An item closes when its signal stops appearing** — `SIGNAL_QUIET_DAYS` (3) days absent
  from the facts, not when the model stops mentioning it; and a present signal keeps its item
  open on nights the model says nothing. A day whose log could not be read closes nothing: no
  facts is not a fix. Items without a signal fall back to `ROADMAP_RETIRE_DAYS` (14) of
  silence, **pending ones included** — twenty single sightings from August were still in the
  prompt in late September. Only items the tool wrote (`source: "review"`) retire; seeded and
  hand-written items are exempt, a human wrote them and only a human closes them. A retired
  item stays in the ledger with its count restarted, and `ROADMAP_MIN_OCCURRENCES` new
  sightings bring it back. The prompt shows only the `_CLOSED_SHOWN` most recent closed items.
- **The review's facts about runs must match the schedule.** Until 2026-09-23 the trigger
  labels still mapped the retired three-cron ladder: the backstop read as "Fallback 2, 291
  minutes late", the review's own run as a trigger that never finished, and the Worker's
  rungs as manual dispatches — and the most-sighted roadmap item (ten reviews) was that
  mislabelling. `_trigger_label` now reads the event: `schedule` is the backstop, a dispatch
  within `DISPATCH_WINDOW_MINUTES` of a rung is the Worker, anything else is a person; the
  row for `GITHUB_RUN_ID` is marked as the review's own. When there is no successful run the
  log comes from the newest failed one. The backstop passes `--skip-if-reviewed`, so a night
  rung 3 already reviewed is not reviewed twice.
- **It runs after the review is on disk**, inside a `try`, and `main()` swallows what escapes.
  A day without a distillation costs the roadmap a day; a distillation that raises would cost
  the review. Skip it with `--no-roadmap`; `--no-llm` and `--dry-run` already imply it.

The `review` job stages `ROADMAP.md` and the ledger alongside the review — a stage that writes
a tracked file that no step stages sits permanently dirty and breaks the next rebase.
