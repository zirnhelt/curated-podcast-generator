# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

It holds the rules **every** session needs. The detailed standing rules for each area live in
[`.claude/rules/`](.claude/rules/), and Claude Code loads each one when you read or edit a file it
covers (table at the end). The incidents and reasoning behind the rules live in
[`docs/decisions/`](docs/decisions/), one file per area: read the linked file before changing the
code it describes. When you add a rule, keep it to one or two lines in the matching rule file and
put the story in the decision file, not here.

The values behind both curators (this show and `super-rss-feed`) are in
[`docs/MANIFESTO.md`](docs/MANIFESTO.md): read it before changing what gets selected or how the hosts sound.

> **⚠️ Never read `podcast_generator.py` whole.** It is ~14.8K lines (~710 KB, ~180K tokens): one
> full Read fills most of a session's context. Use `grep -n "^def name"` to find a function, then
> Read with `offset`/`limit` for that range. Line numbers drift; grep before trusting the index below.
>
> | Lines (approx.) | Section | Landmarks |
> |---|---|---|
> | 1–180 | Imports, constants, exit codes, `_cdata_safe` | |
> | 180–1575 | Segments, money preflight, Claude calls, TTS credit | `segment`, `degrade`, `write_run_report`, `check_api_budget`, `send_claude` |
> | 1575–2930 | Content seeds, page fetch, cached system prompt | `load_content_seeds`, `build_cached_system_prompt` |
> | 2930–3395 | Brave Search / Answers wrappers | `_brave_search` |
> | 3395–4525 | Agentic tool loop, election roll call, ballot research, roundup body research | `_run_event_sweep`, `_research_event_ballot`, `_filter_sparse_news_articles` |
> | 4525–5320 | Batch API helpers, polish and fact-check | `polish_and_factcheck_with_agent` |
> | 5320–6650 | Memory files, super-cycle holding, aired ledger, roundup echoes | `update_episode_memory`, `route_articles_for_focus`, `find_roundup_echoes` |
> | 6650–8725 | Feed fetch, roundup curation and sequencing, deep-dive selection | `fetch_podcast_feed`, `_sequence_roundup`, `pick_cariboo_deep_dive`, `select_deep_dive_from_feed` |
> | 8725–9720 | Phrase ledger, script scoring, citations | `load_phrase_ledger`, `score_script`, `generate_citations_file` |
> | 9720–10485 | Storyboard and script writing | `storyboard_script`, `apply_storyboard_order`, `generate_podcast_script` |
> | 10485–11375 | Pacing, segment parsing, OpenAI TTS | `parse_script_into_segments`, `generate_tts_for_segment` |
> | 11375–12000 | Meta Moment, territory check | `generate_meta_moment_text`, `scrub_territory_claims` |
> | 12000–12940 | Audio assembly, R2 | `generate_audio_from_script`, `sync_site_to_r2` |
> | 12940–13630 | Transcripts, RSS, script file | `generate_podcast_rss_feed`, `save_script_to_file` |
> | 13630–14768 | Stage entry points and CLI | `run_script_stage`, `run_render_stage`, `run_publish_stage`, `main` |

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

**Every PR body follows [`.github/pull_request_template.md`](.github/pull_request_template.md)**, all sections in order, "None" where one doesn't apply. That includes PRs opened from a commit message and PRs whose description you update later.

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

# ⚠️ The bare command defaults to --stage all: script + render + PUBLISH (feeds,
# index.html, R2 sync with whatever R2 credentials are set). Never run it to "try" a change.
python podcast_generator.py                    # = --stage all, publishes

# Generate without publishing: script, then render. Both still spend API money, and
# the script stage rewrites the podcasts/ memory files (revert them; don't commit).
python podcast_generator.py --stage script     # curate + write script, no TTS spend
python podcast_generator.py --stage render     # TTS + assembly only, no publishing

# Run one stage at a time
python podcast_generator.py --stage publish    # transcript, feeds, index.html, R2 sync
python podcast_generator.py --stage recover    # re-render past episodes missing audio
python podcast_generator.py --stage audio      # recover + render + publish (back-compat)

# Re-render or re-publish a past or hand-edited script
python podcast_generator.py --stage render --date 2026-07-24
python podcast_generator.py --stage publish --date 2026-07-24
python podcast_generator.py --stage audio --script podcasts/podcast_script_2026-07-24_theme.txt

# See the per-segment status table locally (CI writes it to the job summary).
# This is a real publish (R2 sync included): unset the R2 credentials first.
GITHUB_STEP_SUMMARY=/tmp/summary.md python podcast_generator.py --stage publish

# Preview the weekly anchor schedule without spending or writing state
python weekly_anchor.py --preview 12
```

Tests require no API keys — `tests/conftest.py` installs lightweight stubs for `anthropic`, `openai`, `pydub` and `cohere` at import time. CI runs the suite on every push and PR (`.github/workflows/tests.yml`). In cloud sessions a SessionStart hook (`.claude/hooks/install-test-deps.sh`) installs the lock file and pytest when they're missing (about 25 s on a fresh container, 0.15 s after).

**State-file isolation:** the live memory/state JSON files in `podcasts/` are production data committed daily by CI — code under test that persists state will rewrite them in place. An autouse fixture in `tests/conftest.py` already redirects `psa_selector.PSA_STATE_FILE` to a tmp copy; any new test (or code path) that touches a `podcasts/` state file must get the same treatment (monkeypatch the path/`PODCASTS_DIR` into `tmp_path`). After any local test run, check `git status` — a modified state file is test leakage to be reverted (`git checkout -- podcasts/<file>`), never committed.

## Architecture

**Cariboo Signals** is a daily two-host show (Riley & Casey) covering rural BC tech and community topics. It is public on Apple Podcasts and Spotify. The pipeline runs on GitHub Actions and deploys audio + RSS to GitHub Pages (audio also to R2).

**Daily run (`podcast_generator.py`):**
1. Idempotency check — exits if today's episode already exists in the RSS feed
2. Pull scored articles from sibling repo `super-rss-feed` (`feed-podcast-{dayname}.json` from its GitHub Pages URL)
3. Deduplicate against the last 7 days of citations (`dedup_articles.py`, optionally Cohere embeddings)
4. Cluster same-story articles; super-cycle routing (release matured held articles, hold off-theme ones for their day); select roundup stories and deep-dive articles
5. Claude storyboards the roundup and deep dive (`storyboard_script`), writes the two-host script to that plan, then polishes it (a discarded polish airs the draft and `degrade()`s `script/polish`; the outcome is `episode.models.polish`). Length rules: `.claude/rules/operations.md`.
6. Write citations JSON and every memory/state file; save the script to `podcasts/podcast_script_{date}_{theme}.txt` — **end of the script stage**; the workflow commits here
7. OpenAI TTS renders each speaker turn in parallel
8. pydub assembles: cold open (before the music) → intro → welcome → interval → news roundup → interval → deep dive → outro
9. Transcript + RSS entry, commit, deploy to `gh-pages`

### Stages and exit codes — rules in `.claude/rules/operations.md`

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

**Exit codes**

| Code | Meaning |
|------|---------|
| 75 | `EXIT_BUDGET_EXHAUSTED` — Anthropic usage limit; skip the day as a warning (it lifts itself) |
| 76 | `EXIT_NO_ARTICLES` — upstream feed gave nothing usable; skip as a warning |
| 77 | `EXIT_RENDER_FAILED` — no audio; the run goes red |
| 78 | `EXIT_PUBLISH_DEGRADED` — audio is safe, a publish surface failed |
| 79 | `EXIT_CREDITS_EXHAUSTED` — a provider is out of credits; the run goes **red**, because only a human can top it up |

### Rules every session needs

The full rule, and the story behind it, is in the rule file and decision record named in brackets.

- A test or local run that rewrites a `podcasts/` state file is leakage: revert it, never commit it (above).
- Every memory/state/feed write goes through `atomic_write_json` / `atomic_write_text` (operations).
- Every fallback calls `degrade()`; a `critical=False` segment pre-assigns its outputs (operations).
- Workflows commit only through `./.github/actions/commit-push` (operations).
- Third-party text is `html.escape`d; every fetch of a feed- or model-supplied URL goes through `_get_public` (operations).
- On air, everything is SOURCED OR UNSAID; elections get no endorsements (curation).
- Name a nation only for its own territory and only when a source names it. Naming the wrong people is worse than naming none (editorial-voice).
- Never use a burned phrase in prompt prose (editorial-voice).
- Never hold a local story (curation).
- Billing walls are recognised by credit wording; an `except` that falls back calls `_abort_if_billing_wall` first (operations).
- Every Sonnet or Opus call goes through `send_claude` or `create_message`; a small or structured call passes `thinking=thinking_off(model)` (api-cost).
- Never `git fetch --depth` over the full checkout (editorial-voice).

## API Cost Discipline

Treat API budget as a first-class constraint on every change.

- **Default to the cheapest model.** Escalate (Haiku → Sonnet → Opus) only when demonstrably required — justify explicitly. Model routing, the refusal fallback, streaming and the price table are in `.claude/rules/api-cost.md`.
- **Prompt compression is mandatory.** Strip filler and redundant context before sending.
- **Cache aggressively.** Use Anthropic `cache_control` headers for large static context reused across calls.
- **Batch where possible.** Combine small tasks into one API call instead of N round-trips.
- **Never call an API when local logic suffices.** Dedup, filtering, formatting, classification — do it in Python first.
- **Log token usage.** Every Claude response goes through `_log_claude_usage(response)` (every token class, a thinking estimate, list-price $). No silent spending.
- **Fail fast on runaway cost.** Unexpectedly large token counts should raise, not proceed.
- **Review diffs for cost regressions.** Call out any prompt/pipeline change that increases per-run token usage.

## Project Constraints

- Python 3.11+, PEP 8, type hints on all functions
- Idempotent scripts where possible
- Refactor existing files rather than creating new ones
- Keep dependencies minimal — check `requirements.txt` before adding anything
- Every workflow installs `requirements-lock.txt`. Edit `requirements.txt`, then regenerate the lock (Python 3.11); `tests/test_requirements_lock.py` fails until you do

## Where the rules live

| Rule file (`.claude/rules/`) | Loads when you touch | Decision record |
|---|---|---|
| `operations.md` | `podcast_generator.py`, `episode_review.py`, `config_loader.py`, `.github/`, `cloudflare/` | [operations.md](docs/decisions/operations.md) |
| `curation.md` | `podcast_generator.py`, `dedup_articles.py`, `cohere_enrichment.py`, theme/cycle/feed config | [curation.md](docs/decisions/curation.md) |
| `editorial-voice.md` | `podcast_generator.py`, `weekly_anchor.py`, `native_land.py`, `email_ingest.py`, `standing_notes.py`, prompt/host/tell config | [editorial-voice.md](docs/decisions/editorial-voice.md) |
| `tts.md` | `podcast_generator.py`, `gemini_tts.py`, `pronunciations.json` | [openai-tts.md](docs/decisions/openai-tts.md), [gemini-tts.md](docs/decisions/gemini-tts.md) |
| `api-cost.md` | `podcast_generator.py`, `episode_review.py`, `review_scripts.py`, `standing_notes.py`, `config_loader.py`, workflows | [operations.md](docs/decisions/operations.md) |
| `configuration.md` | `config/`, `config_loader.py`, `podcasts/` | — |
| `subsystems.md` | `podcast_generator.py`, `cohere_enrichment.py`, `generate_bespoke.py`, `seed.py`, `psa_selector.py` | — |

Also: [MANIFESTO.md](docs/MANIFESTO.md) (the editor's values for both curators) and
[2026-02-16-multi-feed-model.md](docs/decisions/2026-02-16-multi-feed-model.md) (the move to seven themed upstream feeds).
Before changing code a rule file covers when you haven't opened a matching file yet, read the rule file.
