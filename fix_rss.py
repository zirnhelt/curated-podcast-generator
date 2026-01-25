#!/usr/bin/env python3
"""
Quick RSS Feed Fixer for Cariboo Tech Progress Podcast
Fixes XML parsing issues and generates clean RSS feed
"""

import os
import glob
import xml.sax.saxutils as saxutils
from datetime import datetime

def generate_clean_rss():
    """Generate a clean, properly escaped RSS feed."""
    print("📡 Generating clean RSS feed for Cariboo Tech Progress...")
    
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
                'title': f"Cariboo Tech Progress - {theme}",
                'audio_file': audio_file,
                'pub_date': pub_date,
                'file_size': file_size,
                'episode_date': episode_date,
                'theme': theme
            })
    
    # Generate RSS XML with proper escaping using saxutils
    rss_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">',
        '<channel>',
        '<title>Cariboo Tech Progress</title>',
        '<link>https://zirnhelt.github.io/curated-podcast-generator/</link>',
        '<language>en-us</language>',
        '<copyright>Erich\'s AI Curator</copyright>',
        '<itunes:subtitle>Technology and society in rural BC with Riley and Casey</itunes:subtitle>',
        '<itunes:author>Riley and Casey</itunes:author>',
        '<itunes:summary>How do rural communities grow alongside technology? Daily conversations about responsible tech progress in the Cariboo region.</itunes:summary>',
        '<description>How do rural communities grow alongside technology? Daily conversations about responsible tech progress in the Cariboo region.</description>',
        '<itunes:owner>',
        '<itunes:name>Erich\'s AI Curator</itunes:name>',
        '<itunes:email>podcast@example.com</itunes:email>',
        '</itunes:owner>',
        '<itunes:category text="Technology"/>',
        '<itunes:explicit>false</itunes:explicit>',
        f'<lastBuildDate>{datetime.now().strftime("%a, %d %b %Y %H:%M:%S GMT")}</lastBuildDate>'
    ]
    
    # Add episodes with proper XML escaping
    for episode in episodes:
        # Use saxutils.escape for proper XML escaping
        escaped_title = saxutils.escape(episode['title'])
        
        rss_lines.extend([
            '<item>',
            f'<title>{escaped_title}</title>',
            '<link>https://zirnhelt.github.io/curated-podcast-generator/</link>',
            f'<pubDate>{episode["pub_date"]}</pubDate>',
            '<description>Technology and societal progress in the Cariboo region.</description>',
            f'<enclosure url="https://zirnhelt.github.io/curated-podcast-generator/{episode["audio_file"]}" length="{episode["file_size"]}" type="audio/mpeg"/>',
            f'<guid>https://zirnhelt.github.io/curated-podcast-generator/{episode["audio_file"]}</guid>',
            '<itunes:duration>30:00</itunes:duration>',
            '<itunes:explicit>false</itunes:explicit>',
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
    print("🔍 Validating XML structure...")
    
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
