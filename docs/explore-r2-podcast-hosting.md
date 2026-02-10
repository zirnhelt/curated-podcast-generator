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

## Custom Domain (Vanity URL)

### Without a Custom Domain

R2 provides a development subdomain:

```
https://pub-<hash>.r2.dev/podcasts/podcast_audio_2026-02-10_example.mp3
```

Functional but not pretty. Fine for RSS feeds (listeners never see the URL).

### With a Custom Domain

If you have a domain on Cloudflare (e.g., `cariboosignals.com`), you can map a
subdomain to the R2 bucket:

```
https://audio.cariboosignals.com/podcast_audio_2026-02-10_example.mp3
```

**Requirements:**
- Domain must be added as a zone in your Cloudflare account
- Can use a new domain (~$10-15/year for a `.com`) or a free subdomain of an
  existing Cloudflare-managed domain
- Setup: R2 bucket → Settings → Custom Domains → Add domain → Cloudflare
  auto-creates the DNS record

**Bonus with custom domain:**
- Cloudflare Cache (Smart Tiered Caching) accelerates repeated downloads
- WAF rules, bot management, access controls available
- Looks professional for directory submissions

### Recommendation

Start with the `r2.dev` URL — it works immediately and costs nothing.
Add a custom domain later if you want a cleaner look for directory listings.
Podcast apps (Apple Podcasts, Spotify, Pocket Casts) don't display the raw
audio URL to listeners, so vanity matters less than you'd think.

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
  "audio_base_url": "https://pub-<hash>.r2.dev/"
}
```

Or with a custom domain:

```json
{
  "audio_base_url": "https://audio.cariboosignals.com/"
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

1. **Create Cloudflare account** (free) at cloudflare.com
2. **Create R2 bucket** named `cariboo-signals`
3. **Enable public access** on the bucket (Settings → Public Access → Allow)
4. **Note the `r2.dev` URL** assigned to the bucket
5. **Create R2 API token** with read/write permissions for the bucket
6. **Add secrets** to GitHub repo (Settings → Secrets → Actions)
7. **Update `config/podcast.json`** with `audio_base_url`
8. **(Optional)** Add a custom domain if you have one on Cloudflare

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
   Add custom domain if desired. Submit to podcast directories.

4. **Phase 4:** Clean MP3s from git history with `git filter-repo` to reclaim repo size
   (optional, destructive — only if repo size becomes a real problem).

## Open Questions

- [ ] Do you want to register a domain (e.g., `cariboosignals.com`) or is the `r2.dev` URL fine?
- [ ] Should the website audio player also point to R2, or keep serving from GitHub Pages during transition?
- [ ] How many historical episodes (currently 20) should be migrated to R2?
- [ ] Is a Cloudflare account acceptable, or do you prefer a different S3-compatible provider (Backblaze B2, etc.)?
