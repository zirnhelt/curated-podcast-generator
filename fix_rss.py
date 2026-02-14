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
from config_loader import load_podcast_config, load_credits_config

PODCASTS_DIR = Path(__file__).parent / "podcasts"

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
    credits_config = load_credits_config()
    
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
    
    # Generate RSS XML with proper escaping
    rss_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">',
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
            raw_description = podcast_config["description"] + "\n\n" + credits_config['text']
            print(f"  ⚠️  Using generic description for {episode['episode_date']}")

        # Use CDATA so line breaks render in podcast apps
        rss_lines.extend([
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
            '</item>'
        ])
    
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
        ET.parse('podcast-feed.xml')
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
