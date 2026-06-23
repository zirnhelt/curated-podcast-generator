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
python podcast_generator.py

# Bespoke episode
python generate_bespoke.py --tag <topic-tag>
```

**Note:** `tests/` is in `.gitignore`. Use `git add -f tests/` when committing test changes.

Tests require no API keys — `tests/conftest.py` installs lightweight stubs for `anthropic`, `openai`, `pydub`, and `azure` at import time.

## Architecture

### High-Level Flow

This is a daily AI podcast generator for **Cariboo Signals**, a two-host show (Riley & Casey) covering rural BC tech and community topics. The pipeline runs on GitHub Actions and deploys audio + RSS to GitHub Pages.

**Daily run (`podcast_generator.py`):**
1. Idempotency check — exits if today's episode already exists in the RSS feed
2. Pull scored articles from sibling repo `super-rss-feed` (fetches `feed-podcast-{dayname}.json` from its GitHub Pages URL)
3. Deduplicate against last 7 days of citations (`dedup_articles.py`, optionally Cohere embeddings via `cohere_enrichment.py`)
4. Cluster same-story articles; select top stories + theme-matched deep-dive articles
5. Claude generates raw two-host script → Claude polishes script (flow, repetition)
6. OpenAI TTS (or Azure Neural TTS) renders each speaker segment in parallel
7. pydub assembles: intro → welcome → interval → news roundup → interval → deep dive debate → outro
8. Writes citations JSON, RSS entry, pushes commit, deploys to `gh-pages`

**Memory state** (JSON files in `podcasts/`):
- `episode_memory.json` — 21-day sliding window for story continuity
- `host_personality_memory.json` — Evolving host traits
- `debate_memory.json` — 90-day window to avoid repeating debate angles
- `psa_rotation_state.json` — Round-robin PSA org rotation state

### Configuration System (`config_loader.py`)

All content is externalized to `config/` JSON files; loaders are LRU-cached (single load per process). No hard-coded strings — all messaging, personalities, and themes live in `config/`.

| File | Purpose |
|------|---------|
| `podcast.json` | Title, RSS metadata, TRACE accountability scores |
| `hosts.json` | Riley & Casey — bios, voices, personalities, debate stances |
| `themes.json` | 7 rotating daily themes (Mon–Sun), keywords, editorial lenses |
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

### TTS Providers

**OpenAI (default):** `nova` (Riley) + `echo` (Casey), per-segment synthesis, parallel rendering.

**Azure Neural TTS (optional, `USE_AZURE_TTS=1`):** Multi-Talker model for coherent prosody across speaker transitions. SSML with `<phoneme>` IPA tags for Cariboo place names. 8,000-char conservative SSML chunk limit. Set `AZURE_TTS_PARALLEL=1` to generate both providers for comparison.

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
