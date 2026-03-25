# CLAUDE.md — super-rss-feed

This repo generates the daily curated article feeds consumed by the
[curated-podcast-generator](https://github.com/zirnhelt/curated-podcast-generator)
to produce the Cariboo Signals podcast.

## How the feeds are used

The podcast generator fetches one JSON feed per day of the week:

```
feed-podcast-monday.json    → Arts, Culture & Digital Storytelling
feed-podcast-tuesday.json   → Working Lands & Industry
feed-podcast-wednesday.json → Community Tech & Governance
feed-podcast-thursday.json  → Indigenous Lands & Innovation
feed-podcast-friday.json    → Wild Spaces & Outdoor Life
feed-podcast-saturday.json  → Cariboo Voices & Local News
feed-podcast-sunday.json    → Resilient Rural Futures
```

Each feed is fetched at generation time (~6 AM Pacific). The generator
splits items into **theme articles** and **bonus articles**, selects the
top 3 theme-matched articles as the deep-dive source, and passes the rest
to the news roundup prompt. It then asks Claude to prioritise on-theme
stories when writing the episode.

## Required item fields (currently missing — priority work)

The generator reads three fields per item that the feed does not currently
emit. Without them, every article looks identical to Claude and theme
alignment degrades significantly.

### `_keyword_matches` (int)

How many of the day's theme keywords appear in `title + summary`.
Used to identify "strong match" articles for the deep dive.

```python
# themes.json lives in curated-podcast-generator/config/themes.json
# Keywords per theme, e.g. for Tuesday (Working Lands & Industry):
# ["forestry","ranching","mining","agriculture","lumber","cattle",
#  "resource","farming","timber","sawmill","crop","harvest","livestock","industrial"]

def compute_keyword_matches(article, theme_keywords):
    text = (article["title"] + " " + article.get("summary", "")).lower()
    return sum(1 for kw in theme_keywords if kw in text)
```

### `_boosted_score` (int, 0–100)

Theme-relevance score used to sort articles and shown in the prompt so
Claude can weight its selection. A simple formula that works well:

```python
def compute_boosted_score(article, theme_keywords):
    hits = compute_keyword_matches(article, theme_keywords)
    base = article.get("ai_score", 0)           # existing interest score
    return min(100, hits * 20 + base * 0.3)     # tune multipliers as needed
```

Sort `items` by `_boosted_score` descending before writing the feed.

### `_is_bonus` (bool)

`true` for articles that don't match the day's theme at all. The generator
renders bonus items differently in the prompt ("Also worth noting today…")
so they don't crowd out on-theme stories.

```python
item["_is_bonus"] = compute_keyword_matches(article, theme_keywords) == 0
    and source_is_not_local(article)   # always include local BC sources
```

Local BC sources (Williams Lake Tribune, Quesnel Cariboo Observer, etc.)
should **never** be marked `_is_bonus` on the Saturday feed regardless of
keyword score — local news is the point of that day.

### `_podcast.theme_description` (string)

The generator injects this into the generation prompt as framing context
for the hosts. It's currently empty. Add a 2–3 sentence editorial angle
per day, written for the hosts, e.g.:

```json
{
  "_podcast": {
    "theme": "Working Lands & Industry",
    "theme_description": "Today's focus is the resource economy that defines
      the Cariboo — forestry, ranching, mining, and the agricultural tech
      helping these industries adapt. Look for stories about the people and
      technology keeping rural BC's working landscape viable, including
      tensions between extraction and stewardship."
  }
}
```

## Source gaps by theme (highest editorial impact)

Several themes produce weak episodes because the feed has no dedicated
sources for them. Add these RSS/JSON sources to the relevant day feeds.

### Saturday — Cariboo Voices & Local News (highest priority)
These sources produce daily Cariboo/BC content; Saturday's feed should be
predominantly local even without keyword filtering.
- Williams Lake Tribune RSS
- Quesnel Cariboo Observer RSS
- 100 Mile Free Press RSS
- My Cariboo Now (mycariboonow.com)
- My East Kootenay Now

### Thursday — Indigenous Lands & Innovation
- IndigiNews (indiginews.com)
- Yellowhead Institute (yellowheadinstitute.org)
- APTN News (aptnnews.ca)
- First Nations Technology Council news

### Tuesday — Working Lands & Industry
- BC Cattlemen's Association news
- BC Lumber Trade Council / COFI
- BC Ministry of Forests news (BC Gov News filtered)
- The Narwhal (mining/forestry tags)

### Friday — Wild Spaces & Outdoor Life
- The Narwhal (already partial — add conservation/wildlife tags)
- BC Wildfire Service (bcwildfire.ca/news)
- BC Parks news feed
- Haida Gwaii Observer (for coastal/wilderness angle)

### Monday — Arts, Culture & Digital Storytelling
This is the hardest theme to feed organically; generic tech news dominates.
- CBC Arts RSS
- BC Arts Council news
- Spacing Magazine (spacing.ca)
- Local festival/event feeds (Williams Lake, Quesnel)

### Sunday — Resilient Rural Futures
- BC Gov News (already partial — include infrastructure/energy tags)
- BCBC Infrastructure news
- Rural Municipalities of BC news
- Connecting BC broadband updates

## Expected item schema (complete)

```json
{
  "title": "[Source Name] Article headline here",
  "url": "https://example.com/article",
  "summary": "Two or three sentence description of the article content.",
  "ai_score": 72,
  "date_published": "2026-03-25T14:30:00+00:00",
  "authors": [{ "name": "Williams Lake Tribune" }],
  "_keyword_matches": 3,
  "_boosted_score": 67,
  "_is_bonus": false
}
```

## Complete feed envelope

```json
{
  "_podcast": {
    "theme": "Working Lands & Industry",
    "theme_description": "2–3 sentences of editorial framing for the hosts."
  },
  "items": [
    { "...": "theme articles first, sorted by _boosted_score desc" },
    { "...": "bonus articles last, _is_bonus: true" }
  ]
}
```

## Suggested implementation order

1. **Add `_keyword_matches`, `_boosted_score`, `_is_bonus` to all feeds**
   — unblocks the client-side improvements already in podcast-generator;
   immediate improvement across all 7 themes. Themes.json keyword lists
   are the source of truth.

2. **Add local news sources for Saturday and Thursday**
   — highest editorial impact; these two themes are currently weakest.

3. **Add `theme_description` to `_podcast` metadata**
   — low effort; improves host framing for every episode.

4. **Add remaining local/specialist sources by theme**
   — incremental; can be done one theme at a time.
