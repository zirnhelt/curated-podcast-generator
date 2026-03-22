# Cariboo Signals — Podcast Generator

AI-generated daily podcast about technology and society in rural BC. Two hosts (Riley and Casey) discuss curated news and themed deep dives, assembled from the [Super RSS Feed](https://github.com/zirnhelt/super-rss-feed) system.

**Live:** [https://zirnhelt.github.io/curated-podcast-generator/](https://zirnhelt.github.io/curated-podcast-generator/)

---

## Shows

### Cariboo Signals (Daily)

The flagship show. Riley and Casey cover top stories and a themed deep dive, generated daily at 5 AM Pacific.

### Cariboo Weekends

- **Cariboo Saturday Morning** — Riley hosts a CBC Radio 1-style Saturday show: CBC news podcasts (World Report → BC Today → CBC Kamloops), interspersed with Canadian indie music from Jamendo.
- **Cariboo Sunday Morning** — Casey hosts a cultural Sunday show: CBC cultural podcasts (q, Unreserved), interspersed with indie music. Mirrors the CBC Radio 1 Sunday listening experience.

Both weekend shows include AI-generated host commentary, Cariboo weather, and track IDs. Requires a free Jamendo API key.

### Bespoke (On-Demand)

Long-form debate episodes (~35–45 min) on user-curated topics. Tag URLs with a topic, optionally expand sources via Brave Search, and generate a full debate episode. No news roundup — the whole episode is the deep dive.

```bash
python generate_bespoke.py --tag "billionaires"
python generate_bespoke.py --tag "middle-east" --threshold 2
```

---

## How It Works (Daily Show)

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

Optional (for weekend shows and bespoke):

| Secret | Value |
|---|---|
| `JAMENDO_CLIENT_ID` | Jamendo API key — free at devportal.jamendo.com |
| `BRAVE_SEARCH_API_KEY` | Brave Search API key — for bespoke source expansion |

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
| `prompts.json` | Claude prompt templates |
| `notable_dates.json` | Local holidays and awareness dates for PSA selection |
| `psa_organizations.json` | Community organizations for PSA rotation |
| `psa_events.json` | Awareness events tied to PSA selection |
| `blocklist.json` | Domains or articles to exclude from selection |
| `ambient.json` | Ambient chime configuration per theme |
| `cariboo_saturday.json` | CBC feeds and Jamendo config for Saturday show |
| `cariboo_sunday.json` | CBC feeds and Jamendo config for Sunday show |
| `bespoke_config.json` | Bespoke show settings (R2 upload, base URL, etc.) |
| `bespoke_hosts.json` | Host overrides for bespoke episodes |

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
| `podcast_generator.py` | Main script — orchestrates the full daily pipeline |
| `cariboo_saturday.py` | Saturday show generator — CBC news + Jamendo music, Riley hosts |
| `cariboo_sunday.py` | Sunday show generator — CBC cultural podcasts + Jamendo music, Casey hosts |
| `generate_bespoke.py` | Bespoke long-form debate generator — tag-driven, Brave Search expansion |
| `dedup_articles.py` | Cross-episode deduplication (checks last 7 days of citations) |
| `generate_html.py` | Generates `index.html` from config files |
| `fix_rss.py` | Standalone RSS regenerator — use if `podcast-feed.xml` gets corrupted |
| `generate_cariboo_saturday_feed.py` | Builds `cariboo-saturday-feed.xml` from episode files |
| `generate_cariboo_sunday_feed.py` | Builds `cariboo-sunday-feed.xml` from episode files |
| `generate_ambient_chimes.py` | Generates 7 themed ambient chimes from the main theme song |
| `config_loader.py` | Config file loader with helpers |
| `psa_selector.py` | Selects a community PSA organization for each episode |
| `weather.py` | Fetches Cariboo weather from Open-Meteo (no API key required) |
| `seed.py` | Bookmark articles or log thoughts for future episodes |
| `validate_feed.py` | Validates `podcast-feed.xml` against Apple Podcasts requirements |
| `ambient.py` | Theme-aware ambient transition sounds (falls back to interval music) |
| `episode_memory.json` | Tracks recent episodes for continuity (21-day window) |
| `host_personality_memory.json` | Evolving host personality state |
| `cariboo-signals-{intro,interval,outro}.mp3` | Music segments (Sumo AI) |
| `cariboo-signals-full.mp3` | Full theme song (linked in credits) |
| `bespoke-theme-{intro,interval,outro}.mp3` | Bespoke show music segments |
| `ambient/` | Themed ambient chime MP3s (one per weekly theme) |

### Per-Episode Outputs

Each daily run produces three files:

- `podcast_audio_{date}_{theme}.mp3` — the episode
- `podcast_script_{date}_{theme}.txt` — the generated script
- `citations_{date}_{theme}.json` — sources, descriptions, and structured metadata

Weekend shows produce:

- `podcasts/cariboo_saturday_{date}.mp3`
- `podcasts/cariboo_sunday_{date}.mp3`

Bespoke episodes are stored in `podcasts/bespoke/`.

---

## Tests

112 unit tests across 6 modules. No API keys or network access required — heavy dependencies (anthropic, openai, pydub) are stubbed automatically via `conftest.py`.

```bash
pip install pytest
python -m pytest tests/ -v
```

| Module | Tests | What it covers |
|---|---|---|
| `test_ambient.py` | 5 | Ambient config loading, theme transitions, fallbacks |
| `test_config_loader.py` | 12 | Loading each config file, voice/theme helpers, caching |
| `test_dedup.py` | 10 | Title normalization, similarity scoring, evolving story context |
| `test_podcast_generator.py` | 30 | Article scoring, script parsing, pacing tags, heuristic gaps, host selection |
| `test_psa_selector.py` | 38 | PSA org selection, event matching, round-robin rotation, notable dates, config validation |
| `test_weather.py` | 17 | Weather fetching, driving impact detection, prompt formatting, WMO codes |

**Note:** `tests/` is in `.gitignore` (via `*test*`). Use `git add -f tests/` when committing test changes.

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

# Run daily show
python podcast_generator.py

# Run weekend shows (also requires JAMENDO_CLIENT_ID)
python cariboo_saturday.py
python cariboo_sunday.py

# Run bespoke (optionally set BRAVE_SEARCH_API_KEY for source expansion)
python generate_bespoke.py --tag "your-topic"
```

The daily script will skip generation if today's episode already exists. Delete the `podcast_audio_` and `podcast_script_` files for today if you want to regenerate.

**Note:** ffmpeg must be installed separately for pydub to work. On Mint:

```bash
sudo apt install ffmpeg
```

### Regenerate just the RSS feed

If the XML gets corrupted or you need to rebuild it from existing audio files:

```bash
python fix_rss.py
python generate_cariboo_saturday_feed.py
python generate_cariboo_sunday_feed.py
```

### Regenerate the website

```bash
python generate_html.py
```

### Validate the RSS feed

```bash
python validate_feed.py
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

Weekend shows and bespoke episodes add to this based on usage.

---

## License

MIT
