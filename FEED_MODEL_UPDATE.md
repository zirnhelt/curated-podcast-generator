# Feed Model Update — Multi-Feed Architecture

**Date:** 2026-02-16
**Status:** Implemented

## Summary

The podcast generator now uses **7 separate themed feeds** (one per day of the week) instead of a single rotating daily feed. This change improves content consistency and quality by maintaining persistent themed feeds with rolling 7-day article caches.

---

## Breaking Changes

### Feed Structure

**Before:**
- ❌ Single file: `feed-podcast.json`
- Content rotated daily based on current day
- Article pool: last 48 hours only
- Each day got different themed content from a limited pool

**After:**
- ✅ Seven files: `feed-podcast-{dayname}.json` (monday through sunday)
- Each feed has persistent themed content
- Article pool: rolling 7-day cache
- Consistent thematic curation from weekly articles
- Updates 3x daily (6 AM, 2 PM, 10 PM Pacific)

### Feed Naming Convention

```
feed-podcast-monday.json
feed-podcast-tuesday.json
feed-podcast-wednesday.json
feed-podcast-thursday.json
feed-podcast-friday.json
feed-podcast-saturday.json
feed-podcast-sunday.json
```

---

## Implementation Details

### Code Changes

**1. New day mapping constant:**
```python
DAY_NAMES = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
```

**2. Dynamic feed URL function:**
```python
def get_podcast_feed_url(weekday):
    """Get the podcast feed URL for a specific day of the week.

    Each day has its own persistent themed feed with a rolling 7-day article cache.
    Updates occur 3x daily (6 AM, 2 PM, 10 PM Pacific).

    Args:
        weekday: Integer 0-6 (0=Monday, 6=Sunday)

    Returns:
        URL string for that day's feed (e.g., feed-podcast-monday.json)
    """
    day_name = DAY_NAMES[weekday]
    return f"{SUPER_RSS_BASE_URL}/feed-podcast-{day_name}.json"
```

**3. Updated fetch function:**
- `fetch_podcast_feed()` now accepts a `weekday` parameter
- Fetches the appropriate day-specific feed
- Logs which day's feed is being fetched

**4. Main workflow update:**
- Passes `today_weekday` (0-6) to `fetch_podcast_feed()`
- Automatically selects the correct feed based on current day

### Fallback Behavior

The generator maintains backward compatibility:
- If podcast feeds are unavailable, falls back to legacy category feeds
- Uses `scored_articles_cache.json` + category feeds as before
- Graceful error handling with clear logging

---

## Benefits

### For Content Quality

1. **Thematic consistency:** Each feed curates from a full week of articles, not just 48 hours
2. **Better article selection:** More candidates = better theme matches
3. **No more off-theme filler:** Light news days don't force random articles into the theme

### For Consumers

1. **Predictable themes:** Subscribe to specific feeds for consistent content
2. **Multiple subscription options:** Can follow one theme or all seven
3. **Better discovery:** Each feed has a clear focus

### For Maintenance

1. **Easier debugging:** Each feed is independent
2. **Gradual rollout:** Can test individual feeds
3. **Better caching:** 7-day rolling cache improves article availability

---

## Migration Guide

### For Podcast Generator

**No action required.** The generator automatically uses the new feed model. The code changes are backward-compatible:

1. Generator detects current day (0-6)
2. Fetches appropriate `feed-podcast-{dayname}.json`
3. Falls back to category feeds if podcast feed unavailable

### For Feed Consumers

If you were subscribed to `feed-podcast.json`:
- **Action:** Subscribe to one or more themed feeds instead
- **Options:** Pick specific days/themes you want, or subscribe to all 7
- **Benefit:** More consistent content vs. daily theme rotation

### For Super RSS Feed (Upstream)

The upstream feed system must generate all 7 feeds:
- Maintain `podcast_articles_cache.json` (7-day rolling cache)
- Generate each feed 3x daily
- Tag articles with theme relevance metadata
- Include `_podcast.scoring_method` with `"weekly_cache"` indicator

---

## Testing Checklist

- [x] Code compiles without errors
- [ ] Generator fetches Monday feed correctly
- [ ] Generator fetches Sunday feed correctly
- [ ] Fallback to category feeds works if podcast feed unavailable
- [ ] Logging shows correct day name
- [ ] Generated episodes use correct theme
- [ ] RSS feed generation works
- [ ] Citations align with fetched articles

---

## Related Files

- `podcast_generator.py`: Main implementation
- `README.md`: Updated dependencies and themes sections
- `config/themes.json`: Theme definitions (unchanged)
- `config_loader.py`: Theme loading helpers (unchanged)

---

## Upstream Dependencies

This change assumes the Super RSS Feed system generates all 7 feeds. If feeds are missing, the generator falls back gracefully to the legacy category feed system.

For coordination with the upstream feed system, see the [super-rss-feed repository](https://github.com/zirnhelt/super-rss-feed).
