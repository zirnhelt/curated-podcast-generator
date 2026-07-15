#!/usr/bin/env python3
"""Upload episode videos to YouTube via the Data API v3.

Credentials come from repo secrets exported as env vars: YT_CLIENT_ID,
YT_CLIENT_SECRET, YT_REFRESH_TOKEN (minted once locally with
scripts/youtube_oauth_setup.py). When any are missing, callers should treat
the pipeline as render-only — have_credentials() is the gate.

Note: until the Google Cloud project passes YouTube's API audit, uploads
from it are forced to private regardless of the requested privacyStatus.
Publish manually from YouTube Studio in the meantime.

Quota: videos.insert = 1600 units + captions.insert = 400 of the default
10,000/day — one episode/day uses ~20%.
"""

import json
import os
import time
from pathlib import Path

PODCASTS_DIR = Path(__file__).parent / "podcasts"
LEDGER_PATH = PODCASTS_DIR / "youtube_uploads.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",  # required by captions.insert
]

MAX_TITLE_LEN = 100
MAX_DESC_LEN = 5000
CATEGORY_SCIENCE_TECH = "28"
UPLOAD_RETRIES = 3


def have_credentials() -> bool:
    """True when all three YT_* secrets are present in the environment."""
    return all(os.getenv(k) for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))


def get_service():
    """Build an authenticated YouTube API client from env credentials."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def load_ledger() -> dict:
    if LEDGER_PATH.exists():
        with open(LEDGER_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_ledger(ledger: dict) -> None:
    with open(LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2)


def already_uploaded(date_str: str) -> bool:
    return date_str in load_ledger()


def _fmt_ts(seconds: float) -> str:
    """Seconds → YouTube chapter timestamp (M:SS or H:MM:SS)."""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _clean(text: str) -> str:
    """YouTube descriptions/titles reject angle brackets."""
    return text.replace("<", "(").replace(">", ")")


def build_metadata(citations: dict, chapters: list, privacy: str, date_str: str) -> dict:
    """videos.insert body from the episode's citations + chapters JSON."""
    from config_loader import load_podcast_config
    podcast_cfg = load_podcast_config()

    episode = (citations or {}).get("episode", {})
    segments = (citations or {}).get("segments", {})
    theme = episode.get("theme", "")

    title = episode.get("title") or podcast_cfg.get("title", "Cariboo Signals")
    title = _clean(f"{title} — {episode.get('formatted_date', date_str)}")[:MAX_TITLE_LEN]

    lines = [podcast_cfg.get("tagline", ""), ""]

    # Chapter timestamps → YouTube chapter markers (first must be 0:00)
    if chapters:
        if float(chapters[0].get("startTime", 0)) != 0:
            chapters = [{"startTime": 0, "title": chapters[0]["title"]}] + chapters[1:]
        lines += [f"{_fmt_ts(float(c['startTime']))} {c['title']}" for c in chapters]
        lines.append("")

    for seg_key, heading in (("news_roundup", "Stories covered"), ("deep_dive", "Deep dive sources")):
        articles = segments.get(seg_key, {}).get("articles", [])
        if articles:
            lines.append(f"{heading}:")
            lines += [f"• {a.get('title', '')} ({a.get('source', '')}) — {a.get('url', '')}"
                      for a in articles]
            lines.append("")

    base_url = podcast_cfg.get("audio_base_url", "")
    if base_url:
        lines.append(f"Podcast + transcripts: {podcast_cfg.get('url', base_url)}")
    if podcast_cfg.get("email"):
        lines.append(f"Corrections: {podcast_cfg['email']}")
    if podcast_cfg.get("copyright"):
        lines.append(podcast_cfg["copyright"])

    description = _clean("\n".join(lines))
    if len(description) > MAX_DESC_LEN:
        description = description[:MAX_DESC_LEN - 1] + "…"

    tags = ["Cariboo Signals", "rural BC", "podcast", "technology"]
    if theme:
        tags.append(theme[:100])

    return {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": CATEGORY_SCIENCE_TECH,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }


def upload_video(service, mp4_path: str, metadata: dict) -> str:
    """Resumable videos.insert with retries. Returns the video ID."""
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(mp4_path, mimetype="video/mp4",
                            resumable=True, chunksize=8 * 1024 * 1024)
    request = service.videos().insert(
        part="snippet,status", body=metadata, media_body=media
    )
    last_error = None
    for attempt in range(UPLOAD_RETRIES):
        try:
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    print(f"  ⬆️  Upload {int(status.progress() * 100)}%")
            return response["id"]
        except Exception as e:
            last_error = e
            wait = 2 ** (attempt + 1)
            print(f"  ⚠️  Upload attempt {attempt + 1} failed: {e} — retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"Upload failed after {UPLOAD_RETRIES} attempts: {last_error}")


def upload_captions(service, video_id: str, vtt_path: str) -> None:
    """Attach the episode WebVTT transcript. Non-fatal: YouTube auto-captions are the fallback."""
    from googleapiclient.http import MediaFileUpload

    try:
        service.captions().insert(
            part="snippet",
            body={"snippet": {"videoId": video_id, "language": "en", "name": "English"}},
            media_body=MediaFileUpload(vtt_path, mimetype="text/vtt"),
        ).execute()
        print("  💬 Captions uploaded")
    except Exception as e:
        print(f"  ⚠️  Caption upload failed (YouTube auto-captions will apply): {e}")


def upload_episode(mp4_path: str, citations_path: str | None, chapters_path: str | None,
                   vtt_path: str | None, date_str: str, privacy: str = "unlisted") -> dict | None:
    """Full upload flow: metadata → video → captions → ledger. Returns ledger entry."""
    ledger = load_ledger()
    if date_str in ledger:
        print(f"✅ {date_str} already uploaded: {ledger[date_str]['url']}")
        return ledger[date_str]

    citations = {}
    if citations_path and os.path.exists(citations_path):
        with open(citations_path, encoding="utf-8") as f:
            citations = json.load(f)
    chapters = []
    if chapters_path and os.path.exists(chapters_path):
        with open(chapters_path, encoding="utf-8") as f:
            chapters = json.load(f).get("chapters", [])

    metadata = build_metadata(citations, chapters, privacy, date_str)
    service = get_service()

    print(f"⬆️  Uploading to YouTube ({privacy}): {metadata['snippet']['title']}")
    video_id = upload_video(service, mp4_path, metadata)
    url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"✅ Uploaded: {url}")

    if vtt_path and os.path.exists(vtt_path):
        upload_captions(service, video_id, vtt_path)

    from datetime import datetime, timezone
    entry = {"video_id": video_id, "url": url,
             "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    ledger[date_str] = entry
    save_ledger(ledger)
    return entry
