#!/usr/bin/env python3
"""
Quick RSS Feed Fixer for Cariboo Signals
Fixes XML parsing issues and generates clean RSS feed
Now uses config files for all text content AND episode-specific citations
"""

import os
import glob
import json
import xml.sax.saxutils as saxutils
from datetime import datetime
from pathlib import Path
from config_loader import load_podcast_config, render_credits_text

PODCASTS_DIR = Path(__file__).parent / "podcasts"


def _build_trace_channel_xml(trace_cfg, producer_name):
    """Return a list of XML lines for a channel-level trace:assessment block."""
    lines = [f'<trace:assessment version="{trace_cfg.get("version", "1.0")}">']
    lines.append(f'<trace:producer url="{trace_cfg["producer_url"]}">{saxutils.escape(producer_name)}</trace:producer>')
    lines.append(f'<trace:community>{saxutils.escape(trace_cfg["community"])}</trace:community>')
    generated = "true" if trace_cfg.get("ai_generated") else "false"
    lines.append(f'<trace:ai generated="{generated}" role="{trace_cfg.get("ai_role", "none")}">')
    for tool in trace_cfg.get("ai_tools", []):
        lines.append(f'<trace:tool>{saxutils.escape(tool)}</trace:tool>')
    lines.append('</trace:ai>')
    lines.append(f'<trace:track>{saxutils.escape(trace_cfg["track"])}</trace:track>')
    lines.append(f'<trace:disqualified>{"true" if trace_cfg.get("disqualified") else "false"}</trace:disqualified>')
    scores = trace_cfg.get("scores", {})
    if scores:
        lines.append('<trace:scores>')
        for cat, s in scores.items():
            lines.append(f'<trace:score category="{cat}" value="{s["score"]}" max="{s["max"]}"/>')
        lines.append('</trace:scores>')
    lines.append(f'<trace:total score="{trace_cfg["total_score"]}" max="{trace_cfg["total_max"]}" pct="{trace_cfg["total_pct"]}"/>')
    lines.append(f'<trace:verdict>{saxutils.escape(trace_cfg["verdict"])}</trace:verdict>')
    lines.append(f'<trace:assessmentDate>{trace_cfg["assessment_date"]}</trace:assessmentDate>')
    lines.append(f'<trace:assessedBy>{saxutils.escape(trace_cfg["assessed_by"])}</trace:assessedBy>')
    lines.append('</trace:assessment>')
    return lines


def load_episode_transcript_urls(episode_date, safe_theme, audio_base):
    """Return (html_url, vtt_url) for this episode's transcript files, or None where missing."""
    html_file = PODCASTS_DIR / f"podcast_transcript_{episode_date}_{safe_theme}.html"
    vtt_file = PODCASTS_DIR / f"podcast_transcript_{episode_date}_{safe_theme}.vtt"
    html_url = f"{audio_base}podcasts/podcast_transcript_{episode_date}_{safe_theme}.html" if html_file.exists() else None
    vtt_url = f"{audio_base}podcasts/podcast_transcript_{episode_date}_{safe_theme}.vtt" if vtt_file.exists() else None
    return html_url, vtt_url


def load_episode_description(episode_date, theme):
    """Load episode-specific description from citations file if it exists."""
    safe_theme = theme.replace(" ", "_").replace("&", "and").lower()
    citations_file = str(PODCASTS_DIR / f"citations_{episode_date}_{safe_theme}.json")

    try:
        if os.path.exists(citations_file):
            with open(citations_file, 'r', encoding='utf-8') as f:
                citations_data = json.load(f)
                # Return the full episode description with sources
                return citations_data['episode']['description']
    except (json.JSONDecodeError, KeyError, FileNotFoundError) as e:
        print(f"  ⚠️  Could not load description from {citations_file}: {e}")
    
    return None

def generate_clean_rss():
    """Generate a clean, properly escaped RSS feed with episode-specific descriptions."""
    print("📡 Generating clean RSS feed for Cariboo Signals...")
    
    # Load configuration
    podcast_config = load_podcast_config()
    
    # Find all episode files in podcasts/ subfolder
    audio_files = glob.glob(str(PODCASTS_DIR / "podcast_audio_*.mp3"))

    episodes = []
    for audio_file in sorted(audio_files, reverse=True):  # Newest first
        # Extract date and theme from filename
        audio_basename = os.path.basename(audio_file)
        parts = audio_basename.replace('podcast_audio_', '').replace('.mp3', '').split('_')
        if len(parts) >= 2:
            episode_date = parts[0]  # 2026-01-24
            theme = ' '.join(parts[1:]).replace('_', ' ').title()
            
            # Get file size
            file_size = os.path.getsize(audio_file)
            
            # Convert date for RSS
            try:
                date_obj = datetime.strptime(episode_date, "%Y-%m-%d")
                pub_date = date_obj.strftime("%a, %d %b %Y 06:00:00 GMT")
            except ValueError:
                pub_date = datetime.now().strftime("%a, %d %b %Y 06:00:00 GMT")
            
            episodes.append({
                'title': f"{podcast_config['title']} - {theme}",
                'audio_url_path': f"podcasts/{audio_basename}",
                'audio_file': audio_file,
                'pub_date': pub_date,
                'file_size': file_size,
                'episode_date': episode_date,
                'theme': theme
            })
    
    trace_cfg = podcast_config.get("trace", {})

    # Generate RSS XML with proper escaping
    rss_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"'
        ' xmlns:podcast="https://podcastindex.org/namespace/1.0"'
        ' xmlns:trace="https://tracestandard.org/ns/trace/1.0">',
        '<channel>',
        f'<title>{saxutils.escape(podcast_config["title"])}</title>',
        f'<link>{podcast_config["url"]}index.html</link>',
        f'<language>{podcast_config["language"]}</language>',
        f'<copyright>{saxutils.escape(podcast_config["copyright"])}</copyright>',
        f'<itunes:subtitle>{saxutils.escape(podcast_config["subtitle"])}</itunes:subtitle>',
        f'<itunes:author>{podcast_config["author"]}</itunes:author>',
        f'<itunes:summary>{saxutils.escape(podcast_config["summary"])}</itunes:summary>',
        f'<description>{saxutils.escape(podcast_config["description"])}</description>',
        '<itunes:owner>',
        f'<itunes:name>{podcast_config["author"]}</itunes:name>',
        f'<itunes:email>{podcast_config["email"]}</itunes:email>',
        '</itunes:owner>',
        f'<itunes:image href="{podcast_config["url"]}{podcast_config["cover_image"]}"/>',
    ]
    
    # Add categories
    for category in podcast_config["categories"]:
        rss_lines.append(f'<itunes:category text="{saxutils.escape(category)}"/>')
    
    rss_lines.extend([
        f'<itunes:explicit>{"true" if podcast_config["explicit"] else "false"}</itunes:explicit>',
        '<itunes:type>episodic</itunes:type>',
        f'<lastBuildDate>{datetime.now().strftime("%a, %d %b %Y %H:%M:%S GMT")}</lastBuildDate>'
    ])

    if trace_cfg:
        rss_lines += _build_trace_channel_xml(trace_cfg, podcast_config["author"])

    # Use R2 audio URL if configured, otherwise fall back to GitHub Pages
    audio_base = podcast_config.get("audio_base_url", podcast_config["url"])

    # Add episodes with episode-specific descriptions
    for episode in episodes:
        escaped_title = saxutils.escape(episode['title'])

        # Try to load episode-specific description from citations file
        episode_description = load_episode_description(episode['episode_date'], episode['theme'])

        if episode_description:
            raw_description = episode_description
            print(f"  ✅ Using episode-specific description for {episode['episode_date']}")
        else:
            # Fallback to generic description + credits
            # Generic fallback can't know which provider voiced a given episode
            raw_description = podcast_config["description"] + "\n\n" + render_credits_text(
                "OpenAI, Azure, or Gemini TTS (varies by episode)"
            )
            print(f"  ⚠️  Using generic description for {episode['episode_date']}")

        safe_theme = episode['theme'].replace(" ", "_").replace("&", "and").lower()
        transcript_url, vtt_transcript_url = load_episode_transcript_urls(episode['episode_date'], safe_theme, audio_base)

        # Use CDATA so line breaks render in podcast apps
        item_lines = [
            '<item>',
            f'<title>{escaped_title}</title>',
            f'<link>{podcast_config["url"]}index.html</link>',
            f'<pubDate>{episode["pub_date"]}</pubDate>',
            f'<description><![CDATA[{raw_description}]]></description>',
            f'<itunes:summary><![CDATA[{raw_description}]]></itunes:summary>',
            f'<itunes:subtitle>Daily tech progress - {episode["theme"]}</itunes:subtitle>',
            f'<enclosure url="{saxutils.escape(audio_base + episode["audio_url_path"], {chr(34): "&quot;"})}" length="{episode["file_size"]}" type="audio/mpeg"/>',
            f'<guid isPermaLink="false">{podcast_config["title"].lower().replace(" ", "-")}-{episode["episode_date"]}</guid>',
            f'<itunes:duration>{podcast_config["episode_duration"]}</itunes:duration>',
            f'<itunes:explicit>{"true" if podcast_config["explicit"] else "false"}</itunes:explicit>',
            '<itunes:episodeType>full</itunes:episodeType>',
        ]
        if vtt_transcript_url:
            escaped_vtt_url = saxutils.escape(vtt_transcript_url, {chr(34): "&quot;"})
            item_lines.append(f'<podcast:transcript url="{escaped_vtt_url}" type="text/vtt" language="en-CA"/>')
        if transcript_url:
            escaped_transcript_url = saxutils.escape(transcript_url, {chr(34): "&quot;"})
            item_lines.append(f'<podcast:transcript url="{escaped_transcript_url}" type="text/html" language="en-CA"/>')
        item_lines.append('</item>')
        rss_lines.extend(item_lines)
    
    rss_lines.extend([
        '</channel>',
        '</rss>'
    ])
    
    # Join lines and save
    rss_content = '\n'.join(rss_lines)
    
    with open('podcast-feed.xml', 'w', encoding='utf-8') as f:
        f.write(rss_content)
    
    print(f"✅ Generated clean RSS feed with {len(episodes)} episodes")
    print("📋 Validating XML structure...")
    
    # Quick validation
    try:
        import xml.etree.ElementTree as ET
        ET.parse('podcast-feed.xml')  # nosec B314 – parsing our own generated output, not external input
        print("✅ XML validation passed!")
    except ET.ParseError as e:
        print(f"❌ XML validation failed: {e}")
        return False
    
    return True

if __name__ == "__main__":
    success = generate_clean_rss()
    if success:
        print("\n🎉 RSS feed fixed! Test it at:")
        print("   https://zirnhelt.github.io/curated-podcast-generator/podcast-feed.xml")
    else:
        print("\n❌ RSS feed generation failed")
