# Super RSS Feed — Requested Fixes

These are changes needed in [super-rss-feed](https://github.com/zirnhelt/super-rss-feed) to improve the podcast generator's article quality. Both issues are logged as `TODO(super-feed)` in `podcast_generator.py` (lines 340–345).

See [SIBLING_REPOS.md](../SIBLING_REPOS.md) for the general integration model between the two projects.

---

## 1. Add dedicated local news sources for "Cariboo Voices & Local News" theme day

### Problem

Theme day 5 (Saturday) is **"Cariboo Voices & Local News"** — its purpose is to surface actual local reporting about Williams Lake, Quesnel, 100 Mile House, and Bella Coola. Currently, the feed has no dedicated local news RSS sources, so this theme day ends up pulling generic tech articles and awkwardly framing them as local content. The theme keywords in the podcast generator (`Williams Lake`, `Quesnel`, `100 Mile`, `Bella Coola`, `Cariboo`, `local`, `community`, `rural`, `small town`, etc.) rarely match anything because the upstream feeds simply don't carry this content.

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

## Implementation order

These fixes are independent and can be done in either order, but **fix 1 (local sources) has higher impact** because it addresses a content gap that no amount of filtering can solve — you can't surface local Cariboo news if the feed doesn't contain any.

Suggested sequence:
1. Add local news sources and verify they appear in feeds
2. Add category diversity filtering to the bonus article set
3. Add `_source_category` field to `feed-podcast.json` items

After each change, follow the [deployment verification steps](../SIBLING_REPOS.md#monitoring-and-failure) to confirm the podcast generator consumes the updated feed correctly.

---

## Related roadmap items

These fixes also advance several items tracked in the podcast generator's [ROADMAP.md](../ROADMAP.md):

- **Medium-term:** "Better theme-to-article matching" — category metadata enables this
- **Long-term:** "Cross-project: shared interest/scoring config" — local source scoring aligns with this goal
