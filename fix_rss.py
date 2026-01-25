#!/usr/bin/env python3
"""
Quick RSS Feed Fixer for Cariboo Signals
Fixes XML parsing issues and generates clean RSS feed
Now uses config files for all text content
"""

import os
import glob
import xml.sax.saxutils as saxutils
from datetime import datetime
from config_loader import load_podcast_config, load_credits_config

def generate_clean_rss():
    """Generate a clean, properly escaped RSS feed."""
    print("📡 Generating clean RSS feed for Cariboo Signals...")
    
    # Load configuration
    podcast_config = load_podcast_config()
    credits_config = load_credits_config()
    
    # Find all episode files
    audio_files = glob.glob("podcast_audio_*.mp3")
    
    episodes = []
    for audio_file in sorted(audio_files, reverse=True):  # Newest first
        # Extract date and theme from filename
        parts = audio_file.replace('podcast_audio_', '').replace('.mp3', '').split('_')
        if len(parts) >= 2:
            episode_date = parts[0]  # 2026-01-24
            theme = ' '.join(parts[1:]).replace('_', ' ').title()
            
            # Get file size
            file_size = os.path.getsize(audio_file)
            
            # Convert date for RSS
            try:
                date_obj = datetime.strptime(episode_date, "%Y-%m-%d")
                pub_date = date_obj.strftime("%a, %d %b %Y 06:00:00 GMT")
            except:
                pub_date = datetime.now().strftime("%a, %d %b %Y 06:00:00 GMT")
            
            episodes.append({
                'title': f"{podcast_config['title']} - {theme}",
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
        f'<link>{podcast_config["url"]}</link>',
        f'<language>{podcast_config["language"]}</language>',
        f'<copyright>{podcast_config["copyright"]}</copyright>',
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
    
    # Add episodes with credits in description
    for episode in episodes:
        escaped_title = saxutils.escape(episode['title'])
        
        # Combine description with credits
        description_with_credits = podcast_config["description"] + "\n\n" + credits_config['text']
        escaped_description = saxutils.escape(description_with_credits)
        
        rss_lines.extend([
            '<item>',
            f'<title>{escaped_title}</title>',
            f'<link>{podcast_config["url"]}</link>',
            f'<pubDate>{episode["pub_date"]}</pubDate>',
            f'<description>{escaped_description}</description>',
            f'<itunes:summary>{escaped_description}</itunes:summary>',
            f'<itunes:subtitle>Daily tech progress - {episode["theme"]}</itunes:subtitle>',
            f'<enclosure url="{podcast_config["url"]}{episode["audio_file"]}" length="{episode["file_size"]}" type="audio/mpeg"/>',
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
    print("📝 Validating XML structure...")
    
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
