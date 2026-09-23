# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

It holds the **standing rules**. The incidents and reasoning behind them live in
[`docs/decisions/`](docs/decisions/), one file per area, linked from each section below.
Read the linked file before changing the code it describes. When you add a rule, keep it
here as one or two lines and put the story in the decision file, not here.

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

# Preview the weekly anchor schedule without spending or writing state
python weekly_anchor.py --preview 12
```

Tests require no API keys — `tests/conftest.py` installs lightweight stubs for `anthropic`, `openai`, `pydub` and `cohere` at import time. CI runs the suite on every push and PR (`.github/workflows/tests.yml`).

**State-file isolation:** the live memory/state JSON files in `podcasts/` are production data committed daily by CI — code under test that persists state will rewrite them in place. An autouse fixture in `tests/conftest.py` already redirects `psa_selector.PSA_STATE_FILE` to a tmp copy; any new test (or code path) that touches a `podcasts/` state file must get the same treatment (monkeypatch the path/`PODCASTS_DIR` into `tmp_path`). After any local test run, check `git status` — a modified state file is test leakage to be reverted (`git checkout -- podcasts/<file>`), never committed.

## Architecture

**Cariboo Signals** is a daily two-host show (Riley & Casey) covering rural BC tech and community topics. It is public on Apple Podcasts and Spotify. The pipeline runs on GitHub Actions and deploys audio + RSS to GitHub Pages (audio also to R2).

**Daily run (`podcast_generator.py`):**
1. Idempotency check — exits if today's episode already exists in the RSS feed
2. Pull scored articles from sibling repo `super-rss-feed` (`feed-podcast-{dayname}.json` from its GitHub Pages URL)
3. Deduplicate against the last 7 days of citations (`dedup_articles.py`, optionally Cohere embeddings)
4. Cluster same-story articles; super-cycle routing (release matured held articles, hold off-theme ones for their day); select roundup stories and deep-dive articles
5. Claude writes the two-host script, then polishes it. Scripts under `TARGET_SCRIPT_WORDS` get one expand retry; under `MIN_SCRIPT_WORDS` after that the run aborts. Target runtime 22–25+ min
6. Write citations JSON and every memory/state file; save the script to `podcasts/podcast_script_{date}_{theme}.txt` — **end of the script stage**; the workflow commits here
7. OpenAI TTS renders each speaker turn in parallel
8. pydub assembles: cold open (before the music) → intro → welcome → interval → news roundup → interval → deep dive → outro
9. Transcript + RSS entry, commit, deploy to `gh-pages`

### Operations — see [docs/decisions/operations.md](docs/decisions/operations.md)

**Scheduling.** A Cloudflare Worker (`cloudflare/scheduler/`, shared with `super-rss-feed`) dispatches the 1:05 / 2:05 / 3:05 AM Pacific ladder with a `run_slot` input; GitHub's cron is best-effort and a late run misses the listener's morning.
- The Worker **starts** runs; `check-episode` alone decides whether one is needed. Never add a second "did today ship?" check.
- Keep the one GitHub cron (`5 11 * * *`) as the backstop for the Worker being down. Don't remove it; don't add ladder slots back.
- Anything that needs to know which rung it is on reads `inputs.run_slot`, never `github.event.schedule`.
- Deploy the Worker with **Actions → Deploy Cloudflare Scheduler**; there is no local wrangler path.

**Stages** (`--stage`) are separate processes with a commit between them, because they fail differently: `script` is where the API spend lives, `render` is where the runner dies, `publish` fails on credentials and network.

| Stage | Entry point | Steps |
|-------|-------------|-------|
| `script` | `run_script_stage` | 1–6 |
| `recover` | `run_recover_stage` | re-render the last 3 days' orphaned episodes |
| `render` | `run_render_stage` | 7–8 |
| `publish` | `run_publish_stage` | 9: transcript, RSS, `index.html`, R2 |
| `audio` | `run_audio_stage` | recover + render + publish |
| `all` | — | 1–9 (default) |

- Values that must cross the stage boundary ride in the script file's `#` header (`# Theme:`, `# Brave:`, `# Weather:`, `# Anchor:`), read back by `read_script_metadata`. Audio paths come from the script's own filename (`_episode_paths`); never recompute the theme.

**Segments.** Every phase runs in `with segment(name, critical=...)`.
- `critical=True` (default) propagates; pass `exit_code=` to turn it into a process exit status.
- `critical=False` swallows the exception. **Pre-assign the block's outputs to their fallback before the `with`.**
- `SystemExit` always passes through. Each memory/state-file write gets its own segment.

**`degrade(name, detail)`.** When you add a fallback, add a `degrade()` call — a silent fallback is the failure this exists to catch. `run_publish_stage` derives exit 78 from these records. Modules that cannot import `degrade()` without a cycle (`gemini_tts`, `weekly_anchor`, `native_land`) append to their own `_degradations` list, which the pipeline drains; a new fallback there must append too. `write_run_report()` runs from a `finally`, so a crashed run still reports.

**Exit codes**

| Code | Meaning |
|------|---------|
| 75 | `EXIT_BUDGET_EXHAUSTED` — Anthropic usage limit; skip the day as a warning (it lifts itself) |
| 76 | `EXIT_NO_ARTICLES` — upstream feed gave nothing usable; skip as a warning |
| 77 | `EXIT_RENDER_FAILED` — no audio; the run goes red |
| 78 | `EXIT_PUBLISH_DEGRADED` — audio is safe, a publish surface failed |
| 79 | `EXIT_CREDITS_EXHAUSTED` — a provider is out of credits; the run goes **red**, because only a human can top it up |

**Money preflight** (`check_api_budget`, `_check_tts_budget`). Recognise billing walls by their **credit wording, never the status code** (`_billing_wall()`): Gemini uses the same 429 for an ordinary rate limit. TTS is preflighted in the script stage, before the day's state is spent. OpenAI is the universal fallback, so its health alone answers "can this day ship?"; the primary is probed only when OpenAI is walled, and the run aborts only when nothing left can render.

**Commits.** Every workflow commits through the `./.github/actions/commit-push` composite action, never an inline `git add`/`commit`/`push`. Its `--autostash` rebase is load-bearing. **If a stage writes a tracked file, some step must stage it.**

**Atomic writes.** Every memory/state/feed write goes through `config_loader.atomic_write_text` / `atomic_write_json`. The loaders read a truncated JSON as `{}`, so a non-atomic write silently loses history.

**Brave spend.** Two plans, two meters: Search ($5/1000, capped) and Answers (monthly credit).
- **The Search cap is shared with `super-rss-feed`** — one key serves both repos. Read Brave's per-key usage export before trusting any estimate.
- A 402 closes that meter for the run (`_trip_brave_wall`); a spent meter is a reason to ask the other one, not to give up.
- Three per-run budgets: `BRAVE_SEARCH_CALL_LIMIT` (speculative body backfill), `BRAVE_DEEP_DIVE_CALL_LIMIT` (demand-driven research), `BRAVE_ANSWERS_CALL_LIMIT`. **Only the two rate-limit wrappers may call `_brave_search`** (a test enforces it). Answers is never called from the speculative path.
- With both meters closed, skip the research pass rather than report "no research warranted".

**Daily review → roadmap** (`episode_review.py`). One Haiku call a night turns the review into candidate findings; dedup, counting and rendering are Python. An item reaches `ROADMAP.md` on its `ROADMAP_MIN_OCCURRENCES`th sighting. The tool owns only the block between `<!-- reviews:begin -->` and `<!-- reviews:end -->`; a human closes an item by checking its box. Only tool-written items retire on silence.

### Configuration (`config_loader.py`)

All content lives in `config/` JSON files, loaded through LRU-cached loaders. No hard-coded strings.

| File | Purpose |
|------|---------|
| `podcast.json` | Title, RSS metadata, `local_places`, TRACE accountability scores |
| `hosts.json` | Riley & Casey — bios, voices, personalities, debate stances |
| `themes.json` | 7 daily themes, keywords, lenses, `event_focus` (the election) |
| `super_cycles.json` | Multi-week focus rotations within each daily theme |
| `weekly_anchors.json` | Seeded pool of weekly anchor questions |
| `ai_tells.json` | Hard-banned phrases, `score_script` pattern families, phrase-ledger tuning, rhythm budget |
| `prompts.json` | All Claude prompt templates |
| `pronunciations.json` | Name → spoken alias, applied in order before synthesis; a longer name must precede any name it contains |
| `standing_notes.txt` | **The producer's** standing rules and facts, one per line, in the cached script system prompt and both fact-check prompts. Edited by hand or by merging a weekly proposal PR |
| `interests.txt` | Article relevance rubric |
| `blocklist.json` | Excluded domains and keywords; `email_producer_senders` |
| `credits.json` | Spoken and written credits |
| `psa_organizations.json` | Community org roster + weekday assignments |
| `disciplines.json` | Topic taxonomy for roundup grouping |
| `indigenous_nations.json` | Nation names + aliases the territory check recognises (recognition only) |

**Memory state** (`podcasts/`, committed daily by CI): `episode_memory.json` (35 days), `host_personality_memory.json`, `debate_memory.json` (90 days), `psa_rotation_state.json`, `article_holding.json`, `weekly_anchor_state.json`, `phrase_ledger.json`, `roadmap_ledger.json`, `native_land_cache.json`, `email_queue.json`, `standing_notes_ledger.json`.

### Curation — see [docs/decisions/curation.md](docs/decisions/curation.md)

**Themes** (weekday 0=Mon): Arts, Culture & Digital Storytelling · Working Lands & Industry · Gear, Gadgets & Practical Tech · Indigenous Lands & Innovation · Wild Spaces & Outdoor Life · **Cariboo Local Affairs** (Sat, longer, 15 articles) · Science, Wonder & the Natural World.

**Saturday is geographic** (`geographic: true`): every candidate is local, so a place-name hit is a constant, not a signal.
- `_build_theme_subject_keywords` strips places and ranks on civic vocabulary.
- `home_places` (Williams Lake, the CRD and its electoral areas, SD27) outranks neighbour towns. It is an **ordering rule, not an exclusion**.
- A geographic day is never a routing target for held articles.

**`event_focus`** is a date-bounded civic event: the Williams Lake 2026 local election, window to Oct 24.
- **It is named on air.** No endorsements: report the races and the candidates' stated positions, never rank them.
- Name every race and name people, not roles. Report a previous run's outcome, **loss included**.
- Everything is bounded by **SOURCED OR UNSAID**: a claim comes from the day's articles or the research block, and an unestablished record is said to be unestablished.
- `event_focus.roster` settles **who is running and nothing else**. A race with no names renders as `NO FILED LIST`.
- `docs/wl-2026-election-candidates.md` and the JSON must carry the same names (a test enforces it).
- An election story airs the day it breaks and is also booked back (`status: 'recall'`) for the next civic episode, tagged `_recalled_from` so the hosts say they covered it.
- The event vocabulary is never folded into `_build_theme_subject_keywords`: "campaign" and "ballot" would admit US politics.
- **When a Saturday deep dive looks wrong, check the upstream theme score in `super-rss-feed` before touching the ranking.**

**News roundup.** The story count comes from airtime (`NEWS_ROUNDUP_COUNT`, ~70 words each), not appetite. A story that can't get its floor is cut, never compressed.
- `NEWS_ROUNDUP_COUNT` bounds the **whole segment, bonus picks included**. `all_articles` is the curated pool; never concatenate the pre-curation `bonus_articles` back in.
- Blocks air in order: `local` → `theme` / `theme_adjacent` → discipline groups → `standalone` → `kicker`. No discipline cluster takes more than `ROUNDUP_CLUSTER_MAX` (3) slots outside the arc blocks.
- `_sequence_roundup` is a chain, not a regroup (the block's lead never moves), and runs at both consumers.
- `_infer_discipline` counts word-boundary hits, never substrings.
- `script/curate` degrades when the theme block is thin. **Read that as a scoring problem, not a supply problem.**
- Hosts never announce blocks, counts, or the running order.
- **LOCAL ELECTION RACES** is a standing roundup rule: keep each town's race distinct, never borrow a name across races.
- NO HEADLINE CRAWL. DO NOT MANUFACTURE CONNECTIONS: if the shared thing can't be named plainly, move on.

**Super-cycles.** Each topical weekday rotates a multi-week focus, calendar-derived (`get_focus_for_day`) and **never announced on air**. Holding (`route_articles_for_focus`) parks off-theme articles for their day within 14 days, flagged `_held_from`.
- **Never hold a local story.**
- Route both the theme and bonus buckets. A slot matches on its theme **or** focus keywords.
- Keyword sets that **gate** a decision are strict (`_build_strict_theme_keywords`); description prose is for ranking only.
- `_no_deep_dive` is honoured by `select_deep_dive_from_feed`.
- A released article is re-labelled for its new day (`_relabel_for_day`), and its stale theme scores are dropped, not rewritten. A story imported for today must not be droppable by the run that imported it.
- The export decision reads the feed's **raw** charter score (`_theme_score_raw`), never the percentile.

**Opinion and crime are filtered upstream** (`super-rss-feed` → `podcast_content_exclusion()`). When one reaches an episode, read that repo's `config/podcast_schedule.json` → `excluded_content` before touching anything here.

### Editorial voice — see [docs/decisions/editorial-voice.md](docs/decisions/editorial-voice.md)

**Weekly anchor** (`weekly_anchor.py`) — one open question per ISO week, **named on air**, circled by all seven deep dives.
- It is pinned in `weekly_anchor_state.json` on the week's first run; every later run reads it back.
- No repetition by **dimension** (26-week cooldown) as well as by id.
- It frames, never selects.
- The prompt's escape hatch (drop the anchor when the material doesn't reach it) is load-bearing, and tested.

**Naming a nation.** The three house nations describe the Cariboo and nowhere else. Name a nation only for its own territory, and only when a source names it. **Naming the wrong people is worse than naming none.**
- `script/territory-check` (`native_land.py`) only ever disconfirms. A crowd-sourced map may remove a claim; it must never supply one.
- The land acknowledgment is exempt by construction (`local_places`).
- `NATIVE_LAND_API_KEY` is a secret; degradation rows record only the exception type.

**AI tells** (`config/ai_tells.json`, `podcasts/phrase_ledger.json`).
- A new pattern family goes in `soft_patterns` unless the extra Opus escalation is intended and costed.
- **Never use a burned phrase in prompt prose** (a test enforces it). The model copies its instructions' register.
- The phrase ledger burns spiking adverbs only; multi-word tics go in `hard_banned` by hand.
- The burned-phrases block goes into the dynamic user prompt, never the cached system prompt.
- `script/tell-scrub` runs after the cold open.
- **TIME OF DAY:** listeners hear the show in the morning. Never "tonight's episode".

**Sunday Meta Moment.** One Haiku call turns the week's commit subjects into a segment.
- **NONE is a first-class answer.**
- The reply cites its commit lines (`COVERED:`) before it speaks, and unknown capitalised names drop the segment.
- Every Sunday without the segment `degrade()`s, whatever the reason.
- Nothing listener-facing goes into that prompt unconditionally.

**Inbound mail** (`email_ingest.py`). `podcasts/email_queue.json` is public: senders are masked and contact details redacted at ingest.
- "From the producer" is decided at ingest (`is_producer_sender`, stamped `from_producer`); never re-derive it from the masked address.
- The block header stays `LISTENER CORRECTIONS`; attribution is per item.
- A correction airs as the final beat of the next roundup (`docs/corrections-policy.md`).
- **A correction that should outlive its episode becomes a standing note.** Every Sunday `standing_notes.py` (in `periodic-review.yml`) reads the correction and feedback emails no run has seen, makes one Haiku call, and opens a PR adding proposed lines to `config/standing_notes.txt`. Merge adopts; close declines for good. Email text is untrusted, so it is quoted as data and nothing reaches a prompt without a merged PR. One proposal PR at a time.

### TTS — see [docs/decisions/openai-tts.md](docs/decisions/openai-tts.md)

**OpenAI `tts-1` is the nightly provider**: `nova` (Riley) + `echo` (Casey), per-turn, in parallel. `OPENAI_TTS_MODEL` selects the model. The steerable `gpt-4o-mini-tts` was tried and reverted: no `speed`, and the acoustic scene resampled mid-turn.
- `_SPEECH_RATE_FITS` is keyed by model. Refit it from the transcript sidecars, never by assumption.
- Every take is checked for duration (`_expected_speech_ms`, retry below 0.80) and amplitude (`_is_silent_take`). **A turn that won't render is cut, never shipped as silence**, and the cut is `degrade()`d.
- Credits name every provider that actually rendered audio (`_compose_tts_credit`). `get_active_tts_provider()` is the routing answer and must not be used for a credit.

**Gemini multi-speaker TTS is parked** — [docs/decisions/gemini-tts.md](docs/decisions/gemini-tts.md) holds its exit criterion and 45 KB of history.
- The code, tests and TTS Eval workflow stay, and `tts_provider=gemini` still dispatches.
- Don't tune, refit or extend it while parked.
- **Never put prior dialogue into a TTS prompt.**

### Smaller subsystems

- **Cohere** (`cohere_enrichment.py`, `USE_COHERE=1`): evolving-story detection, intra-batch clustering, deep-dive rerank. Every public function returns `None` when disabled; callers fall back to string matching.
- **Bespoke episodes** (`generate_bespoke.py`) are **parked**: one episode (March 2026). The code and workflow stay because `seed-content` still triggers it when 3+ content seeds share a tag.
- **PSA selection** (`psa_selector.py`): 7-day lookahead for awareness dates; otherwise round-robin, 28 days between repeats per org.

### Sibling repository

`super-rss-feed` scores the articles and publishes `feed-podcast-{dayname}.json`; it must deploy before this pipeline runs. This repo reads its underscore fields (`_is_bonus`, `_keyword_matches`, `_theme_score`, `_theme_score_raw`, `_excerpt`, …) — see `SIBLING_REPOS.md`. **A claim in this file about `super-rss-feed` is not a change in that repo**: read its config before trusting one.

## API Cost Discipline

Treat API budget as a first-class constraint on every change.

- **Default to the cheapest model.** Escalate (Haiku → Sonnet → Opus) only when demonstrably required — justify explicitly. Opus is only used for review escalation when deep-dive sourcing is thin (<3 articles).
- **Sonnet 5 and Opus 5 think when `thinking` is omitted, and thinking shares `max_tokens` with the answer.** A small-budget or structured call must pass `thinking={"type": "disabled"}`. Otherwise it returns no text: the weekly anchor framings and the weekly script review both failed this way for months.
- **Prompt compression is mandatory.** Strip filler and redundant context before sending.
- **Cache aggressively.** Use Anthropic `cache_control` headers for large static context reused across calls.
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

## Decision records

| File | Covers |
|------|--------|
| [operations.md](docs/decisions/operations.md) | Scheduling, stages and segments, `degrade()`, exit codes, money preflight, commits, atomic writes, Brave spend, the roadmap |
| [curation.md](docs/decisions/curation.md) | Themes, the geographic day, the election (`event_focus`), roundup curation, super-cycles and holding |
| [editorial-voice.md](docs/decisions/editorial-voice.md) | Weekly anchor, naming nations, AI tells and the phrase ledger, the Meta Moment, inbound mail |
| [openai-tts.md](docs/decisions/openai-tts.md) | The nightly TTS provider, speech-rate fits, per-take checks |
| [gemini-tts.md](docs/decisions/gemini-tts.md) | Gemini multi-speaker TTS — parked, with its exit criterion |
| [2026-02-16-multi-feed-model.md](docs/decisions/2026-02-16-multi-feed-model.md) | The move to seven themed upstream feeds |
