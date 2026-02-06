# Sibling Repos: Podcast Generator + Super RSS Feed

This project depends on a sibling repository at runtime. This document covers how the two repos relate and how to coordinate changes across them.

## The Relationship

```
super-rss-feed (producer)          curated-podcast-generator (consumer)
─────────────────────────          ────────────────────────────────────
Scores and categorizes articles    Fetches scored articles at runtime
Deploys JSON to GitHub Pages  ──►  Reads JSON from GitHub Pages URL
```

There is no git submodule, no build-time link, and no shared package. The coupling is entirely through published HTTP endpoints on GitHub Pages.

### Data Endpoints

The podcast generator fetches from `https://zirnhelt.github.io/super-rss-feed/`:

| Endpoint | Purpose |
|---|---|
| `scored_articles_cache.json` | Article scores from Claude |
| `feed-local.json` | Local news articles |
| `feed-ai-tech.json` | AI and technology articles |
| `feed-climate.json` | Climate articles |
| `feed-homelab.json` | Homelab articles |
| `feed-news.json` | General news articles |
| `feed-science.json` | Science articles |
| `feed-scifi.json` | Sci-fi articles |

These URLs are configured via `SUPER_RSS_BASE_URL` in `podcast_generator.py`.

---

## Deployment Order

The feed system must deploy before the podcast generator consumes it. For breaking changes to the JSON format:

1. Update `super-rss-feed` with the new format
2. Deploy it to GitHub Pages
3. Verify the data is live at the endpoint URLs above
4. Update `curated-podcast-generator` to consume the new format
5. Test locally, then deploy

**Additive changes** (new fields) are safe — the podcast generator ignores fields it doesn't use. **Removals or renames** are breaking — check which fields `podcast_generator.py` actually reads before changing them.

---

## Local Development Across Both Repos

### Directory layout

Clone both repos as siblings:

```
~/
├── curated-podcast-generator/
└── super-rss-feed/
```

### Testing integration locally

To test changes to the feed format without deploying:

```bash
# Serve super-rss-feed output locally
cd ~/super-rss-feed
python3 -m http.server 8000

# Temporarily point the podcast generator at localhost
# In podcast_generator.py, change SUPER_RSS_BASE_URL to:
#   "http://localhost:8000"
# Then run the generator as normal
cd ~/curated-podcast-generator
python podcast_generator.py
```

Remember to revert `SUPER_RSS_BASE_URL` before committing.

---

## Keeping Changes in Sync

When a change spans both repos:

- **Reference the other repo** in each commit message (e.g., "Requires zirnhelt/super-rss-feed#12" or "Companion to curated-podcast-generator@abc123")
- **Don't merge the consumer side** (podcast generator) until the producer side (super-rss-feed) is deployed and verified
- **Check shared concepts** — category names and scoring criteria appear in both repos. When updating categories or interests in one, check the other for corresponding changes. The ROADMAP tracks "shared interest/scoring config" as a long-term goal

---

## Monitoring and Failure

The podcast generator exits cleanly if the feed data is unavailable or stale. This is by design — a feed outage should never produce a bad episode.

To verify integration after deploying feed changes:

1. Check that the GitHub Pages data is live (visit the endpoint URLs)
2. Manually trigger the podcast workflow: **Actions → Daily Podcast Generation → Run workflow**
3. Review the workflow logs for fetch errors

The daily GitHub Actions run is the primary signal that both repos are healthy together. A failed run usually means the feed data is stale, unreachable, or has changed format.
