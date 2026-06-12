# Super RSS Feed — Requested Fixes

These are changes needed in [super-rss-feed](https://github.com/zirnhelt/super-rss-feed) to improve the podcast generator's article quality. Both issues are logged as `TODO(super-feed)` in `podcast_generator.py` (lines 340–345).

See [SIBLING_REPOS.md](../SIBLING_REPOS.md) for the general integration model between the two projects.

---

## 1. Add dedicated local news sources for "Cariboo Local Affairs" theme day

### Problem

Theme day 5 (Saturday) is **"Cariboo Local Affairs"** — its purpose is to surface actual local reporting and civic coverage about Williams Lake, Quesnel, 100 Mile House, and Bella Coola, including Williams Lake Town Council decisions (council meets Tuesday evenings; coverage is available well before Saturday's episode). Currently, the feed has no dedicated local news RSS sources, so this theme day ends up pulling generic tech articles and awkwardly framing them as local content. The theme keywords in the podcast generator (`Williams Lake`, `Quesnel`, `100 Mile`, `Bella Coola`, `Cariboo`, `council`, `CRD`, `SD27`, `local`, `community`, `rural`, etc.) rarely match anything because the upstream feeds simply don't carry this content.

### What to change

Prioritize RSS feeds from regional BC news outlets to the super-rss-feed source list. Recommended sources:

| Source | Coverage |
|---|---|---|
| Williams Lake Tribune | Williams Lake, Cariboo region | 
| Quesnel Cariboo Observer | Quesnel, Cariboo region | 
| 100 Mile Free Press | 100 Mile House area 
| My Cariboo Now | Williams Lake, Quesnel, regional |
| CBC Prince George | Northern BC, Cariboo mentions |
| CBC Kamloops | Northern BC, Cariboo mentions |

**Important notes:**

- Some Black Press sites (Tribune, Observer, Free Press) share a CMS and may use a consistent feed URL pattern — check one and the others likely follow.
- These articles should flow into the existing `feed-local.json` category or a new `feed-cariboo.json` category. If using a new category, the podcast generator's `CATEGORY_FEEDS` list in `podcast_generator.py` (line ~260) will also need updating.
- The scoring system should give these sources a baseline boost. Articles from Cariboo-region outlets inherently match the podcast's interests — the `interests.txt` scoring notes already say "Local Williams Lake/Cariboo content should score 80+ regardless of topic."

### How the podcast generator uses this

The curated `feed-podcast.json` endpoint already tags articles with `_is_bonus` (off-theme) vs theme-matched. On Saturdays, the theme keywords will match Cariboo-region articles if the upstream feed actually contains them. No podcast generator changes should be needed — the articles just need to exist in the feed.

### Verification

After deploying, check that `feed-podcast.json` on a Saturday includes articles from these local sources in the theme-matched set (not in `_is_bonus`). You can also check `feed-local.json` directly to confirm the articles are being ingested.

---

## 2. Add theme-aware filtering for news roundup articles

### Problem

The podcast has two main article segments:
1. **News Roundup (Segment 1)** — a quick survey of 3–5 articles
2. **Deep Dive (Segment 3)** — an extended discussion of 1–2 theme-relevant articles

Currently, the `feed-podcast.json` endpoint provides theme-matched articles and bonus (off-theme) articles, but the **bonus articles used for the news roundup are not filtered for thematic diversity**. On most days, the bonus set skews heavily toward AI/tech content because those are the most common high-scoring articles in the feed. This makes the news roundup feel repetitive and disconnected from the day's theme.

### What to change

When building the bonus article set for `feed-podcast.json`, apply light theme-aware filtering:

1. **Ensure category diversity in bonus articles.** Don't let a single category (especially `ai-tech`) dominate the bonus set. A simple approach:
   - Cap any single category at 2–3 articles in the bonus set.
   - If space remains, backfill from underrepresented categories.

2. **Prefer articles with some thematic adjacency.** When scoring/ranking bonus articles, give a small boost to articles that share at least one keyword with the day's theme — not enough to override quality, but enough to break ties. The podcast generator's `config/themes.json` provides the full keyword list per theme day and is the source of truth for what each day's theme covers.

3. **Expose category metadata on each article.** If not already present, include a `_source_category` field (e.g., `"ai-tech"`, `"climate"`, `"local"`) on each article item in `feed-podcast.json`. This lets the podcast generator make its own downstream decisions about diversity without requiring more upstream logic later.

### How the podcast generator uses this

In `podcast_generator.py` (lines 1596–1600), after selecting deep-dive articles from the theme set, the remaining theme articles plus all bonus articles become the news roundup pool. The generator currently takes them in score order. With better upstream diversity, the news roundup will naturally cover a broader range of topics.

The podcast generator does **not** currently use `_source_category` for filtering, but exposing it is low-cost and enables future improvements on the consumer side (e.g., the medium-term roadmap item "Better theme-to-article matching").

### Verification

Compare `feed-podcast.json` output across several days. The bonus article set should show a mix of categories rather than being dominated by `ai-tech`. A quick check: count articles per `_source_category` in the bonus set — no single category should exceed ~50% of the total.

---

## 3. Wednesday ("Gear, Gadgets & Practical Tech") theme pool runs dry

### Problem

The 2026-06-10 episode (Wednesday, theme "Gear, Gadgets & Practical Tech") had **zero
genuinely gadget-related news articles**. The GitHub Actions log for that run
(workflow run `27267722640`) shows:

```
📌 Feed theme: Gear, Gadgets & Practical Tech
✓ Theme articles: 5
✓ Bonus articles: 28
✅ Loaded 40 articles from podcast feed
```

Only 5 of 40 articles in `feed-podcast-wednesday.json` were theme-matched, and none of
those 5 were actually about gear/gadgets — the news roundup ended up 100% Al Jazeera
world news, and the deep dive had to be carried by the hosts' own knowledge rather than
grounded in an article.

`podcast_shown_cache.json` shows several Hackaday articles tagged `:::wednesday` from
May 27 (e.g. `inside-dysons-over-engineered-...-hand-dryer`,
`autopsy-of-a-failed-vintage-carbon-resistor`) — these were already used for a previous
Wednesday and have since aged out of the rolling 7-day cache, leaving the Wednesday pool
without fresh replacements. Meanwhile `feed-homelab.json` / `feed-science.json` /
`feed-climate.json` *do* contain recent Hackaday content (late May/early June), so the
gadget-source feeds (Hackaday, Make Magazine, Cool Tools — already in `feeds.opml`) are
being polled; the issue looks like a cache/scoring timing gap specific to the Wednesday
theme pool rather than a missing-source problem.

### What to change

- Check whether `podcast_schedule.json`'s Wednesday `min_score: 42` is filtering out
  gadget/maker articles that score lower on the general-interest rubric than world-news
  items, even when they're a strong topical match for "Gear, Gadgets & Practical Tech."
  Consider a per-theme score floor, or a relevance boost for gadget-source articles
  similar to the `source_boost` allowlist added on the consumer side (see below).
- Confirm the rolling 7-day cache + `podcast_shown_cache.json` aren't exhausting the
  small pool of gadget-source articles before fresh ones are scored and ingested for a
  given theme day. If Hackaday/Make/Cool Tools publish only a handful of articles per
  week, a 7-day window with `:::wednesday`-tagged exclusions can leave Wednesday with
  too few candidates even though the sources are healthy.

### Consumer-side context (already fixed in `curated-podcast-generator`)

This run also surfaced a false-positive keyword match: `config/themes.json` theme 2
included the bare keyword `"gear"`, which matched **"[NYT Business] Nose Gear on Boeing
787-9 Dreamliner Collapses"** (aircraft landing gear, not consumer gadgets) as a "strong
keyword match." Several other generic single-word keywords (`tool`, `camera`, `monitor`,
`battery`, `drone`, `device`, `review`, `phone`) had the same problem. These have been
replaced with more specific multi-word phrases (`"3D printer"`, `"right to repair"`,
`"battery chemistry"`, `"GPS unit"`, etc.), and a `source_boost` allowlist of gadget/maker
outlets (Hackaday, Engadget, The Verge, TechRadar, iFixit, etc.) now gives a small
relevance boost to articles from those sources.

If a similar `source_boost`-style allowlist or per-source scoring boost is added
upstream in `super-rss-feed`'s scoring pass, it should use the same source names so the
two repos stay aligned (see [ROADMAP.md](../ROADMAP.md) "Cross-project: shared
interest/scoring config").

### Verification

On a future Wednesday, check `feed-podcast-wednesday.json`'s theme-matched article count
and confirm it includes at least a few gadget/maker-source articles (Hackaday, Make,
Cool Tools, etc.), not just whatever scored highest on the general rubric.

---

## 4. Apply anti-keyword penalties when assigning articles to theme-day buckets

### Problem

`config/themes.json` now includes an `anti_keywords` field for themes whose
keyword sets overlap with a neighboring theme day. For example, theme 6
(Science, Wonder & the Natural World) penalizes Indigenous data-sovereignty
terms (`"data sovereignty"`, `"OCAP"`, `"land title"`, `"treaty negotiation"`,
etc.) because that content really belongs to theme 3 (Indigenous Lands &
Innovation, Thursday). The consumer side (`podcast_generator.py`) already
applies this penalty in three places:

- `_score_text_against_themes()` — scoring articles against all 7 themes
- `_local_theme_relevance()` — picking Deep Dive articles from a theme's bucket
- News Roundup ordering — sorting the remaining theme-bucket articles for the
  roundup (added alongside this doc update)

But the **upstream bucket assignment** — whichever scoring pass in
super-rss-feed decides which `feed-podcast-{day}.json` an article lands in,
and sets its initial `_keyword_matches` / `_boosted_score` — does not know
about `anti_keywords`. An article that scores well on theme-6 keywords purely
because it mentions Indigenous governance in a science context can still be
bucketed into Sunday's feed as a strong theme match, and the consumer-side
penalty only gets a chance to demote it within that bucket — it can't move it
to a different day's bucket where it'd actually fit better.

### What to change

When scoring an article against a theme day's keyword set, also check that
theme's `anti_keywords` (from `config/themes.json`, the source of truth for
both repos) and subtract the same per-word-weighted penalty before computing
`_keyword_matches` / `_boosted_score` for that theme-day bucket:

```
theme_score = positive_keyword_hits - anti_keyword_hits   # floored at 0
```

If an article scores higher against a *different* theme's keyword set once
anti_keywords are applied (e.g. it scores low for Sunday/Science but high for
Thursday/Indigenous Lands), prefer bucketing it under the theme it actually
fits — same logic as `_claude_theme_match()`'s "hold for the most relevant
upcoming episode" fallback in the consumer.

### How the podcast generator uses this

This closes the loop the consumer side opened: `_score_text_against_themes()`,
`_local_theme_relevance()`, and the News Roundup sort all already subtract
`anti_keywords` hits. Doing the same at bucket-assignment time means
misclassified articles never enter the wrong day's `_is_bonus=false` set in
the first place, instead of relying on the consumer to merely de-prioritize
them within that day.

### Verification

Pick a theme with `anti_keywords` configured (currently theme 6, Science,
Wonder & the Natural World) and confirm that articles whose text is dominated
by that theme's `anti_keywords` terms either score lower for that theme-day's
bucket or get reassigned to the theme day whose keywords they actually match.

---

## Implementation order

These fixes are independent and can be done in either order, but **fix 1 (local sources) has higher impact** because it addresses a content gap that no amount of filtering can solve — you can't surface local Cariboo news if the feed doesn't contain any.

Suggested sequence:
1. Add local news sources and verify they appear in feeds
2. Add category diversity filtering to the bonus article set
3. Add `_source_category` field to `feed-podcast.json` items
4. Investigate the Wednesday gadget-theme pool (issue 3 above) — check `min_score`
   and rolling-cache interaction for gadget-source feeds
5. Apply `anti_keywords` penalties from `config/themes.json` at bucket-assignment
   time (issue 4 above) — lowest effort of the five since the keyword lists and
   penalty formula already exist on the consumer side; mainly a matter of reading
   the same config and applying the same subtraction during upstream scoring

After each change, follow the [deployment verification steps](../SIBLING_REPOS.md#monitoring-and-failure) to confirm the podcast generator consumes the updated feed correctly.

---

## Related roadmap items

These fixes also advance several items tracked in the podcast generator's [ROADMAP.md](../ROADMAP.md):

- **Medium-term:** "Better theme-to-article matching" — category metadata enables this; `anti_keywords`-aware bucketing (fix 4) is a direct instance of it
- **Long-term:** "Cross-project: shared interest/scoring config" — local source scoring aligns with this goal; `config/themes.json`'s `keywords`/`anti_keywords`/`lens` fields should be treated as the shared source of truth for theme-day scoring in both repos
