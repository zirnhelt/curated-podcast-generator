# Exploration: Cloudflare R2 for Podcast Audio Hosting

## Problem

Audio files (MP3, 4-20 MB each) are committed to git and served from GitHub Pages.
This causes git repo bloat and hits GitHub Pages soft bandwidth limits.
There are no podcast analytics, and directory submission (Apple/Spotify) requires a
stable, reliable audio CDN — GitHub Pages isn't designed for this.

## Proposed Solution

Upload MP3 files to **Cloudflare R2** instead of committing them to git.
Update the RSS feed `<enclosure>` URLs to point to R2.
Keep everything else (GitHub Actions generation, GitHub Pages for website/RSS) the same.

### Why R2

- **$0 egress** — no bandwidth fees, ever (the killer feature vs S3/GCS)
- **10 GB free storage** — at ~150 MB for 10 episodes, we'll never exceed this
- **10 million free reads/month** — podcast downloads won't come close
- **S3-compatible API** — works with boto3, easy to integrate
- **Cloudflare CDN built-in** — fast delivery worldwide
- **Canadian-accessible** — Cloudflare has Canadian PoPs (Vancouver, Toronto, Montreal)

### Estimated Monthly Cost

$0. The free tier covers this project's scale indefinitely.

## Custom Domain: `cariboosignals.ca`

Domain `cariboosignals.ca` is already on Cloudflare. Map a subdomain to the R2 bucket:

```
https://podcast.cariboosignals.ca/podcasts/podcast_audio_2026-02-10_example.mp3
```

**Setup** (one-time, in Cloudflare dashboard):
1. R2 bucket → Settings → Custom Domains → Add
2. Enter `podcast.cariboosignals.ca`
3. Cloudflare auto-creates the CNAME DNS record
4. Wait a few minutes for status to go from "Initializing" to "Active"

**Benefits of custom domain over `r2.dev`:**
- Cloudflare Cache (Smart Tiered Caching) — accelerates repeated downloads
- WAF rules, bot management, access controls available if needed later
- Professional appearance for directory submissions
- No need for the `r2.dev` public access toggle at all

The RSS feed URL can also move to the custom domain later if desired:

```
https://cariboosignals.ca/feed.xml          (via Cloudflare Pages or Worker)
https://podcast.cariboosignals.ca/feed.xml    (served from R2 alongside audio)
```

For now, keeping the RSS on GitHub Pages is fine — directories only need a
stable URL they can poll.

## What Changes

### Files Modified

#### 1. `podcast_generator.py` — RSS feed generation (~10 lines changed)

The enclosure URL currently uses `podcast_config["url"] + episode["audio_url_path"]`,
which resolves to:

```
https://zirnhelt.github.io/curated-podcast-generator/podcasts/podcast_audio_2026-02-10_example.mp3
```

Change to use a new `audio_base_url` config value:

```python
# Before (line 1017):
f'<enclosure url="{podcast_config["url"] + episode["audio_url_path"]}" ...'

# After:
audio_base = podcast_config.get("audio_base_url", podcast_config["url"])
f'<enclosure url="{audio_base + episode["audio_url_path"]}" ...'
```

#### 2. `podcast_generator.py` — new upload function (~30 lines)

Add a function to upload the MP3 to R2 after audio generation:

```python
import boto3
from botocore.config import Config

def upload_to_r2(file_path, object_key):
    """Upload an audio file to Cloudflare R2."""
    r2 = boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['CF_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    r2.upload_file(
        file_path,
        os.environ.get("R2_BUCKET_NAME", "cariboo-signals"),
        object_key,
        ExtraArgs={"ContentType": "audio/mpeg"},
    )
    print(f"  Uploaded {object_key} to R2")
```

Call it after audio assembly:

```python
# After the MP3 is written to disk:
if os.environ.get("R2_ACCESS_KEY_ID"):
    upload_to_r2(output_path, f"podcasts/{os.path.basename(output_path)}")
```

#### 3. `config/podcast.json` — add audio base URL

```json
{
  "audio_base_url": "https://podcast.cariboosignals.ca/"
}
```

Falls back to the existing GitHub Pages URL if not set.

#### 4. `.github/workflows/daily-podcast.yml` — add R2 secrets, remove MP3 from git

**Add secrets to the generation step:**

```yaml
- name: Generate daily podcast
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
    CF_ACCOUNT_ID: ${{ secrets.CF_ACCOUNT_ID }}
    R2_ACCESS_KEY_ID: ${{ secrets.R2_ACCESS_KEY_ID }}
    R2_SECRET_ACCESS_KEY: ${{ secrets.R2_SECRET_ACCESS_KEY }}
    R2_BUCKET_NAME: cariboo-signals
```

**Stop committing MP3s to git (line 90):**

```yaml
# Before:
git add podcasts/podcast_audio_*.mp3 podcasts/podcast_script_*.txt podcasts/citations_*.json

# After:
git add podcasts/podcast_script_*.txt podcasts/citations_*.json
```

**Stop copying MP3s to GitHub Pages output (line 103):**

```yaml
# Remove this line:
cp podcasts/podcast_audio_*.mp3 output/podcasts/ 2>/dev/null || echo "No audio files"
```

#### 5. `requirements.txt` — add boto3

```
boto3
```

### Files NOT Changed

- `generate_html.py` — website player can use the RSS feed URLs or be updated
  separately
- `config/hosts.json`, `themes.json`, etc. — no changes
- `dedup_articles.py` — no changes
- `podcasts/episode_memory.json` — no changes
- Test files — add a test for the upload function

## New GitHub Secrets Required

| Secret | Where to Get It |
|--------|----------------|
| `CF_ACCOUNT_ID` | Cloudflare Dashboard → Overview → Account ID (right sidebar) |
| `R2_ACCESS_KEY_ID` | Cloudflare Dashboard → R2 → Manage R2 API Tokens → Create |
| `R2_SECRET_ACCESS_KEY` | Same as above (shown once at creation) |

## One-Time Setup Steps

1. ~~Create Cloudflare account~~ ✅ Done
2. ~~Add domain to Cloudflare~~ ✅ Done (`cariboosignals.ca`)
3. **Create R2 bucket** named `cariboo-signals`
4. **Add custom domain** `podcast.cariboosignals.ca` to the bucket (auto-creates DNS)
5. **Create R2 API token** with read/write permissions for the bucket
6. **Add secrets** to GitHub repo (Settings → Secrets → Actions)
7. **Update `config/podcast.json`** with `audio_base_url: "https://podcast.cariboosignals.ca/"`

## Directory Submission

Once the RSS feed serves audio from R2 (a proper CDN), submit to directories:

| Directory | How | Cost |
|-----------|-----|------|
| Apple Podcasts | [podcastsconnect.apple.com](https://podcastsconnect.apple.com) — submit RSS URL, needs Apple ID | Free |
| Spotify | [podcasters.spotify.com](https://podcasters.spotify.com) — submit RSS URL | Free |
| Google Podcasts | Automatic if RSS is valid and discoverable | Free |
| Pocket Casts | [pocketcasts.com/submit](https://pocketcasts.com/submit) — submit RSS URL | Free |
| Amazon Music | [music.amazon.com/podcasters](https://music.amazon.com/podcasters) — submit RSS URL | Free |

These are one-time submissions. Directories poll the RSS feed automatically for new episodes.

## Migration Path

This can be done incrementally:

1. **Phase 1:** Set up R2 bucket and upload function. Upload new episodes to R2
   AND continue committing to git. Point RSS `<enclosure>` at R2.
   Verify episodes play correctly from R2 URLs.

2. **Phase 2:** Stop committing MP3s to git. Remove MP3 copy from gh-pages deploy.
   Old episodes still served from GitHub Pages (existing URLs in RSS still work).

3. **Phase 3:** Optionally upload historical episodes to R2 and update old RSS entries.
   Submit to podcast directories.

4. **Phase 4:** Clean MP3s from git history with `git filter-repo` to reclaim repo size
   (optional, destructive — only if repo size becomes a real problem).

## Open Questions

- [ ] Should the website audio player also point to R2, or keep serving from GitHub Pages during transition?
- [ ] How many historical episodes should be migrated to R2?
- [ ] Should the RSS feed eventually move to `cariboosignals.ca` too, or stay on GitHub Pages?
