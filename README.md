# Cariboo Signals — Podcast Generator

AI-generated daily podcast about technology and society in rural BC. Two hosts (Riley and Casey) discuss curated news and themed deep dives, assembled from the [Super RSS Feed](https://github.com/zirnhelt/super-rss-feed) system.

**Live:** [https://zirnhelt.github.io/curated-podcast-generator/](https://zirnhelt.github.io/curated-podcast-generator/)

---

## How It Works

The pipeline runs daily at 5 AM Pacific via GitHub Actions:

```
Super RSS Feed (scored articles + category feeds)
        │
        ▼
  Fetch & deduplicate articles
        │
        ▼
  Select top stories + theme-based deep dive articles
        │
        ▼
  Claude generates podcast script (two-host conversation)
        │
        ▼
  Claude polishes script (reduces repetition, improves flow)
        │
        ▼
  OpenAI TTS renders each speaker segment
        │
        ▼
  pydub assembles audio: intro music → welcome → interval → news → interval → deep dive → outro
        │
        ▼
  RSS feed (podcast-feed.xml) + citations JSON + deploy to GitHub Pages
```

### Dependencies

This repo depends on the RSS feed system staying healthy. It pulls curated podcast feeds at runtime:

- **Day-specific podcast feeds:** `feed-podcast-{dayname}.json` (monday through sunday) — pre-scored, theme-sorted articles from a rolling 7-day cache
  - Each day has its own persistent themed feed
  - Updates 3x daily (6 AM, 2 PM, 10 PM Pacific)
  - Includes theme-matched articles and bonus (off-theme) picks

**Fallback:** If podcast feeds are unavailable, the generator falls back to:
- **Scored articles cache:** `scored_articles_cache.json` — article scores from Claude
- **Category feeds:** `feed-{local,ai-tech,climate,homelab,news,science,scifi}.json` — raw article data

If the RSS feed system is down or stale, the podcast generator will fail gracefully and exit with an error.

For guidance on coordinating changes across both repos, see [SIBLING_REPOS.md](SIBLING_REPOS.md).

---

## Setup

### 1. Fork and clone

```bash
git clone git@github.com:zirnhelt/curated-podcast-generator.git
cd curated-podcast-generator
```

### 2. Add secrets to GitHub

Go to **Settings → Secrets and variables → Actions** and add:

| Secret | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key |
| `OPENAI_API_KEY` | OpenAI API key (for TTS) |

### 3. Enable GitHub Pages

**Settings → Pages → Source:** Deploy from branch `gh-pages`, root `/`

### 4. Run it

The workflow triggers automatically on schedule. To run manually: **Actions → Daily Podcast Generation → Run workflow**.

---

## Configuration

Everything lives in `config/`. No need to touch the main scripts to tweak content or tone.

| File | What it controls |
|---|---|
| `podcast.json` | Title, description, RSS metadata, cover image path |
| `hosts.json` | Riley and Casey — bios, TTS voices, personality traits |
| `themes.json` | 7 rotating weekly themes (Mon–Sun) |
| `credits.json` | Credits text for RSS descriptions and the website |
| `interests.txt` | Scoring interests passed to Claude for article relevance |

### Themes

Themes rotate on a weekly cycle. Each day gets its own themed feed:

| Day | Theme | Description |
|---|---|---|
| Monday | Arts, Culture & Digital Storytelling | Local arts, festivals, creative economy, media |
| Tuesday | Working Lands & Industry | Forestry, ranching, mining, agriculture tech |
| Wednesday | Community Tech & Governance | Municipal networks, civic innovation, digital equity |
| Thursday | Indigenous Lands & Innovation | First Nations governance, land stewardship, language tech |
| Friday | Wild Spaces & Outdoor Life | Conservation, wildfire, backcountry recreation |
| Saturday | Cariboo Voices & Local News | Williams Lake, Quesnel, local community stories |
| Sunday | Resilient Rural Futures | Infrastructure, connectivity, sustainability |

### Hosts

- **Riley** (`nova` voice) — tech systems thinker, asks "how can this work here?"
- **Casey** (`echo` voice) — community development focus, asks "how does this serve people like us?"

Both are AI hosts. The script explicitly avoids personal/family references and keeps the focus on rural tech perspectives.

---

## Key Files

| File | Purpose |
|---|---|
| `podcast_generator.py` | Main script — orchestrates the full pipeline |
| `dedup_articles.py` | Cross-episode deduplication (checks last 7 days of citations) |
| `generate_html.py` | Generates `index.html` from config files |
| `fix_rss.py` | Standalone RSS regenerator — use if `podcast-feed.xml` gets corrupted |
| `config_loader.py` | Config file loader with helpers |
| `episode_memory.json` | Tracks recent episodes for continuity (21-day window) |
| `host_personality_memory.json` | Evolving host personality state |
| `cariboo-signals-{intro,interval,outro}.mp3` | Music segments (Sumo AI) |
| `cariboo-signals-full.mp3` | Full theme song (linked in credits) |

### Per-Episode Outputs

Each run produces three files:

- `podcast_audio_{date}_{theme}.mp3` — the episode
- `podcast_script_{date}_{theme}.txt` — the generated script
- `citations_{date}_{theme}.json` — sources, descriptions, and structured metadata

---

## Local Testing

```bash
# Create and activate venv (required on Linux Mint)
python3 -m venv venv
source venv/bin/activate

# Install dependencies (includes ffmpeg requirement — install separately if missing)
pip install -r requirements.txt

# Set API keys
export ANTHROPIC_API_KEY='your-key'
export OPENAI_API_KEY='your-key'

# Run
python podcast_generator.py
```

The script will skip generation if today's episode already exists. Delete the `podcast_audio_` and `podcast_script_` files for today if you want to regenerate.

**Note:** ffmpeg must be installed separately for pydub to work. On Mint:

```bash
sudo apt install ffmpeg
```

### Regenerate just the RSS feed

If the XML gets corrupted or you need to rebuild it from existing audio files:

```bash
python fix_rss.py
```

### Regenerate the website

```bash
python generate_html.py
```

---

## What the Workflow Actually Does

For reference, here's the GitHub Actions flow broken into reviewable steps:

1. **Checkout** the repo
2. **Install** Python 3.11 + ffmpeg + pip dependencies
3. **Download** `episode_memory.json`, `host_personality_memory.json`, and `podcast-feed.xml` from the live GitHub Pages site (these aren't reliably in the git history due to how the workflow commits)
4. **Run** `podcast_generator.py` with both API keys in the environment
5. **Commit** memory files + new episode files back to `main`
6. **Deploy** everything to `gh-pages` via peaceiris/actions-gh-pages

---

## Costs

- **Claude API:** ~$0.02–0.05 per episode (script generation + polish pass)
- **OpenAI TTS:** ~$0.05–0.10 per episode (30 min of audio across two voices)
- **Total:** roughly $0.10–0.15/day, or ~$3–4/month

---

## License

MIT
