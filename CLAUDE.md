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

### Who starts the run

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
- **`# Weather:`** — gates the spoken weather-provider credit. The weather check is read on
  air in the welcome, and the episode description had credited Open-Meteo since the segment
  existed while the spoken credits never did (2026-08-17). Gated on the flag rather than on
  config, because on a day the fetch fails there is no weather segment to credit. Scripts
  predating this header degrade to `no`.
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

#### Preflighting the money (`check_api_budget`, `_check_tts_budget`)

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
is probed only once OpenAI is already walled**, and Azure is never aborted on (a subscription
has no equivalent cheap probe). Skipping a day that Gemini would have rendered is worse than
the wasted run this exists to prevent, so the abort fires only when nothing left can render.

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
- `phrase_ledger.json` — 21-episode rolling phrase-frequency window + the burned list
- `roadmap_ledger.json` — findings distilled from the daily reviews, with their recurrence
  counts and closed/retired records; renders the managed block of `ROADMAP.md`

### Configuration System (`config_loader.py`)

All content is externalized to `config/` JSON files; loaders are LRU-cached (single load per process). No hard-coded strings — all messaging, personalities, and themes live in `config/`.

| File | Purpose |
|------|---------|
| `podcast.json` | Title, RSS metadata, TRACE accountability scores |
| `hosts.json` | Riley & Casey — bios, voices, personalities, debate stances |
| `themes.json` | 7 rotating daily themes (Mon–Sun), keywords, editorial lenses |
| `super_cycles.json` | Multi-week focus rotations within each daily theme (slug, keywords, lens per focus) |
| `weekly_anchors.json` | Seeded pool of weekly anchor questions (question, dimension, premise, optional `pin_week`) |
| `ai_tells.json` | Hard-banned phrases, `score_script`'s regex families, phrase-ledger tuning, rhythm budget |
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
- 5 Sat: Cariboo Local Affairs (longer episode, 15 articles) — the one **geographic** theme (`geographic: true`), defined by where a story is rather than what it is about
- 6 Sun: Science, Wonder & the Natural World

**The geographic theme.** Saturday is the only theme defined by *where* a story is rather than
what it is about, flagged `geographic: true` with its place names listed in `place_keywords`.
Every other theme can let place names carry theme relevance; this one cannot, because every
candidate in its pool is local by construction — a place-name hit is a constant, not a
discriminator. `_build_theme_subject_keywords` strips the places (and the theme name, which
contributes a place and the bare word `local`) and leaves the civic vocabulary — `council`,
`bylaw`, `zoning`, `budget`, `referendum` — which is what ranks the deep dive and what gates the
roundup's `theme` block. Ranking on locality picked whichever local story named the most towns:
on 2026-08-22 that was a softwood-duty story, a ranching award and a Tyson beef-plant closure,
and the debate that came out of them was a Working Lands debate on a civic-affairs day.

### News Roundup Curation (`_annotate_roundup_blocks`, `_curate_roundup_pool`)

The roundup's story count is derived from **airtime, not appetite**. The segment gets
~1,100–1,300 words of a 3,400-word script, and every story owes the listener what happened,
why it matters and the rural angle — `ROUNDUP_MIN_STORY_WORDS` (70) is the floor that takes.
`NEWS_ROUNDUP_COUNT` (15) is that budget divided by that floor. **A story that cannot be given
its floor is cut, never compressed**, and the prompt states the segment's word target so a
shorter list produces deeper stories rather than a shorter segment.

`NEWS_ROUNDUP_COUNT` bounds the **whole segment, bonus picks included**. It used to bound the
theme pool alone: `_curate_roundup_pool` returned `protected + kept_fill + bonus` and
`generate_podcast_script` then concatenated the full pre-curation bonus list back in. On
2026-08-13 that put 52 stories in a 1,237-word roundup — 24 words each, a headline crawl
("Archaeologists in Sweden uncovered a 9,000-year-old burial. A pistachio butter was recalled.
Ransomware operators are targeting managers."). Every coherence mechanism — blocks, cluster
adjacency, the no-forced-segue rules — ran on the 15 and was bypassed by the 37.

**`all_articles` is the curated pool and is authoritative.** The `bonus_articles` parameter to
`generate_podcast_script` is the *pre-curation* list; concatenating it back in re-admits
everything the cap just dropped.

Blocks, in airing order — curation metadata the hosts never name on air:

| Block | Contents |
|-------|----------|
| `local` | Cariboo/BC place name or regional outlet. Opens the show. Bonus picks are eligible — geography is orthogonal to the feed's theme judgment |
| `theme` / `theme_adjacent` | Net-positive theme relevance; `_adjacent` matches in the body only. Never bonus picks — the feed already made that call |
| discipline groups | Off-theme stories with ≥2 same-field siblings, kept adjacent so the back half plays as mini-arcs |
| `standalone` | Connects to nothing; the weakest material in the segment |
| `kicker` | One standalone, aired last, told properly — the roundup's deliberate closer |

The **kicker** is why cutting the tail is an edit rather than a shortfall. Standalones used to
be read out at a sentence apiece; one of them given real airtime is worth more than ten
mentioned. It reserves its slot before the tail spends the budget, and yields it when the
protected arc alone fills the segment.

**No single discipline cluster may take more than `ROUNDUP_CLUSTER_MAX` (3) slots**, and that
holds whether or not the pool is over the cap. On 2026-08-22 the Cariboo Local Affairs roundup
sat exactly at its cap of 15 and still ran a seven-story US pharma and health-policy cluster
against two local stories and one theme story — which had qualified on the word "local"
("Scientists Saw Strange Spots on Local Fish"). Nothing bounded one field's share of a segment,
so the cap alone let the day's identity be decided by whatever the feed happened to be heavy in.
A cluster is kept adjacent so the back half plays as a mini-arc; past three it stops being an arc
and becomes what the episode is about. The overflow is dropped, never compressed — dropped
articles never reach citations, so dedup lets them resurface on a better-matched day.

Two prompt rules carry the rest: **NO HEADLINE CRAWL** (never stack unrelated stories into one
host turn as one-sentence mentions) and **DO NOT MANUFACTURE CONNECTIONS** — an abstract bridge
that could join *any* two stories ("from one contested piece of land to another", "whoever
controls the categories controls what counts") sounds like insight and carries none. The escape
hatch matters more than the prohibition: if the shared thing can't be named in plain words,
there is no thread, and the hosts just move on.

### Super Cycles (`config/super_cycles.json`)

Each daily theme (except Saturday, deliberately uncycled) rotates through a multi-week **focus** — e.g. Tuesday cycles agriculture → forestry → mining → tourism, one focus per week. Friday runs a 3-week cycle, all other cycled days 4-week. The cycle position is calendar-derived (`(date.toordinal() // 7) % cycle_length` per weekday via `get_focus_for_day`) — stateless, idempotent on re-runs, predictable ahead of time.

- **Selection:** the deep dive prefers focus-matching articles; a thin focus week (<3 matches) degrades to plain theme selection (logged `focus_fallback`). The focus lens is appended to the theme lens in the script prompt.
- **Subtlety:** the focus is deliberately unannounced on air — it shapes selection and emphasis only. Hosts name and acknowledge the weekday theme, never a rotating sub-theme; every focus-derived prompt block carries a do-not-announce instruction.
- **Article holding (`route_articles_for_focus`):** off-theme, non-urgent articles matching an upcoming day within 14 days are held in `podcasts/article_holding.json` and released (flagged `_held_from`, framed as "earlier this week") on that day. Urgent ones (`_boosted_score ≥ 85`) air same-day in the bonus bucket (never deep-dive) and are remembered in the aired-early ledger for an on-air callback when their day arrives. Holding never shrinks the pool below the roundup + deep-dive budget, and **never holds a local story** — local news is the most time-sensitive material in the pool and geography is orthogonal to the rotation (`_is_local_article`, shared with the roundup's `local` block).
  - **Both buckets are routed.** The hold loop used to iterate `theme_articles` alone — the one bucket that by definition holds nothing off-theme. Off-theme material arrives in `bonus_articles`: 72 of it against 8 theme articles on 2026-08-17, so nothing was ever eligible and Monday's roundup aired a PLA-brittleness piece that scored two hits on Wednesday's Maker & Repair focus keywords — enough for the old focus-only matcher, which never saw it.
  - **A slot matches on its theme keywords OR its focus keywords**, and `get_upcoming_day_slots` emits a slot for every upcoming day. Focus-only matching missed whole categories: forestry is a Tuesday theme keyword every week but only reaches a Tuesday slot on the weeks the rotation sits on Forestry, so the 2026-08-17 lumber-tariffs opinion piece scored 0 focus hits on all 14 upcoming days (3 against Tuesday's theme) and had nowhere to go. The theme is the day's standing identity; the focus only narrows it.
  - **Keyword sets that gate a decision are strict** (`_build_strict_theme_keywords`): theme-name words plus the explicit config keywords, never the description prose. `_build_theme_keywords` folds every word of the description in, which is fine for *ranking* — an extra fuzzy hit only moves an article up a list — and wrong for a gate. Saturday's description contributed `that`, `shape`, `everyday` and `life`, so nothing in the pool could read as weak on today's theme and the router had 42 keywords to match a slot on.
  - **A geographic day is never a routing target** (`_is_geographic_theme`, themes.json `geographic: true`). Cariboo Local Affairs is defined by *where* a story is; every other theme is defined by what it is about. Geography is decided by `_is_local_article`, which also exempts local stories from holding, so the day has no import channel to fill and every match it wins is a false one. Its keyword list took the bare word `local` literally: five articles were waiting for 2026-08-22 — New York's housing shortage, a Brooklyn ADU that "follows local and zoning laws", two US drug-pricing pieces, and "8 local AI models that run great on 8GB of VRAM". Two of them aired.
  - **A local story that belongs to another day airs today and defers its deep dive.** It is never held — local news stays the most time-sensitive material in the pool — but when it carries none of today's *subject* keywords and answers an upcoming day's theme, it gets `_no_deep_dive` and an aired-early ledger entry so the callback lands on the day whose question it actually answers. On 2026-08-22 the Cariboo Local Affairs deep dive ran on softwood duties, a ranching award and a Tyson beef-plant closure — Tuesday's episode, aired on Saturday and spent for the week by dedup, because every one of them is local and locality was the whole score.
  - **`_no_deep_dive` is now read.** It was written by the router and read by nothing: `_ensure_deep_dive_substance` was free to swap back into the deep dive exactly what the router kept out of it. `select_deep_dive_from_feed` holds flagged articles back, and restores them only below `DEEP_DIVE_ELIGIBLE_FLOOR` (2) — a debate with no sources is a worse failure than a debate one day early.

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

### Voice and AI Tells (`config/ai_tells.json`, `podcasts/phrase_ledger.json`)

Two mechanisms, because the obvious one had already failed. `script_generation_system`
banned `"[X] is carrying a lot of weight in that sentence"` verbatim for a long time and the
phrase still shipped; `genuinely` reached 146 uses across 30 episodes (~5/episode) without any
single script looking unusual. A longer prose ban list is not the fix, and `score_script`
counting hits into a JSON nobody reads is not enforcement.

**The corpus is one file.** `config/ai_tells.json` holds `hard_banned`, the regex `patterns`
and `soft_patterns` that `score_script` scans, the `ledger` tuning and the `rhythm` budget.
`score_script` falls back to `_FALLBACK_TELL_PATTERNS` when the file is missing — a style file
must never be able to fail a run. **A new pattern family goes in `soft_patterns` unless the
extra Opus escalation is intended and costed:** `soft_patterns` are reported but excluded from
`total_hits`, which gates `OPUS_QUALITY_HIT_THRESHOLD`. Adding two families to `patterns` took
Opus escalation from 2/30 episodes to 5/30 before they were moved.

#### The prompt was teaching the tic

`genuinely` appeared **44 times in `prompts.json` prose** and 146 times in the scripts;
`directly` 31 and 94; `actually` 26 and 405. The model copies its instructions' register, so
the ban and the example sat in the same file. All 44 are gone (deleting the adverb never
changed an instruction's meaning) and `tests/test_ai_tells.py` fails if a hard-banned phrase
reappears in prompt prose outside a quoted ban example. **Check the burned list before writing
prompt copy** — the fastest way to install a new tic is to use it in the instructions.

#### The ledger — the back catalogue as the ban list

`update_phrase_ledger` folds each finished script into a 21-episode window and promotes
anything spiking. Nobody predicted `genuinely`, and nobody should have to predict its
successor: `quietly` (31x/16 episodes) surfaced on its own.

Three filters decide what may be burned, and each exists because the unfiltered version
produced garbage on a backfill of the real 30-episode catalogue:

- **Adverbs only** (`unigram_mode`). Content words are subject matter — a news show says
  "story", "region" and "question" constantly and must keep doing so. Raw frequency burned all
  three. The generated register lives in stance adverbs.
- **Proper nouns never counted.** An n-gram containing a capitalized non-sentence-initial token
  is skipped, so "Williams Lake" and "Cariboo Regional District" can never be burned.
- **`min_repetition_ratio`** (count/episodes ≥ 2). Boilerplate is said once per episode, every
  episode; a tic recurs inside one. This is what keeps the show's own welcome copy
  ("impact our rural communities", 21x/21 episodes) off the list without parsing sections.

**`ngram_sizes` is `[1]` deliberately.** On the backfill, every multi-word phrase clearing the
thresholds was either basic English ("it's a", "rather than") or subject matter ("fire season"),
and banning those in the prompt would damage the script. Division of labour: the ledger
machine-detects the adverb register; multi-word tics are what a human notices, and
`hard_banned` is the channel for naming them.

Promotion requires `phrase in counts` — the window aggregate still holds a phrase for weeks
after the show stops saying it, so promoting off the aggregate alone re-fired daily, reset
`clean_streak`, and nothing could ever retire. A phrase retires after
`retire_after_clean_episodes` clean episodes, which frees the slot for whatever replaced it.
Idempotent on date, so a re-render never double-counts its own episode.

#### Enforcement

`format_burned_phrases_for_prompt()` renders the block into the **dynamic** user prompt
(never the cached system prompt — it changes daily and would defeat the cache), the expand
retry, the cold open and both polish paths. `config_loader.format_static_tell_block()` carries
the config-only half so `generate_bespoke.py` can use it without importing the pipeline — the
same reason `atomic_write_text` lives there. Bespoke built its own prompt and inherited none of
this until then.

`script/tell-scrub` runs **after** `script/cold-open`, deliberately: `generate_cold_open` runs
after every polish pass, so the teaser is the one part of the episode nothing else cleans, and
it is the first thing a listener hears. It sends only the offending sentences to `SCRUB_MODEL`
(Haiku) — a few hundred tokens, against 3,400 words for a re-polish. A rewrite is spliced only
if it is clean and the original still matches verbatim; anything else keeps the original and
`degrade()`s, so a bad rewrite can never be worse than the tic.

#### The rhythm budget

The vocabulary is half of it. 47 words per turn, 53 em-dashes an episode and every turn a
finished paragraph is a fingerprint on its own. The system prompt's `**RHYTHM**` section asks
for what the show should sound like — short turns, one flat unhedged statement, a disagreement
allowed to not resolve — and `score_rhythm` measures exactly those, reporting `over_budget`
into `episode.quality`. It is advisory: nothing blocks on it.

### TTS Providers

**OpenAI (default):** `nova` (Riley) + `echo` (Casey), per-segment synthesis, parallel rendering. Each segment is checked against `_expected_speech_ms` (`369 ms/word − 642 ms`, speed-normalised — fitted to the 688 segments of the ten episodes rendered 2026-08-13..22, whose transcript sidecars carry each segment's real duration) and re-synthesized once below 0.80 of it. **Refit those constants against the sidecars rather than assuming a rate:** the flat 400 ms/word they replace described nothing the show has produced and re-rendered ~14 complete segments a night, each retry landing within 2% of the take it was doubting.

`OPENAI_TTS_MODEL` selects the model, defaulting to **`tts-1`**. The legacy pair
(`tts-1`, `tts-1-hd`) honours the per-host `speed` multiplier from `hosts.json`;
the steerable models (`gpt-4o-mini-tts`) take an `instructions` string instead,
which is what `hosts.json`'s long-dormant `voice_instructions` was written for —
authored, wired through `get_voice_instructions_for_host`, imported, and never
called, because `tts-1` has no parameter to send it to. `_openai_speech_request`
owns that split so the render path and `evaluate_tts.py` build the same request.

#### Why the default came back to tts-1

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
it with `python evaluate_tts.py --section deep_dive --skip-azure --skip-gemini`,
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

#### Per-take checksums

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

The same check runs on whole-section (Gemini/Azure) renders, where it raises into the
existing per-section OpenAI fallback — a silent section is this failure minutes wide.

**Azure Neural TTS (optional, `USE_AZURE_TTS=1`):** Multi-Talker model for coherent prosody across speaker transitions. SSML with `<phoneme>` IPA tags for Cariboo place names. 8,000-char conservative SSML chunk limit. Set `AZURE_TTS_PARALLEL=1` to generate both providers for comparison.

**Gemini multi-speaker TTS (optional, `USE_GEMINI_TTS=1`, wins over Azure):** `gemini_tts.py` renders each section's whole two-host conversation in one `generateContent` call (NotebookLM-style prosody) via REST — needs `GEMINI_API_KEY`; `GEMINI_TTS_MODEL` overrides the default (`gemini-3.1-flash-tts-preview`). A style prompt plus whitelisted `[tag]` stage directions live in `config/prompts.json` under `gemini_tts`; the polish pass only adds tags when Gemini is active, and the OpenAI/Azure paths (and both published transcripts) strip them. Credits on every surface resolve through `_compose_tts_credit()` — **every** provider that actually rendered audio is named, in render order, so a mid-episode fallback reads as "Gemini TTS and OpenAI TTS" rather than picking one. `get_active_tts_provider()` is the *routing* answer (what renders next), which is a different question and must not be used for a credit. Compare providers with `python evaluate_tts.py`.

#### The prompt says where the speech starts

The request is scaffolded rather than prose-led — `### AUDIO PROFILE` (one line per
speaker, from `hosts.json`'s `gemini_audio_profile`), `### PERFORMANCE NOTES` (the
config's `style_prompt` plus the speaker and tag rules this call generates), then
`#### TRANSCRIPT` and nothing but speech below it. Every failure this endpoint has cost
the show is a boundary failure: the cold open read aloud twice (see `CONTINUATION_NOTE`),
a stage direction spoken as dialogue. The marker is the boundary, so the model never has
to infer one from a colon at the end of a sentence.

That does put `Riley: <description>` lines above the marker, which is the transcript's own
shape. It is the format Google documents and the marker is what separates them — but if an
episode ever reads a profile line aloud, that collision is the first thing to change.

Cues are inline `[thoughtfully]` tags now, from the documented vocabulary
(`whitelist`), not invented ones — custom tags read flatter. Hype tags
(`cheerfully`, `enthusiasm`, `gasp`) are deliberately not in it: the show has no
morning-DJ register to reach for. `legacy_whitelist` is strip-only, so the
`(wry)`-style parentheticals in every script already on disk still get cleaned on a
re-render. **The never-speak-a-tag rule is not in `style_prompt`** — it rides with the
tags (`tag_instruction`), so the rung that sheds the style cannot ship tags with nothing
saying they are direction.

**Gemini is the nightly default; OpenAI is its fallback.** The daily workflow's
`tts_provider` input defaults to `gemini` and a scheduled run passes no input, so the show
renders multi-speaker. OpenAI catches it in two places, both automatic: the canary pins
OpenAI for the whole episode before any audio exists when Gemini will not answer, and a
section that fails mid-render falls back per section. Dispatch with `tts_provider=openai`
to skip Gemini for a run.

**The code default is `gemini-3.1-flash-tts-preview`, and it has never answered here.**
It was made the default without the probe this section asks for, and every run from
2026-09-01 spent two 45 s canary read timeouts on it (1082 chars, 45.1 s, unanswered,
twice each night, identical on both dates) before pinning
`GEMINI_TTS_FALLBACK_MODEL` for the show. **Production overrides it via the
`GEMINI_TTS_MODEL` repository variable, set to `gemini-2.5-flash-preview-tts`, and that
works**: on 2026-09-03 the canary passed on the first candidate in 3.4 s.

**Setting that variable then collapsed the model ladder, which is the trap to know
about.** `gemini-2.5-flash-preview-tts` was also the hard-coded default of
`GEMINI_TTS_FALLBACK_MODEL`, so primary and fallback named one model, the canary printed
one candidate, and `_next_model_rung()` returned `None` on every rung — the same hole the
`_model_override` pin opens, reached from the configuration side and just as silent. All
four attempts on that day's cold open re-asked the same model unchanged and the episode
went to OpenAI. `GEMINI_TTS_FALLBACK_MODEL` is therefore resolved against the primary
(`_default_fallback_model`, preference order `2.5-flash` then `2.5-pro`) rather than
hard-coded, and a candidate list of one now `degrade()`s from `canary()` whether or not
the render goes on to succeed.

**Price is not a reason to keep the ladder one model short.** Pro TTS was demoted out of
the fallback slot for costing more than flash; measured 2026-09-03 that holds only for
*input* tokens ($1.25 vs $0.50 per MTok) while **audio output is $10 per MTok on both** —
and audio output is essentially the whole bill. An episode sends ~2.5k input tokens, so
choosing pro over flash as the second model costs a fraction of a cent. `3.1` is the one
that is genuinely more expensive ($1.00 in / **$20** out) and it is also the one that has
never answered, which is why it is not in the preference order.

Before making any new preview the primary, **run `python evaluate_tts.py --probe-models`
(TTS Eval workflow, `probe_models` input) against the 8/15 baseline** and record the
numbers here — the reason 3.1 ran unexamined for weeks is that nobody had a measurement
to argue with. Note the probe only ever measures the two configured models: it cannot
discover a better one, so widening the field is a decision made here, not by the tool.
Check the pinned `speechConfig` voices (`Kore`/`Iapetus` in `hosts.json`) carry over —
voice identity is the last thing to degrade — and refit `READ_TIMEOUT_MS_PER_CHAR` off
the probe's slowest column while the data is in hand.

**The wider model question is still open, and it is about the endpoint, not the model.**
All three Gemini API TTS models (`2.5-flash-preview-tts`, `2.5-pro-preview-tts`,
`3.1-flash-tts-preview`) are *preview*: no SLA, tighter rate limits, two weeks' notice
before withdrawal. Cloud Text-to-Speech carries what appear to be GA-labelled equivalents
(`gemini-2.5-flash-tts`, `gemini-2.5-pro-tts`) on `texttospeech.googleapis.com` — a
different product surface, different auth (service account, not an API key), different
request shape, and a different quota pool from the one throwing our 500s and read
timeouts. That is the only change on the table that would alter the *reliability* rather
than re-rolling the same dice, and it has not been verified against the docs — do that
before costing it.

Google's Interactions API is now GA with `generateContent` marked legacy for speech.
Port to it only if a probe shows `generateContent` is what is holding the model back.

#### Getting Gemini through a whole episode

An episode is 6–9 independent Gemini calls, so per-call reliability compounds — in the week of 2026-08-01, seven of seven episodes fell back to OpenAI at or before the welcome section, and three shipped a Gemini cold open with an OpenAI show. Four mechanisms exist to stop that, in the order they fire:

- **Canary (`gemini_tts.canary()`).** One tiny throwaway synthesis before any audio exists, run from `generate_audio_from_script`. A failed canary pins OpenAI up front, so a provider that is down costs one tiny call rather than a section's whole retry ladder — on 2026-09-02 that ladder spent 290 s of the render learning what a 3 s probe would have said. It probes the fallback model too, and pins it only if the primary is the one that's down.
  **It does not make a mixed-voice episode unrepresentable, and it never did.** It front-runs only the *pre-render* case; the per-section fallback still leaves already-rendered sections in the earlier provider's voice, which is what 2026-09-01 shipped (Gemini cold open and welcome, OpenAI from the news on). **A mixed episode is an accepted outcome** — re-rendering good audio to force one voice spends the render clock and the OpenAI budget on nothing a listener asked for. What is *not* acceptable is a mixed episode that does not say so: `record_tts_render()` tracks every provider that spoke and `_compose_tts_credit()` names all of them, on the spoken credits, the citations sidecar and the episode description alike. Each candidate gets `CANARY_ATTEMPTS` probes, but only against a failure carrying no verdict — the same rule `_carries_no_shape_verdict` applies to the ladder. Every canary failure of the week of 2026-08-17 was a read timeout against an endpoint the 2026-08-13 probe had measured at 8/15 calls answering, so a single attempt was a coin flip that moved whole episodes onto OpenAI's voices; a tokenized rejection is still taken at its word and never re-asked.
  **The probe has to ask the question the render asks.** It was one single-speaker turn at a 30 s leash, vouching for multi-speaker sections that get 75–120 s: on 2026-08-28 it passed and the same model then failed three multi-speaker sections in a row, so the episode was pinned to a provider that could not render it. `CANARY_SEGMENTS` is now two turns, one per speaker (still under the 10 words `_duration_ratio` needs before it will judge a clip), and `CANARY_READ_TIMEOUT` is `READ_TIMEOUT_MIN_S` — **a canary must never be stricter than the render**, or it fails endpoints the render would have waited out. That coupling means raising the floor raises what a *dead* night costs before the render starts: at 75 s, two candidates × `CANARY_ATTEMPTS` is ~310 s against ~190 s at 45. It stays coupled anyway — a probe that gives up sooner than the render is the more expensive mistake, because it spends the whole episode's voices rather than five minutes.
  **It still only vouches for the multi-speaker shape**, and the cold open is usually one turn, which takes the `singleSpeakerVoiceConfig` branch: on 2026-09-02 the probe passed in 3.3 s and the single-speaker cold open that followed was rejected twice. Probing both shapes was considered and left out — a rung-0 rejection is not a dead provider (the same section succeeded one rung later on 2026-09-01), so vetoing Gemini on one would cost more Gemini days than it saves. `_ladder_summary()` names the shape in the degradation instead: the same information, on the day it matters, for no extra call.
- **Retry ladder (`RETRY_LADDER`).** Each rung changes the *shape* of the request, not just the seed — `finishReason: OTHER` returns `promptTokenCount == totalTokenCount`, i.e. a rejection of what was asked, which reseeding cannot fix. Rungs shed the continuation note, then the audio profile and style block, then the tags. Backoff (0/15/45/90/90 s) is sized to outlast the minutes-long capacity windows the old 5 s/10 s ladder always died inside.
- **Model ladder.** `GEMINI_TTS_FALLBACK_MODEL` is tried at rung 3, *before* the primary model with a bare transcript: voices are pinned by `speechConfig` on every rung, so a model change keeps the hosts sounding like themselves while a stripped prompt loses the direction. It resolves to whichever of `2.5-flash` / `2.5-pro` the primary is not, so this rung exists no matter what `GEMINI_TTS_MODEL` names — the 2026-09-03 collapse is the failure that rule exists to prevent, and it cost a whole episode's voices while every log line looked healthy.
- **Failure-shape routing (`_carries_no_shape_verdict`).** The rung order above assumes a *rejection*. Exactly one failure here is one: `finishReason: OTHER`, which returns `promptTokenCount == totalTokenCount` — accepted, tokenized, refused. A read timeout, a dropped connection, a 429 and a 5xx all carry no verdict on the prompt, so on one the ladder goes straight to a rung that changes the model, and when there is none left (`_model_override` pinned one) it re-asks the same full-quality request rather than shedding anything. Prompt-shedding is not a retry strategy for a request that was never read: the two shedding rungs cost a full read timeout each and pushed the model rungs out of `SECTION_BUDGET_S` entirely — three timeouts spend 120+15+120+45+120 = 420 s, the budget exactly, which is why every August 2026 episode fell back to OpenAI mid-show and no model rung ever ran. The budget still allows three attempts; the change is *what* they ask, not how many there are.
  **A 429 is a rate limit until proven otherwise — unless it names a spend cap.** Gemini answers an ordinary per-minute throttle with the same 429 `RESOURCE_EXHAUSTED` it uses for a spent quota, and taking it as a verdict gave both canary candidates away on one throttled call each on 2026-08-26 (two of three crons). A genuinely spent quota costs one extra tiny probe before it is believed; refusing to re-ask a rate limit costs an episode its voices. Note the asymmetry with `_billing_wall()` on the *script* side, which must match on credit wording and never on the status code — there the cost of reading a throttle as a wall is a skipped day.
  **The one 429 that is a verdict is a spend cap** (`_is_spend_cap`, `SpendCapError`), and like `_billing_wall()` it is matched on the *wording* — `"Your project has exceeded its monthly spending cap"` — never the status. A capped project refuses every model on it until a human raises the cap or the month rolls over, so no rung, no backoff and no model rung reaches past it: `_carries_no_shape_verdict` returns False for it, the ladder hands the section back immediately, and `canary()` skips the remaining candidates instead of asking the same wall twice per model. On 2026-08-29 that wall cost four probes across two models, and would have cost four a night until Sept 1. The degradation names the cap, because "did not answer the pre-flight check" reads like a flaky endpoint and this one needs a person.
  The 2026-08-13 probe (`--probe-gemini`, welcome section, 3 calls/rung) measured 8/15 calls succeeding, spread evenly across all five rungs — flaky endpoint, not a rejected prompt, and not a dead primary model. That is why a timeout is worth re-asking unchanged, and why the canary's verdict on the primary should be read as "slow right now", not "down".
- **Budgets, and the leash that spends them.** `SECTION_BUDGET_S` bounds one chunk's ladder; `set_render_deadline()` (called with `GEMINI_RENDER_DEADLINE_S`) bounds all Gemini work in a render, so a provider that dies *after* the canary passed cannot eat the 40-minute render step one section at a time. `_budget_allows` reserves the attempt's own read timeout as well as its backoff, so a retry that cannot finish inside the budget is never started.
  **`REQUEST_READ_TIMEOUT` was flat and that is what bounded the ladder's reach.** One 120 s leash covered a 354-char cold open and an 8 500-char chunk alike, so at 420 s a section afforded **two attempts** — and against the measured ~53%-per-call endpoint, two attempts is ~78% per section and **~17% across a seven-section episode**. That arithmetic, not any one outage, is why a whole Gemini episode kept not happening. `_read_timeout_for(segments)` now scales the leash by transcript chars, clamped to `[READ_TIMEOUT_MIN_S, READ_TIMEOUT_MAX_S]` = `[75, 120]`: the ceiling is the old flat value, so **the largest chunks wait exactly as long as they always have and the change is only ever a cut for the small ones**, which is where the budget was being burned on silence. Five attempts per section takes the episode to ~85%.
  **The constants are provisional and instrumented for refit.** They are fitted to one measured take (a 1 173-char request answered in ~16 s on 2026-08-28) plus the chunk ceiling, at roughly 3x observed. Every call now logs `latency=` and `limit=` alongside `chars=`, success and failure alike — pair those across a few episodes and refit `READ_TIMEOUT_MS_PER_CHAR`, the same way `_SPEECH_RATE_FITS` was fitted from the transcript sidecars. Nothing recorded latency before, which is how a flat 120 s survived unexamined for the life of the integration.
  **The floor was the stale half of that fit, and it is what a small section actually gets.** The formula wants 15.9 s for a 398-char cold open and the clamp lifts it, so the floor was never "3x observed" — it was 3x the single take the constants were fitted to. The same request measured **27.9 s** on 2026-09-03 and two of that section's four attempts died at exactly 45.0 s and 45.1 s, so the floor is now **75 s**. This is a hypothesis, and the counter-evidence is in the 2026-08-13 probe: 7 of 15 calls failed against a flat 120 s leash, so a longer wait does not convert every timeout into a take. What it buys is that a slow-but-alive call stops being indistinguishable from a dead one at exactly the leash.
  **`SECTION_BUDGET_S` moved with it (420 → 540), because the budget and the leash trade against each other.** At 420 s a 45 s-leash section afforded four attempts (0+45, 15+45, 45+45, 90+45 = 330 s); at 75 s the same budget affords three, so raising the timeout alone would have bought longer waits by silently spending an attempt. 540 s keeps four (450 s) and still sits well inside `GEMINI_RENDER_DEADLINE_S` (1500 s), which is the ceiling that actually protects the render step. **Move these two together or not at all.**

**Ordering rule:** degrade delivery nuance before voice identity. Anything that changes *who the hosts sound like* is the last resort — a model change (which keeps the pinned `speechConfig` voices) always comes before dropping the show onto OpenAI's. That ordering is why the whole-episode decision is made up front where it can be; it is not a promise that every episode is single-provider, which the per-section fallback has never been able to keep. When the episode does end up mixed, the credit says so.

#### Nothing speakable in the prompt that isn't meant to be spoken

Sections used to be primed with the previous section's *verbatim* transcript tail (400 chars) under a `CONTEXT — already spoken immediately before this, do not repeat` header, so delivery continued instead of resampling cold. On 2026-08-17 the welcome section read the entire cold open aloud before its own first line and the episode opened with the teaser twice: 92.8 s of audio for a 969-char transcript, against 65–76 s on the six prior Gemini episodes, an excess matching the 25.5 s cold open.

The prompt shape was the same on all seven days and so was the model, so there is no wording that makes it safe — asking a text-to-speech model not to say words you have handed it is a request it honours most of the time. It is now `continuing: bool` and a fixed `CONTINUATION_NOTE` directive (`gemini_tts`), which carries the same "open mid-flow" intent with nothing quotable in it. **Never reintroduce prior dialogue into a TTS prompt.**

`gemini_tts` cannot import `degrade()` without a circular import, so it records degradations and the render path drains them via `_report_gemini_degradations()`. **A new fallback in `gemini_tts` must append to `_degradations`** or it will not reach the run report.

Two probes, asking different questions — both are `TTS Eval` workflow inputs that write their table to the job summary, and both spend real budget:

- `--probe-gemini` (`probe_gemini`) asks **what shape** Gemini will accept: the same text with progressively less prompt around it. Rung 0 failing while a later rung passes names the element Gemini is rejecting; every rung failing equally is an outage or a quota wall, not a prompt problem.
- `--probe-models` (`probe_models`) asks **which model answers, and how fast**: N real section requests per candidate, reporting answer rate and median/slowest latency. This is the one to run before trusting a nightly to a new model, and its slowest column is what `READ_TIMEOUT_MS_PER_CHAR` should be refitted against.

**`GEMINI_TTS_MODEL` is a repository *variable*, never a secret.** A model name is not a credential, and sourcing it from `secrets` made GitHub mask it everywhere — on 2026-08-28 the log could only say a request went unanswered on `***`, so the run report could not name the model that failed. `canary()` also prints the candidate list before probing it, so a withdrawn or misspelled model name reads as itself rather than as an outage.

### Brave spend (`_brave_search`, `_BRAVE_WALLS`, the three call budgets)

**Two plans, two meters** (since 2026-08-29). Search is $5/1000 requests against a
self-imposed **$10 monthly** spend limit — 2,000 requests — and Answers is $4/1000 queries plus
$5/MTok each way, held to its **monthly free credit** with no paid overage. Both refuse past
their limit rather than billing on, so a 402 can arrive on any day of the month. The pipeline's
job is to spend each month's calls on work that reaches the listener, and to stop instantly
once one of them has nothing left.

**The fallback crons are the multiplier that spends a month.** The workflow fires at 1:05, 2:05
and 3:05 Pacific; the later two exit on the idempotency check and cost nothing — unless the
first run failed *after* spending, when the day costs three full sets of calls (2026-08-23). A
normal day is ~16 Search requests (~480/month, ~$2.40 of the $10); triple-cron days are the
pathology the per-run ceilings exist to bound, not the ordinary one.

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
to `0` (disabled) and bounded nothing. The defaults (12/10) bound a runaway day rather than a
normal one — 2026-08-29 used 10 and ~6 — so a budget that bites is a signal the pool was
unusually thin, and it says so in the run report.

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

### Daily Review → Roadmap (`episode_review.py`, `podcasts/roadmap_ledger.json`)

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
- **Ids drift, titles do not.** The model coins the id, and the same finding came back as
  `credit-balance-preflight` and `credit-balance-not-usage-limit` in testing. `_match` tries
  the id, then a `difflib` ratio ≥ 0.72 on the title.
- **The first sighting's wording is kept for the life of the item.** A detail rewritten
  nightly is a daily diff on a file nobody asked to change. For the same reason the block is
  in ledger order rather than sorted by recurrence, and its header dates the *reviews that
  produced the items shown* rather than the run — dating it by the run put a one-line diff on
  ROADMAP.md every night, which is how a generated file teaches its reader to skip it.
- **Retirement is only for items the tool wrote** (`source: "review"`), after
  `ROADMAP_RETIRE_DAYS` (14) of silence — a quiet week is not a fix. Seeded and hand-written
  items are exempt: a human wrote them, only a human closes them. Retired items stay in the
  ledger and a recurrence puts them back.
- **It runs after the review is on disk**, inside a `try`, and `main()` swallows what escapes.
  A day without a distillation costs the roadmap a day; a distillation that raises would cost
  the review. Skip it with `--no-roadmap`; `--no-llm` and `--dry-run` already imply it.

The `review` job stages `ROADMAP.md` and the ledger alongside the review — a stage that writes
a tracked file that no step stages sits permanently dirty and breaks the next rebase.

### The Sunday Meta Moment (`get_weekly_changelog`, `generate_meta_moment_text`)

One Haiku call turns the week's commit subjects into a short Riley/Casey segment about
changes to the show itself. The input is `git log --since=7d` over `GENERATION_PATHS`
(`review_scripts.py`) — subject lines only, minus merge commits and minus the embargoed
delivery surfaces in `podcast.json`.

**The failure mode is invention, and the old prompt demanded it.** A week's commits are
usually plumbing — "Split the Brave meters: Answers is its own plan now" has no
listener-facing form at all — while the prompt asked for the 3-4 most listener-noticeable
changes and 320-400 words regardless. A model asked for four good answers where none exist
supplies four: three of the four Sundays to 2026-08-30 aired a "weekly inspiration harvest"
that was never committed, and 08-30 backed it with a Ktunaxa Nation story that did not
exist. The instruction against it ("never fabricate names or details not in the commit
list") had been in the prompt the whole time.

- **NONE is a first-class answer**, stated twice, with the segment lengths tiered by how
  many entries genuinely reach a listener. Same shape as the roadmap distiller's empty
  list, and as the weekly anchor's escape hatch: a segment that must find something finds
  something.
- **The reply cites before it speaks.** `COVERED: <commit line>` above the dialogue,
  matched back against the real subjects at `difflib` ratio ≥ 0.9 (`_meta_moment_covered`).
  A change the model made up has no line to copy.
- **Names are checked, not requested** (`_meta_moment_unknown_names`). Any capitalized word
  the dialogue can't source from the commit list, the host roster, the show title or the
  territory acknowledgment drops the segment. Sentence-initial words, possessives and
  quoted asides are excluded — verified against the four aired segments, which flag only
  the fabrications. This is the phrase-ledger trade: measure the output instead of
  lengthening the ban.
- **A dropped segment `degrade()`s under `script/meta-moment`.** Silently vanishing weekly
  is the failure this guard would otherwise introduce.
- **Nothing listener-facing goes in the prompt unconditionally.** The sentence telling the
  hosts to say "transcripts in your podcast app" handed them a topic, and they used it in a
  week with no transcript commit; it now appears only when a commit earns it. The prompt
  teaching the tic is the same failure `genuinely` documented above.
- The last turn hands off **in general terms** — the Meta Moment is spliced ahead of the
  community spotlight and has not been told what follows it. On 2026-08-30 it previewed a
  deep-dive story two segments away, and invented that too.

### Sibling Repository

`super-rss-feed` scores and categorizes articles, publishing `feed-podcast-{dayname}.json` to its GitHub Pages URL. The podcast generator fetches this at runtime. Deploy order matters: super-rss-feed must deploy before the podcast generator runs. See `SIBLING_REPOS.md` for integration details.

## API Cost Discipline

Treat API budget as a first-class constraint on every change.

- **Default to the cheapest model.** Escalate (Haiku → Sonnet → Opus) only when demonstrably required — justify explicitly. Opus is only used for review escalation when deep-dive sourcing is thin (<3 articles). Opus 5 costs under 2x Sonnet 5 ($5/$25 vs $3/$15 per MTok), not the ~5x the tier gap once did — the gate stays anyway, since the escalation buys nothing on a well-sourced day.
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
