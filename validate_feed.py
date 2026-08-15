#!/usr/bin/env python3
"""Validate podcast RSS feed against Apple Podcasts requirements."""

import json
import sys
import os
import xml.etree.ElementTree as ET

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
PODCAST_NS = "https://podcastindex.org/namespace/1.0"
FEED_PATH = "podcast-feed.xml"
PODCAST_CONFIG_PATH = "config/podcast.json"
TRANSCRIPT_TYPES = {"text/vtt", "text/html", "text/srt", "application/srt", "application/json"}


def _audio_base_url():
    try:
        with open(PODCAST_CONFIG_PATH) as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return config.get("audio_base_url") or config.get("url")


def validate_feed(feed_path=FEED_PATH):
    """Check RSS feed for Apple Podcasts compliance. Returns (pass, warnings, errors)."""
    errors = []
    warnings = []

    if not os.path.exists(feed_path):
        return False, [], [f"Feed file not found: {feed_path}"]

    try:
        tree = ET.parse(feed_path)  # nosec B314 – parses locally generated RSS, not external input
    except ET.ParseError as e:
        return False, [], [f"XML parse error: {e}"]

    root = tree.getroot()
    channel = root.find("channel")
    if channel is None:
        return False, [], ["Missing <channel> element"]

    ns = {"itunes": ITUNES_NS}

    # --- Required channel tags ---
    image_el = channel.find(f"{{{ITUNES_NS}}}image")
    image_href = image_el.get("href", "") if image_el is not None else ""
    category_el = channel.find(f"{{{ITUNES_NS}}}category")

    required_channel = {
        "title": channel.findtext("title"),
        "itunes:image": image_href,
        "language": channel.findtext("language"),
        "itunes:category": category_el.get("text", "") if category_el is not None else "",
        "itunes:explicit": channel.findtext(f"{{{ITUNES_NS}}}explicit"),
    }

    for tag, value in required_channel.items():
        if not value:
            errors.append(f"Missing required channel tag: <{tag}>")

    # --- Recommended channel tags ---
    recommended_channel = {
        "itunes:author": channel.findtext(f"{{{ITUNES_NS}}}author"),
        "link": channel.findtext("link"),
        "description": channel.findtext("description"),
        "itunes:type": channel.findtext(f"{{{ITUNES_NS}}}type"),
        "itunes:owner/itunes:email": (
            channel.find(f"{{{ITUNES_NS}}}owner/{{{ITUNES_NS}}}email").text
            if channel.find(f"{{{ITUNES_NS}}}owner/{{{ITUNES_NS}}}email") is not None
            else None
        ),
    }

    for tag, value in recommended_channel.items():
        if not value:
            warnings.append(f"Missing recommended channel tag: <{tag}>")

    # Check for placeholder email
    owner_email = recommended_channel.get("itunes:owner/itunes:email", "")
    if owner_email and ("example.com" in owner_email or not owner_email.strip()):
        errors.append(
            f"Placeholder email detected: {owner_email} — update config/podcast.json"
        )

    # --- Cover art check ---
    image_href = required_channel.get("itunes:image", "")
    if image_href:
        # Check if the local file exists and its dimensions
        local_candidates = [
            os.path.basename(image_href),
            image_href,
        ]
        for candidate in local_candidates:
            if os.path.exists(candidate):
                try:
                    from PIL import Image

                    img = Image.open(candidate)
                    w, h = img.size
                    if w < 1400 or h < 1400:
                        errors.append(
                            f"Cover art too small: {w}x{h} — Apple requires 1400x1400 minimum"
                        )
                    elif w > 3000 or h > 3000:
                        warnings.append(
                            f"Cover art very large: {w}x{h} — Apple recommends up to 3000x3000"
                        )
                    if w != h:
                        errors.append(
                            f"Cover art not square: {w}x{h} — Apple requires square artwork"
                        )
                    print(f"  Cover art: {candidate} ({w}x{h})")
                except ImportError:
                    warnings.append(
                        "PIL not installed — cannot verify cover art dimensions (pip install Pillow)"
                    )
                break
        else:
            warnings.append(f"Could not find local cover art file to check dimensions")

    # --- Episode checks ---
    items = channel.findall("item")
    if not items:
        errors.append("No episodes found — Apple requires at least one episode")
    else:
        print(f"  Episodes: {len(items)}")

    audio_base = _audio_base_url()

    for i, item in enumerate(items):
        title = item.findtext("title") or f"Episode {i+1}"
        enclosure = item.find("enclosure")
        if enclosure is None:
            errors.append(f'Episode "{title}": missing <enclosure> tag')
        else:
            url = enclosure.get("url", "")
            if not url:
                errors.append(f'Episode "{title}": empty enclosure URL')
            enc_type = enclosure.get("type", "")
            if enc_type != "audio/mpeg":
                warnings.append(
                    f'Episode "{title}": enclosure type is "{enc_type}" (expected audio/mpeg)'
                )

        if not item.findtext("guid"):
            warnings.append(f'Episode "{title}": missing <guid> — may cause dedup issues')
        if not item.findtext(f"{{{ITUNES_NS}}}duration"):
            warnings.append(f'Episode "{title}": missing <itunes:duration>')

        transcripts = item.findall(f"{{{PODCAST_NS}}}transcript")
        if not transcripts:
            warnings.append(
                f'Episode "{title}": missing <podcast:transcript> — Apple falls '
                f"back to auto-generated transcripts"
            )
        for t in transcripts:
            t_url = t.get("url", "")
            if not t_url:
                errors.append(f'Episode "{title}": <podcast:transcript> has empty url')
                continue
            t_type = t.get("type", "")
            if t_type not in TRANSCRIPT_TYPES:
                warnings.append(
                    f'Episode "{title}": <podcast:transcript> type "{t_type}" not '
                    f"in {sorted(TRANSCRIPT_TYPES)}"
                )
            if not t.get("language"):
                warnings.append(
                    f'Episode "{title}": <podcast:transcript> missing language attribute'
                )
            if audio_base and t_url.startswith(audio_base):
                local_path = t_url[len(audio_base):]
                if not os.path.exists(local_path):
                    errors.append(
                        f'Episode "{title}": <podcast:transcript> references '
                        f'"{local_path}" but the file does not exist locally'
                    )

    passed = len(errors) == 0
    return passed, warnings, errors


def main():
    feed_path = sys.argv[1] if len(sys.argv) > 1 else FEED_PATH
    print(f"Validating: {feed_path}\n")

    passed, warnings, errors = validate_feed(feed_path)

    if errors:
        print(f"\n ERRORS ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")

    if warnings:
        print(f"\n WARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"  - {w}")

    if passed and not warnings:
        print("\n  Feed passes all Apple Podcasts checks!")
    elif passed:
        print(f"\n  Feed passes required checks but has {len(warnings)} warning(s)")
    else:
        print(f"\n  Feed has {len(errors)} error(s) to fix before submitting")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
