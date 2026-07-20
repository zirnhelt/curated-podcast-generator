#!/usr/bin/env python3
"""Render a slide-based video version of a daily episode for YouTube.

Visuals: per-section slides (timed by the episode's chapters JSON) with the
show's cover art and citation headlines, an audio-reactive waveform
(ffmpeg showwaves — the only motion, so encoding stays fast), and a
speaker badge synced to the per-turn timings in video_timeline_*.json.

Rollout is gated on YouTube credentials: when the YT_* env vars are unset
the script runs in render-only mode and leaves the MP4 in podcasts/ for
the workflow to attach as a run artifact. Video generation is additive —
every failure path exits 0 so the audio pipeline is never blocked.

Usage:
    python video_generator.py [--date YYYY-MM-DD] [--skip-upload]
                              [--privacy private|unlisted|public] [--keep-video]
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from config_loader import load_hosts_config, load_podcast_config

PODCASTS_DIR = Path(__file__).parent / "podcasts"
ASSETS_DIR = Path(__file__).parent

WIDTH, HEIGHT = 1280, 720
FPS = 24
WAVE_HEIGHT = 100
# Floor for per-slide screen time when a chapter is subdivided into N slides
MIN_SLIDE_S = 5.0
BADGE_MARGIN_X = 48
# Badge sits just above the waveform strip
BADGE_Y = HEIGHT - WAVE_HEIGHT - 88

BG_COLOR = (16, 20, 24)
FG_COLOR = (240, 240, 235)
MUTED_COLOR = (160, 168, 172)

DEFAULT_HOST_COLORS = {"riley": "#F2A65A", "casey": "#5AB5B2"}

_FONT_CANDIDATES = {
    False: [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ],
    True: [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ],
}


def _load_font(size: int, bold: bool = False):
    """Load DejaVu Sans (present on ubuntu-latest), falling back to PIL default."""
    for path in _FONT_CANDIDATES[bold]:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def hex_to_rgb(value: str) -> tuple:
    """'#F2A65A' → (242, 166, 90)."""
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def get_host_colors() -> dict:
    """Speaker → hex color from hosts.json, with hard-coded defaults."""
    colors = dict(DEFAULT_HOST_COLORS)
    try:
        for key, host in load_hosts_config().items():
            if host.get("color"):
                colors[key] = host["color"]
    except Exception:
        pass
    return colors


def pacific_today() -> str:
    return datetime.now(ZoneInfo("America/Vancouver")).strftime("%Y-%m-%d")


def load_episode_artifacts(date_str: str) -> dict:
    """Locate today's episode files. Raises FileNotFoundError if no audio."""
    audio_matches = sorted(glob.glob(str(PODCASTS_DIR / f"podcast_audio_{date_str}_*.mp3")))
    # Exclude the Azure comparison render (podcast_audio_*_azure.mp3)
    audio_matches = [m for m in audio_matches if not m.endswith("_azure.mp3")]
    if not audio_matches:
        raise FileNotFoundError(f"No audio for {date_str} in {PODCASTS_DIR}")
    audio = audio_matches[0]

    def _sidecar(prefix: str, ext: str) -> str | None:
        p = Path(audio)
        candidate = p.with_name(p.name.replace("podcast_audio_", f"{prefix}_").replace(".mp3", ext))
        return str(candidate) if candidate.exists() else None

    artifacts = {
        "date": date_str,
        "audio": audio,
        "chapters": _sidecar("podcast_chapters", ".json"),
        "citations": _sidecar("citations", ".json"),
        "timeline": _sidecar("video_timeline", ".json"),
        "vtt": _sidecar("podcast_transcript", ".vtt"),
        "mp4": _sidecar("podcast_video", ".mp4") or str(
            Path(audio).with_name(Path(audio).name.replace("podcast_audio_", "podcast_video_").replace(".mp3", ".mp4"))
        ),
    }
    return artifacts


def probe_duration(audio_path: str) -> float:
    """Audio duration in seconds via ffprobe (ships with ffmpeg)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", audio_path],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def pick_cover_image(date_str: str) -> Path:
    """Day-aware cover art, mirroring the RSS feed's weekend covers."""
    weekday = datetime.strptime(date_str, "%Y-%m-%d").weekday()
    if weekday == 5 and (ASSETS_DIR / "cariboo-saturday.png").exists():
        return ASSETS_DIR / "cariboo-saturday.png"
    if weekday == 6 and (ASSETS_DIR / "cariboo-sunday.png").exists():
        return ASSETS_DIR / "cariboo-sunday.png"
    return ASSETS_DIR / load_podcast_config().get("cover_image", "cariboo-signals.png")


def _wrap_text(draw, text: str, font, max_width: int, max_lines: int = 3) -> list:
    """Greedy word wrap; last line ellipsized if the text overflows."""
    words = text.split()
    lines, current = [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len(" ".join(lines).split()) < len(words):
        lines[-1] = lines[-1].rstrip(".,;") + "…"
    return lines


def _new_slide(cover: Image.Image | None, kicker: str, accent: tuple) -> tuple:
    """Base slide canvas: dark bg, cover art panel on the left, kicker line."""
    img = Image.new("RGB", (WIDTH, HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)
    text_x = 80
    if cover is not None:
        art = cover.copy()
        art.thumbnail((360, 360))
        img.paste(art, (80, 130))
        text_x = 500
    draw.rectangle([text_x, 118, text_x + 56, 124], fill=accent)
    draw.text((text_x, 140), kicker.upper(), font=_load_font(28, bold=True), fill=accent)
    return img, draw, text_x


def _article_slide(cover: Image.Image | None, kicker: str, accent: tuple, art: dict) -> Image.Image:
    """One full slide for a single cited article: headline, source, summary, URL."""
    img, draw, x = _new_slide(cover, kicker, accent)
    font_h2 = _load_font(34, bold=True)
    font_body = _load_font(28)
    font_small = _load_font(22)
    max_w = WIDTH - x - 80
    y = 200
    for line in _wrap_text(draw, art.get("title", ""), font_h2, max_w, max_lines=3):
        draw.text((x, y), line, font=font_h2, fill=FG_COLOR)
        y += 44
    if art.get("source"):
        draw.text((x, y), f"— {art['source']}", font=font_small, fill=MUTED_COLOR)
        y += 34
    y += 12
    for line in _wrap_text(draw, art.get("summary", ""), font_body, max_w, max_lines=4):
        draw.text((x, y), line, font=font_body, fill=MUTED_COLOR)
        y += 38
    url = art.get("url", "")
    if url:
        # URLs have no spaces, so word-wrap can't break them — truncate instead
        if draw.textlength(url, font=font_small) > max_w:
            while url and draw.textlength(url + "…", font=font_small) > max_w:
                url = url[:-1]
            url += "…"
        draw.text((x, HEIGHT - WAVE_HEIGHT - 40), url, font=font_small, fill=MUTED_COLOR)
    return img


def render_slides(chapters: list, citations: dict, audio_dur_s: float,
                  cover_path: Path, outdir: str) -> list:
    """Render one or more PNGs per chapter (weather + per-article slides
    subdivide the chapter span). Returns [(png_path, start_s, end_s), ...]."""
    podcast_cfg = load_podcast_config()
    title = podcast_cfg.get("title", "Cariboo Signals")
    tagline = podcast_cfg.get("tagline", "")
    accent = hex_to_rgb(get_host_colors().get("riley", "#F2A65A"))

    episode = (citations or {}).get("episode", {})
    segments = (citations or {}).get("segments", {})
    theme = episode.get("theme", "")
    formatted_date = episode.get("formatted_date", "")

    try:
        cover = Image.open(cover_path)
    except Exception:
        cover = None

    if not chapters:
        chapters = [{"startTime": 0, "title": "Introduction"}]

    font_h1 = _load_font(52, bold=True)
    font_h2 = _load_font(34, bold=True)
    font_body = _load_font(28)
    font_small = _load_font(22)

    slides = []
    for i, chapter in enumerate(chapters):
        start = float(chapter["startTime"])
        end = float(chapters[i + 1]["startTime"]) if i + 1 < len(chapters) else audio_dur_s
        if end <= start:
            continue
        section = chapter["title"]
        # Max slides this chapter's span can hold at MIN_SLIDE_S each
        budget = max(1, int((end - start) // MIN_SLIDE_S))
        imgs: list = []
        img, draw, x = _new_slide(cover, section, accent)
        y = 200

        if section in ("Cold Open", "Introduction"):
            for line in _wrap_text(draw, title, font_h1, WIDTH - x - 80, max_lines=2):
                draw.text((x, y), line, font=font_h1, fill=FG_COLOR)
                y += 64
            y += 8
            for line in _wrap_text(draw, tagline, font_body, WIDTH - x - 80, max_lines=2):
                draw.text((x, y), line, font=font_body, fill=MUTED_COLOR)
                y += 38
            y += 16
            if theme:
                draw.text((x, y), theme, font=font_h2, fill=accent)
                y += 48
            if formatted_date:
                draw.text((x, y), formatted_date, font=font_body, fill=MUTED_COLOR)
            imgs.append(img)

            # Weather is spoken during the welcome — give it the back half of
            # the Introduction span when the citations carry slide data.
            weather = segments.get("weather") or {}
            if section == "Introduction" and weather.get("locations") and budget >= 2:
                wimg, wdraw, wx = _new_slide(cover, weather.get("title", "Weather Check"), accent)
                wy = 200
                wdraw.text((wx, wy), "Cariboo Weather", font=font_h2, fill=FG_COLOR)
                wy += 58
                for loc in weather["locations"][:6]:
                    line = f"{loc['name']} — {loc['temp']}°, {loc['conditions']}"
                    for wrapped in _wrap_text(wdraw, line, font_body, WIDTH - wx - 240, max_lines=1):
                        wdraw.text((wx, wy), wrapped, font=font_body, fill=FG_COLOR)
                    wdraw.text((WIDTH - 210, wy + 4), f"H {loc['high']} / L {loc['low']}",
                               font=font_small, fill=MUTED_COLOR)
                    wy += 44
                wdraw.text((wx, HEIGHT - WAVE_HEIGHT - 40),
                           f"Weather data by {weather.get('source', 'Open-Meteo')}",
                           font=font_small, fill=MUTED_COLOR)
                imgs.append(wimg)

        elif section == "News Roundup":
            draw.text((x, y), "Today's stories", font=font_h2, fill=FG_COLOR)
            y += 58
            articles = segments.get("news_roundup", {}).get("articles", [])
            for art in articles:
                headline = art.get("title", "")
                source = art.get("source", "")
                lines = _wrap_text(draw, headline, font_body, WIDTH - x - 100, max_lines=2)
                draw.ellipse([x, y + 12, x + 10, y + 22], fill=accent)
                for line in lines:
                    draw.text((x + 26, y), line, font=font_body, fill=FG_COLOR)
                    y += 36
                if source:
                    draw.text((x + 26, y), f"— {source}", font=font_small, fill=MUTED_COLOR)
                    y += 30
                y += 10
                if y > HEIGHT - WAVE_HEIGHT - 90:
                    break
            imgs.append(img)
            for idx, art in enumerate(articles[:budget - 1], 1):
                imgs.append(_article_slide(
                    cover, f"News Roundup · {idx}/{len(articles)}", accent, art))

        elif section == "Deep Dive":
            dd = segments.get("deep_dive", {})
            heading = dd.get("title") or "Deep Dive"
            for line in _wrap_text(draw, heading, font_h2, WIDTH - x - 80, max_lines=2):
                draw.text((x, y), line, font=font_h2, fill=FG_COLOR)
                y += 44
            y += 14
            arts = dd.get("articles", [])
            question = dd.get("discussion", {}).get("central_question", "")
            if question:
                for line in _wrap_text(draw, question, font_body, WIDTH - x - 80, max_lines=5):
                    draw.text((x, y), line, font=font_body, fill=FG_COLOR)
                    y += 38
                y += 18
            for art in arts[:2]:
                for line in _wrap_text(draw, art.get("title", ""), font_small, WIDTH - x - 80, max_lines=2):
                    draw.text((x, y), line, font=font_small, fill=MUTED_COLOR)
                    y += 28
                if art.get("source"):
                    draw.text((x, y), f"— {art['source']}", font=font_small, fill=MUTED_COLOR)
                    y += 30
                y += 8
            imgs.append(img)
            for idx, art in enumerate(arts[:budget - 1], 1):
                imgs.append(_article_slide(
                    cover, f"Deep Dive · {idx}/{len(arts)}", accent, art))

        elif section == "Community Spotlight" and segments.get("community_spotlight", {}).get("org_name"):
            spot = segments["community_spotlight"]
            for line in _wrap_text(draw, spot["org_name"], font_h1, WIDTH - x - 80, max_lines=2):
                draw.text((x, y), line, font=font_h1, fill=FG_COLOR)
                y += 64
            y += 12
            for line in _wrap_text(draw, spot.get("description", ""), font_body, WIDTH - x - 80, max_lines=6):
                draw.text((x, y), line, font=font_body, fill=FG_COLOR)
                y += 38
            y += 16
            if spot.get("event_name"):
                draw.text((x, y), spot["event_name"], font=font_body, fill=MUTED_COLOR)
                y += 40
            if spot.get("website"):
                draw.text((x, y), spot["website"], font=font_small, fill=accent)
            imgs.append(img)

        elif section == "Credits":
            draw.text((x, y), title, font=font_h2, fill=FG_COLOR)
            y += 56
            for line in [
                f"Produced by {podcast_cfg.get('author', '')}",
                "Scripts by Claude · Audio by OpenAI TTS · Theme by Suno",
                podcast_cfg.get("url", ""),
                podcast_cfg.get("copyright", ""),
            ]:
                if line:
                    for wrapped in _wrap_text(draw, line, font_body, WIDTH - x - 80, max_lines=2):
                        draw.text((x, y), wrapped, font=font_body, fill=MUTED_COLOR)
                        y += 40
            imgs.append(img)

        else:  # Meta Moment, Spotlight without org data, anything new
            draw.text((x, y), section, font=font_h1, fill=FG_COLOR)
            y += 80
            if theme:
                draw.text((x, y), theme, font=font_body, fill=MUTED_COLOR)
            imgs.append(img)

        # Subdivide the chapter span evenly across its slides
        n = len(imgs)
        for j, im in enumerate(imgs):
            s = start + (end - start) * j / n
            e = start + (end - start) * (j + 1) / n
            png = os.path.join(outdir, f"slide_{i:02d}_{j:02d}.png")
            im.save(png)
            slides.append((png, s, e))
    return slides


def render_speaker_badges(outdir: str) -> dict:
    """Name-pill PNG per host (colored dot + name). Returns {speaker: png_path}."""
    hosts = load_hosts_config()
    colors = get_host_colors()
    font = _load_font(30, bold=True)
    badges = {}
    for key, host in hosts.items():
        name = host.get("name", key.title())
        color = hex_to_rgb(colors.get(key, "#888888"))
        img = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
        text_w = int(ImageDraw.Draw(img).textlength(name, font=font))
        w, h = text_w + 96, 60
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=30, fill=(10, 12, 14, 215))
        draw.ellipse([20, h // 2 - 10, 40, h // 2 + 10], fill=color)
        draw.text((58, h // 2 - 20), name, font=font, fill=(245, 245, 240, 255))
        png = os.path.join(outdir, f"badge_{key}.png")
        img.save(png)
        badges[key] = png
    return badges


def merge_speaker_spans(turns: list, speaker: str, gap_tolerance_s: float = 1.5) -> list:
    """[(start_s, end_s)] for a speaker, merging turns separated by short gaps."""
    spans = []
    for turn in turns:
        # speaker=None marks whole-section (Azure) spans with no per-turn boundaries
        if turn.get("speaker") is None or turn["speaker"] != speaker:
            continue
        start = turn["start_ms"] / 1000.0
        end = (turn["start_ms"] + turn["dur_ms"]) / 1000.0
        if spans and start - spans[-1][1] <= gap_tolerance_s:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end])
    return [(round(s, 2), round(e, 2)) for s, e in spans]


def build_enable_expr(turns: list, speaker: str) -> str:
    """ffmpeg overlay enable expression for a speaker's merged spans."""
    return "+".join(f"between(t,{s},{e})" for s, e in merge_speaker_spans(turns, speaker))


def write_concat_file(slides: list, outdir: str) -> str:
    """ffmpeg concat-demuxer list; the last file is repeated per the demuxer spec."""
    path = os.path.join(outdir, "slides.txt")
    with open(path, "w", encoding="utf-8") as f:
        for png, start, end in slides:
            f.write(f"file '{png}'\nduration {end - start:.3f}\n")
        f.write(f"file '{slides[-1][0]}'\n")
    return path


def build_ffmpeg_command(audio: str, concat_file: str, badges: dict,
                         turns: list, out_mp4: str, outdir: str) -> list:
    """Assemble the ffmpeg argv + write the filter graph to a script file."""
    colors = get_host_colors()
    wave_colors = "|".join(c.replace("#", "0x") for c in colors.values())

    filters = [
        # format=yuv420p MUST come before fps: fps fills the long gaps between
        # slides by bursting thousands of duplicate frames at once, and any
        # per-frame conversion downstream of it materializes each duplicate
        # (~1.4 MB/frame) faster than the encoder drains them — unbounded
        # memory growth that OOM-killed the CI runner (exit 143, 2026-07-15/16).
        # Converting first makes the duplicates zero-copy refs (~250 MB flat).
        f"[1:v]setsar=1,format=yuv420p,fps={FPS}[slides]",
        f"[0:a]showwaves=s={WIDTH}x{WAVE_HEIGHT}:mode=cline:rate={FPS}:colors={wave_colors}[wave]",
        f"[slides][wave]overlay=0:{HEIGHT - WAVE_HEIGHT}:shortest=1[base]",
    ]

    inputs = ["-i", audio, "-f", "concat", "-safe", "0", "-i", concat_file]
    last_label = "base"
    input_idx = 2
    for speaker, png in sorted(badges.items()):
        expr = build_enable_expr(turns, speaker)
        if not expr:
            continue
        inputs += ["-i", png]
        out_label = f"b{input_idx}"
        filters.append(
            f"[{last_label}][{input_idx}:v]overlay={BADGE_MARGIN_X}:{BADGE_Y}:"
            f"enable='{expr}'[{out_label}]"
        )
        last_label = out_label
        input_idx += 1

    # Rename the final label to [vout] for the -map
    filters[-1] = filters[-1].rsplit("[", 1)[0] + "[vout]"

    filter_script = os.path.join(outdir, "filters.txt")
    with open(filter_script, "w", encoding="utf-8") as f:
        f.write(";\n".join(filters))

    return [
        "ffmpeg", "-y", *inputs,
        "-filter_complex_script", filter_script,
        "-map", "[vout]", "-map", "0:a",
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
        "-crf", "26", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart", "-shortest",
        out_mp4,
    ]


def render_video(artifacts: dict) -> str:
    """Render the episode MP4. Returns the MP4 path."""
    audio = artifacts["audio"]
    duration = probe_duration(audio)

    chapters = []
    if artifacts.get("chapters"):
        with open(artifacts["chapters"], encoding="utf-8") as f:
            chapters = json.load(f).get("chapters", [])

    citations = {}
    if artifacts.get("citations"):
        with open(artifacts["citations"], encoding="utf-8") as f:
            citations = json.load(f)

    turns = []
    if artifacts.get("timeline"):
        with open(artifacts["timeline"], encoding="utf-8") as f:
            turns = json.load(f).get("turns", [])

    with tempfile.TemporaryDirectory() as tmpdir:
        cover = pick_cover_image(artifacts["date"])
        slides = render_slides(chapters, citations, duration, cover, tmpdir)
        badges = render_speaker_badges(tmpdir) if turns else {}
        concat_file = write_concat_file(slides, tmpdir)
        cmd = build_ffmpeg_command(audio, concat_file, badges, turns,
                                   artifacts["mp4"], tmpdir)
        print(f"🎬 Rendering video: {artifacts['mp4']}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed:\n{result.stderr[-3000:]}")

    size_mb = os.path.getsize(artifacts["mp4"]) / 1024 / 1024
    print(f"✅ Video rendered: {artifacts['mp4']} ({size_mb:.1f} MB, {duration / 60:.1f} min)")
    return artifacts["mp4"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Render (and optionally upload) the episode video")
    parser.add_argument("--date", default=None, help="Episode date YYYY-MM-DD (default: today Pacific)")
    parser.add_argument("--skip-upload", action="store_true", help="Render only, never upload")
    parser.add_argument("--privacy", default=None, choices=["private", "unlisted", "public"],
                        help="Override YT_PRIVACY for this run")
    parser.add_argument("--keep-video", action="store_true",
                        help="Keep the MP4 in podcasts/ even after a successful upload")
    args = parser.parse_args()

    date_str = args.date or pacific_today()

    import youtube_upload  # deferred so render-only mode works without google libs configured

    try:
        if youtube_upload.already_uploaded(date_str):
            print(f"✅ {date_str} already uploaded to YouTube — nothing to do")
            return 0

        artifacts = load_episode_artifacts(date_str)
        mp4 = render_video(artifacts)

        if args.skip_upload or not youtube_upload.have_credentials():
            print("📦 Render-only mode (no YouTube credentials or --skip-upload): "
                  f"MP4 left at {mp4} for review")
            return 0

        result = youtube_upload.upload_episode(
            mp4_path=mp4,
            citations_path=artifacts.get("citations"),
            chapters_path=artifacts.get("chapters"),
            vtt_path=artifacts.get("vtt"),
            date_str=date_str,
            privacy=args.privacy or os.getenv("YT_PRIVACY", "unlisted"),
        )
        if result and not args.keep_video:
            os.remove(mp4)
            print("🧹 Removed local MP4 after upload")
        return 0
    except Exception as e:
        # Video is additive — never fail the pipeline over it.
        print(f"::warning::Video generation failed for {date_str}: {e}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
