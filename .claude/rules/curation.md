---
paths:
  - "podcast_generator.py"
  - "tests/test_podcast_generator.py"
  - "dedup_articles.py"
  - "cohere_enrichment.py"
  - "tests/test_dedup.py"
  - "tests/test_super_cycles.py"
  - "config/themes.json"
  - "config/super_cycles.json"
  - "config/podcast.json"
  - "config/disciplines.json"
  - "config/blocklist.json"
  - "config/interests.txt"
  - "docs/wl-2026-election-candidates.md"
---
<!-- Moved verbatim from CLAUDE.md on 2026-11-02. Claude Code loads this file when a
     matching path is read or edited. Keep each rule to one or two lines; the story goes
     in docs/decisions/. -->

### Curation — see [docs/decisions/curation.md](../../docs/decisions/curation.md)

**Themes** (weekday 0=Mon): Arts, Culture & Digital Storytelling · Working Lands & Industry · Gear, Gadgets & Practical Tech · Indigenous Lands & Innovation · Wild Spaces & Outdoor Life · **Cariboo Local Affairs** (Sat, longer: 18 roundup stories + a 5-source deep dive; weekdays 18 + 4) · Science, Wonder & the Natural World.

**Saturday is geographic** (`geographic: true`): every candidate is local, so a place-name hit is a constant, not a signal.
- `_build_theme_subject_keywords` strips places and ranks on civic vocabulary.
- `home_places` (Williams Lake, the CRD and its electoral areas, SD27) outranks neighbour towns. It is an **ordering rule, not an exclusion**.
- A geographic day is never a routing target for held articles.

**`event_focus`** is a date-bounded civic event: the Williams Lake 2026 local election, window to Oct 24.
- **It is named on air.** No endorsements: report the races and the candidates' stated positions, never rank them.
- Name every race and name people, not roles. Report a previous run's outcome, **loss included**.
- Everything is bounded by **SOURCED OR UNSAID**: a claim comes from the day's articles or the research block, and an unestablished record is said to be unestablished.
- `event_focus.roster` settles **who is running**, plus `records`: dated lines from a named outlet, rendered as SOURCED BACKGROUND. A race with no names renders as `NO NAMES IN THE ROSTER`. **Never air a gap as "unfindable" or "not public"**: fill the race in the day its list is public.
- The research pass runs a **Python roll call first** (`_run_event_sweep`: one search per race and per running candidate), then the agent follows up with `fetch_page`. A page in the day's feed whose headline names a running candidate is read in full first (`_candidate_profiles`, no search); local names come before provincial follow-ups. More than a third of the ballot under `NO RECORD FOUND` `degrade()`s. Queries and the research block are saved in the citations JSON under `research`.
- **All-week events** (`podcast.json` → `all_week_events`: the 2026 B.C. provincial vote, Oct 24) are swept and lensed on any day an episode article carries their vocabulary (`_events_in_play`). They never steer selection.
- **An all-week event gets its own research pass** (`_research_event_ballot`: election meter, results-only search, `recent` for this campaign). It never takes the deep dive's pass; only the day's own event shares that one.
- **Once nominations close, the weekday ballot pass reuses its last complete research** (`podcasts/ballot_research_cache.json`) until it is `BALLOT_RESEARCH_MAX_AGE_DAYS` old, the roster or brief changes, or an election story reads like the field moved (`_ROSTER_CHANGE_RE`). Saturday's shared pass is never cached.
- **A fresh ballot pass is handed the last one** (PRIOR RESEARCH) and each election story's URL and first 1500 characters: never call unestablished what an earlier pass sourced. Article bodies are `<p>` prose (`_article_prose`), never the page's menus.
- `docs/wl-2026-election-candidates.md` and the JSON must carry the same names (a test enforces it).
- An election story airs the day it breaks and is also booked back (`status: 'recall'`) for the next civic episode, tagged `_recalled_from` so the hosts say they covered it.
- The event vocabulary is never folded into `_build_theme_subject_keywords`: "campaign" and "ballot" would admit US politics.
- **When a Saturday deep dive looks wrong, check the upstream theme score in `super-rss-feed` before touching the ranking.**

**News roundup.** The story count comes from airtime (`NEWS_ROUNDUP_COUNT`, ~70 words each), not appetite. A story that can't get its floor is cut, never compressed.
- `NEWS_ROUNDUP_COUNT` bounds the **whole segment, bonus picks included**. `all_articles` is the curated pool; never concatenate the pre-curation `bonus_articles` back in.
- Blocks air in order: `local` → `theme` / `theme_adjacent` → discipline groups → `standalone` → `kicker`. No discipline cluster takes more than `ROUNDUP_CLUSTER_MAX` (3) slots outside the arc blocks.
- The kicker is the wildcard: from the better half of the standalones that share no subject with another roundup story (`_shares_subject`, a headline word pair), the lowest `_theme_score_raw`. None qualifies, no kicker.
- `_sequence_roundup` is a chain, not a regroup (the block's lead never moves), and runs at both consumers.
- `_infer_discipline` counts word-boundary hits, never substrings.
- **The charter fit overrules a stray keyword** (`_charter_promotes` / `_charter_demotes`): a keyword hit under the p`THEME_BLOCK_MIN_PCT` percentile is not on-theme, and a story at p`THEME_BLOCK_PROMOTE_PCT` with raw ≥ `THEME_BLOCK_PROMOTE_RAW` is, keyword or not (never on the geographic day). The Cariboo bar's pool keeps non-local candidates at p`DEEP_DIVE_PICK_MIN_PCT`+. Unscored articles keep the keyword test.
- `script/curate` degrades when the theme block is thin. **Read that as a scoring problem, not a supply problem.**
- Hosts never announce blocks, counts, or the running order.
- **LOCAL ELECTION RACES** is a standing roundup rule: keep each town's race distinct, never borrow a name across races.
- NO HEADLINE CRAWL. DO NOT MANUFACTURE CONNECTIONS: if the shared thing can't be named plainly, move on.
- **Research before writing, never narrate the gap.** Every curated story under `NEWS_BODY_RICH_CHARS` is researched in `_filter_sparse_news_articles` (own page, excerpt (2000 chars when upstream ran Kagi Extract), the own page through Kagi Extract (`_extract_roundup_stories`, `KAGI_EXTRACT_CALL_LIMIT` pages, before any Brave spend), one title search plus a read of the matching coverage, credited via `_body_via`). A **Kagi News** cluster (`_kite_sources` > 0) arrives researched: `_use_kite_body` makes its `_excerpt` (summary + talking points, each claim's outlet in parentheses) the `_body` at load, so nothing is spent on it and no single outlet's page replaces it; a day one airs credits Kagi News (CC BY-NC). The prompt sees `ROUNDUP_PROMPT_BODY_CHARS` of it. Its `_kite_context` (perspectives, quote, timeline, background, each line naming its outlet) follows as a Research line, whole lines only: `ROUNDUP_PROMPT_CONTEXT_CHARS` in the roundup, `DEEP_DIVE_PROMPT_CONTEXT_CHARS` in the deep dive (`_kite_context_line`). Hosts never say what an excerpt or report leaves out (only the ballot's "unestablished" survives); `source_gap_narration` counts it.

**Super-cycles.** Each topical weekday rotates a multi-week focus, calendar-derived (`get_focus_for_day`) and **never announced on air**. Holding (`route_articles_for_focus`) parks off-theme articles for their day within 14 days, flagged `_held_from`.
- **Never hold a local story.**
- Route both the theme and bonus buckets. A slot matches on its theme **or** focus keywords.
- Keyword sets that **gate** a decision are strict (`_build_strict_theme_keywords`); description prose is for ranking only.
- A focus keyword is a phrase when the bare word has another common sense (`park`, `trail`, `space`): one hit admits an article to the deep-dive pool, and rerank cannot drop it.
- `_no_deep_dive` is honoured by `select_deep_dive_from_feed`.
- A released article is re-labelled for its new day (`_relabel_for_day`), and its stale theme scores are dropped, not rewritten. A story imported for today must not be droppable by the run that imported it.
- The export decision reads the feed's **raw** charter score (`_theme_score_raw`), never the percentile.

**Opinion and crime are filtered upstream** (`super-rss-feed` → `podcast_content_exclusion()`). When one reaches an episode, read that repo's `config/podcast_schedule.json` → `excluded_content` before touching anything here.
