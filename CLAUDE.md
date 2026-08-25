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

**Gemini multi-speaker TTS (optional, `USE_GEMINI_TTS=1`, wins over Azure):** `gemini_tts.py` renders each section's whole two-host conversation in one `generateContent` call (NotebookLM-style prosody) via REST — needs `GEMINI_API_KEY`; `GEMINI_TTS_MODEL` overrides the default flash model. A style prompt plus whitelisted `(cue)` stage directions live in `config/prompts.json` under `gemini_tts`; the polish pass only adds cues when Gemini is active, and the OpenAI/Azure paths strip them. Credits on every surface resolve through `get_active_tts_provider()` — the provider that actually rendered the audio wins (an OpenAI fallback is credited as OpenAI). Compare providers with `python evaluate_tts.py`.

**Newer model, untried here:** `gemini-3.1-flash-tts-preview` exists, and Google's
Interactions API is now GA with `generateContent` marked legacy for speech. Most of
the reliability apparatus below was built against the 2.5 preview endpoint answering
8 of 15 calls (2026-08-13 probe), so a newer model may make much of it unnecessary.
Trying it costs no code: `GEMINI_TTS_MODEL` is already plumbed through
`gemini_tts.py` and both workflows, so point the repo variable at it and run
`python evaluate_tts.py --probe-gemini` against that 8/15 baseline first. Check the
pinned `speechConfig` voices (`Kore`/`Iapetus` in `hosts.json`) carry over before
adopting — voice identity is the last thing to degrade. Port to the Interactions
API only if the probe shows `generateContent` is what is holding the model back.

#### Getting Gemini through a whole episode

An episode is 6–9 independent Gemini calls, so per-call reliability compounds — in the week of 2026-08-01, seven of seven episodes fell back to OpenAI at or before the welcome section, and three shipped a Gemini cold open with an OpenAI show. Four mechanisms exist to stop that, in the order they fire:

- **Canary (`gemini_tts.canary()`).** One tiny throwaway synthesis before any audio exists, run from `generate_audio_from_script`. It decides the provider for the whole episode: a failed canary pins OpenAI up front, which is what makes a mixed-voice episode *unrepresentable* rather than merely unlikely. It probes the fallback model too, and pins it only if the primary is the one that's down. Each candidate gets `CANARY_ATTEMPTS` probes, but only against a request that went *unanswered* — the same rule `_is_transport_failure` applies to the ladder. Every canary failure of the week of 2026-08-17 was a read timeout against an endpoint the 2026-08-13 probe had measured at 8/15 calls answering, so a single attempt was a coin flip that moved whole episodes onto OpenAI's voices; a rejection is still taken at its word and never re-asked.
- **Retry ladder (`RETRY_LADDER`).** Each rung changes the *shape* of the request, not just the seed — `finishReason: OTHER` returns `promptTokenCount == totalTokenCount`, i.e. a rejection of what was asked, which reseeding cannot fix. Rungs shed the context tail, then the style prompt, then the cues. Backoff (0/15/45/90/90 s) is sized to outlast the minutes-long capacity windows the old 5 s/10 s ladder always died inside.
- **Model ladder.** `GEMINI_TTS_FALLBACK_MODEL` (default pro TTS) is tried at rung 3, *before* the primary model with a bare transcript: voices are pinned by `speechConfig` on every rung, so a model change keeps the hosts sounding like themselves while a stripped prompt loses the direction. Pro costs more than flash, which is why it sits behind three primary failures.
- **Failure-shape routing (`_is_transport_failure`).** The rung order above assumes a *rejection*. A read timeout or dropped connection carries no verdict on the prompt, so on one the ladder goes straight to a rung that changes the model, and when there is none left (`_model_override` pinned one) it re-asks the same full-quality request rather than shedding anything. Prompt-shedding is not a retry strategy for an unanswered request: the two shedding rungs cost 120 s each and pushed the model rungs out of `SECTION_BUDGET_S` entirely — three timeouts spend 120+15+120+45+120 = 420 s, the budget exactly, which is why every August 2026 episode fell back to OpenAI mid-show and no model rung ever ran. The budget still allows three attempts; the change is *what* they ask, not how many there are.
  The 2026-08-13 probe (`--probe-gemini`, welcome section, 3 calls/rung) measured 8/15 calls succeeding, spread evenly across all five rungs — flaky endpoint, not a rejected prompt, and not a dead primary model. That is why a timeout is worth re-asking unchanged, and why the canary's verdict on the primary should be read as "slow right now", not "down".
- **Budgets.** `SECTION_BUDGET_S` bounds one chunk's ladder; `set_render_deadline()` (called with `GEMINI_RENDER_DEADLINE_S`) bounds all Gemini work in a render, so a provider that dies *after* the canary passed cannot eat the 40-minute render step one section at a time. `_budget_allows` reserves the attempt's own read timeout as well as its backoff, so a retry that cannot finish inside the budget is never started.

**Ordering rule:** degrade delivery nuance before voice identity. Anything that changes *who the hosts sound like* is the last resort, which is why the whole-episode OpenAI decision is made up front rather than drifted into mid-show.

#### Nothing speakable in the prompt that isn't meant to be spoken

Sections used to be primed with the previous section's *verbatim* transcript tail (400 chars) under a `CONTEXT — already spoken immediately before this, do not repeat` header, so delivery continued instead of resampling cold. On 2026-08-17 the welcome section read the entire cold open aloud before its own first line and the episode opened with the teaser twice: 92.8 s of audio for a 969-char transcript, against 65–76 s on the six prior Gemini episodes, an excess matching the 25.5 s cold open.

The prompt shape was the same on all seven days and so was the model, so there is no wording that makes it safe — asking a text-to-speech model not to say words you have handed it is a request it honours most of the time. It is now `continuing: bool` and a fixed `CONTINUATION_NOTE` directive (`gemini_tts`), which carries the same "open mid-flow" intent with nothing quotable in it. **Never reintroduce prior dialogue into a TTS prompt.**

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
