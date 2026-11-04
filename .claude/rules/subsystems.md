---
paths:
  - "podcast_generator.py"
  - "tests/test_podcast_generator.py"
  - "cohere_enrichment.py"
  - "generate_bespoke.py"
  - "seed.py"
  - "psa_selector.py"
  - "tests/test_psa_selector.py"
  - "SIBLING_REPOS.md"
---
<!-- Moved verbatim from CLAUDE.md on 2026-11-02. Claude Code loads this file when a
     matching path is read or edited. Keep each rule to one or two lines; the story goes
     in docs/decisions/. -->

### Smaller subsystems

- **Cohere** (`cohere_enrichment.py`, `USE_COHERE`, on in the script step; the repo variable set to `0` turns it off): evolving-story detection, intra-batch clustering, and the live deep-dive rerank. Every public function returns `None` when disabled or failed; callers fall back, and a failure is a `script/cohere` row (`drain_degradations`).
  - **Rerank reorders, never admits**: it picks from the head (`DEEP_DIVE_RERANK_POOL`) of the rule-ranked list, never on the geographic day, and its query is the theme and focus, never the weekly anchor.
- **The Cariboo bar** (`pick_cariboo_deep_dive`, one Haiku call, topical days only): from the rule-ranked head plus fresh local bonus stories, the first story a Cariboo household could act on, then other outlets' reports of the same story, never a neighbouring one: a report must share a headline word with the lead (`_headline_words`), checked in Python. The anchor only breaks a tie. An aired local story from the past week (`recent_local_roundup`) may ride along as its Cariboo connection, tagged `_recalled_from`. Nothing passes: `script/deep-dive-local` degrades and the rule pick airs.
  - The credit follows `was_used()`, not the switch. Embed is batched at 96 texts and read from `embeddings.float_`.
- **Bespoke episodes** (`generate_bespoke.py`) are **parked**: one episode (March 2026). The code and workflow stay because `seed-content` still triggers it when 3+ content seeds share a tag.
- **Producer-pinned deep dive** (`seed.py url … --deep-dive --date YYYY-MM-DD`, `--title` for a link whose page names no article): every pinned seed due that day is the deep dive, past focus, rerank, the Cariboo bar and the substance swap. A seed already in the feed pins the feed's copy (`_merge_seed_articles`), so its body survives.
- **PSA selection** (`psa_selector.py`): 7-day lookahead for awareness dates; otherwise round-robin, 28 days between repeats per org.

### Sibling repository

`super-rss-feed` scores the articles and publishes `feed-podcast-{dayname}.json`; it must deploy before this pipeline runs. This repo reads its underscore fields (`_is_bonus`, `_keyword_matches`, `_theme_score`, `_theme_score_raw`, `_excerpt`, …) — see `SIBLING_REPOS.md`. **A claim in this file about `super-rss-feed` is not a change in that repo**: read its config before trusting one.
