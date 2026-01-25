#!/usr/bin/env python3
"""
Curated Podcast Generator
Converts RSS feed scoring data into conversational podcast scripts and generates audio.
"""

import os
import sys
import json
import requests
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import anthropic
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
SUPER_RSS_BASE_URL = "https://zirnhelt.github.io/super-rss-feed"
SCORING_CACHE_URL = "https://raw.githubusercontent.com/zirnhelt/super-rss-feed/main/scored_articles_cache.json"
FEED_URL = f"{SUPER_RSS_BASE_URL}/super-feed.json"

# TTS Configuration
TTS_VOICES = {
    'riley': 'nova',    # Female voice for Riley
    'casey': 'echo'     # More neutral voice for Casey
}

# Daily themes (Wednesday = Local News) - for DEEP DIVE ONLY
DAILY_THEMES = {
    0: "AI/ML Infrastructure",        # Monday
    1: "Climate & Clean Energy",      # Tuesday  
    2: "Local News & Canadian Focus", # Wednesday
    3: "Smart Home & Homelab",        # Thursday
    4: "Sci-Fi & Future Tech",        # Friday
    5: "Wild Card",                   # Saturday
    6: "Wild Card"                    # Sunday
}

def get_daily_filenames(theme_name):
    """Get expected filenames for today's script and audio."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    safe_theme = theme_name.replace(" ", "_").replace("&", "and").lower()
    
    script_filename = f"podcast_script_{date_str}_{safe_theme}.txt"
    audio_filename = f"podcast_audio_{date_str}_{safe_theme}.mp3"
    
    return script_filename, audio_filename

def check_existing_files(theme_name):
    """Check if today's script and/or audio already exist."""
    script_filename, audio_filename = get_daily_filenames(theme_name)
    
    script_exists = os.path.exists(script_filename)
    audio_exists = os.path.exists(audio_filename)
    
    if script_exists:
        print(f"📝 Found existing script: {script_filename}")
    if audio_exists:
        print(f"🎵 Found existing audio: {audio_filename}")
    
    return script_exists, audio_exists, script_filename, audio_filename

def load_existing_script(script_filename):
    """Load script content from existing file."""
    try:
        with open(script_filename, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Extract just the script content (skip metadata header)
        lines = content.split('\n')
        script_start = 0
        for i, line in enumerate(lines):
            if line.startswith('# ') and ('Generated:' in line or 'Theme:' in line):
                continue
            elif line.strip() == '':
                continue
            else:
                script_start = i
                break
        
        script = '\n'.join(lines[script_start:])
        print(f"✅ Loaded existing script ({len(script)} characters)")
        return script
        
    except Exception as e:
        print(f"❌ Error loading script: {e}")
        return None

def fetch_scoring_data():
    """Fetch article scores from the live super-rss-feed system."""
    print("📥 Fetching scoring cache from super-rss-feed...")
    
    try:
        response = requests.get(SCORING_CACHE_URL, timeout=10)
        response.raise_for_status()
        
        scoring_data = response.json()
        print(f"✅ Loaded {len(scoring_data)} scored articles")
        return scoring_data
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching scoring cache: {e}")
        return {}
    except json.JSONDecodeError as e:
        print(f"❌ Error parsing JSON: {e}")
        return {}

def fetch_feed_data():
    """Fetch the current feed articles."""
    print("📥 Fetching current feed data...")
    
    try:
        response = requests.get(FEED_URL, timeout=10)
        response.raise_for_status()
        
        feed_data = response.json()
        articles = feed_data.get('items', [])
        print(f"✅ Loaded {len(articles)} current articles")
        return articles
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching feed: {e}")
        return []
    except json.JSONDecodeError as e:
        print(f"❌ Error parsing feed JSON: {e}")
        return []

def categorize_articles_for_deep_dive(articles, theme_day):
    """Categorize articles for deep dive segment only."""
    theme = DAILY_THEMES[theme_day]
    
    # Keywords for each theme
    theme_keywords = {
        "AI/ML Infrastructure": ["ai", "ml", "machine learning", "artificial intelligence", "llm", "mlops", "telemetry", "observability", "platform engineering"],
        "Climate & Clean Energy": ["climate", "solar", "wind", "battery", "ev", "electric vehicle", "renewable", "carbon", "emission", "sustainability"],
        "Local News & Canadian Focus": ["canada", "canadian", "williams lake", "bc", "british columbia", "cariboo", "kamloops", "vancouver"],
        "Smart Home & Homelab": ["smart home", "homekit", "homebridge", "hue", "automation", "self-hosted", "homelab", "raspberry pi", "docker"],
        "Sci-Fi & Future Tech": ["sci-fi", "science fiction", "space", "mars", "quantum", "fusion", "robot", "android", "future"],
        "Wild Card": []  # No specific keywords - use highest scores
    }
    
    keywords = theme_keywords.get(theme, [])
    
    # For Wild Card, just return top articles by score
    if theme == "Wild Card":
        deep_dive_articles = articles[:4]  # Top 4 articles
        print(f"🎯 Wild Card: Using top {len(deep_dive_articles)} articles by score")
        return deep_dive_articles
    
    # Filter articles for theme
    theme_articles = []
    for article in articles:
        title = article.get('title', '').lower()
        summary = article.get('summary', '').lower()
        content = f"{title} {summary}"
        
        if any(keyword in content for keyword in keywords):
            theme_articles.append(article)
    
    # Take top 4 theme-matching articles
    deep_dive_articles = theme_articles[:4]
    print(f"🎯 Found {len(deep_dive_articles)} articles for '{theme}' deep dive")
    
    return deep_dive_articles

def get_article_scores(articles, scoring_data):
    """Match articles with their AI scores."""
    scored_articles = []
    
    for article in articles:
        url = article.get('url', '')
        title = article.get('title', '')
        
        # Find matching score in cache
        score = 0
        for cache_key, cache_data in scoring_data.items():
            if cache_data.get('title', '') == title:
                score = cache_data.get('score', 0)
                break
        
        article_with_score = article.copy()
        article_with_score['ai_score'] = score
        scored_articles.append(article_with_score)
    
    # Sort by score (highest first)
    scored_articles.sort(key=lambda x: x.get('ai_score', 0), reverse=True)
    return scored_articles

def get_current_date_info():
    """Get properly formatted current date and day."""
    now = datetime.now()
    weekday = now.strftime("%A")
    date_str = now.strftime("%B %d, %Y")
    
    return weekday, date_str

def generate_podcast_script(all_articles, deep_dive_articles, theme_name):
    """Generate conversational podcast script with Riley & Casey."""
    print("🎙️ Generating podcast script with Claude...")
    
    # Check for API key
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        print("❌ ANTHROPIC_API_KEY not found in .env file")
        return None
    
    # Get current date info
    weekday, date_str = get_current_date_info()
    
    # Prepare articles for script generation
    top_news = all_articles[:10]  # Top 10 for news roundup (by score, any topic)
    
    # Create article summaries for Claude
    news_text = "\n".join([
        f"- [{a.get('authors', [{}])[0].get('name', 'Unknown')}] {a.get('title', '')}\n  {a.get('summary', '')[:150]}... (AI Score: {a.get('ai_score', 0)})"
        for a in top_news
    ])
    
    deep_dive_text = "\n".join([
        f"- [{a.get('authors', [{}])[0].get('name', 'Unknown')}] {a.get('title', '')}\n  {a.get('summary', '')[:200]}... (AI Score: {a.get('ai_score', 0)})"
        for a in deep_dive_articles
    ])
    
    prompt = f"""Create a 30-minute conversational podcast script for {weekday}, {date_str}.

HOSTS:
- Riley (she/her): Tech systems thinker, engineering background, optimistic about solutions, asks "how does this scale?", loves finding connections between technologies
- Casey (they/them): Impact journalist, community-focused, asks "who benefits?" and "who gets left out?", great at spotting unintended consequences

EPISODE STRUCTURE:

**SEGMENT 1 (18 minutes): "What Caught Our Eye" - News Roundup**
Natural, flowing conversation about these TOP-SCORED articles (regardless of topic):
{news_text}

Style: Newsy, snappy, engaging. Hit the highlights, share quick reactions. Riley and Casey riff off each other. Include some personality - maybe Riley gets excited about technical details, Casey always brings it back to real-world impact. Make it fun!

**SEGMENT 2 (12 minutes): "Deep Dive - {theme_name}"**
More focused discussion of these related articles:
{deep_dive_text}

Style: More analytical, but still conversational. Build connections between the articles. Riley might see technical patterns, Casey might spot social trends.

CONVERSATION STYLE:
- Natural back-and-forth, like friends talking
- Interruptions and "Oh!" moments are good
- Use transitions like "Speaking of...", "That reminds me...", "Wait, did you see..."
- Let personalities show: Riley's engineering enthusiasm vs Casey's community focus
- Include some lighter moments - they should actually like each other!
- Build on each other's points, don't just take turns

IMPORTANT:
- Use CORRECT date: {weekday}, {date_str}
- Make it flow naturally - avoid robotic turn-taking
- ~4,000-4,500 words total
- Use **RILEY:** and **CASEY:** speaker tags"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}]
        )
        
        script = response.content[0].text
        print("✅ Generated podcast script successfully!")
        return script
        
    except Exception as e:
        print(f"❌ Error generating script: {e}")
        return None

def parse_script_by_speaker(script):
    """Parse script into segments by speaker."""
    if not script:
        return []
    
    segments = []
    current_speaker = None
    current_text = []
    
    for line in script.split('\n'):
        line = line.strip()
        
        # Check for speaker tags
        riley_match = re.match(r'\*\*RILEY:\*\*\s*(.*)', line)
        casey_match = re.match(r'\*\*CASEY:\*\*\s*(.*)', line)
        
        if riley_match:
            # Save previous segment
            if current_speaker and current_text:
                segments.append({
                    'speaker': current_speaker,
                    'text': ' '.join(current_text).strip()
                })
            current_speaker = 'riley'
            current_text = [riley_match.group(1)] if riley_match.group(1) else []
            
        elif casey_match:
            # Save previous segment
            if current_speaker and current_text:
                segments.append({
                    'speaker': current_speaker,
                    'text': ' '.join(current_text).strip()
                })
            current_speaker = 'casey'
            current_text = [casey_match.group(1)] if casey_match.group(1) else []
            
        elif line and current_speaker:
            # Skip metadata lines and empty lines
            if not line.startswith('#') and not line.startswith('---') and not line.startswith('*End of'):
                current_text.append(line)
    
    # Add final segment
    if current_speaker and current_text:
        segments.append({
            'speaker': current_speaker,
            'text': ' '.join(current_text).strip()
        })
    
    # Filter out very short segments
    segments = [s for s in segments if len(s['text']) > 10]
    
    print(f"🎭 Parsed script into {len(segments)} speaking segments")
    return segments

def generate_audio_from_script(script, output_filename):
    """Convert script to audio using OpenAI TTS."""
    print("🔊 Generating audio with OpenAI TTS...")
    
    # Check for OpenAI API key
    openai_api_key = os.getenv('OPENAI_API_KEY')
    if not openai_api_key:
        print("❌ OPENAI_API_KEY not found in .env file")
        return None
    
    try:
        from openai import OpenAI
        client = OpenAI(api_key=openai_api_key)
        
        # Parse script by speaker
        segments = parse_script_by_speaker(script)
        if not segments:
            print("❌ No speaking segments found in script")
            return None
        
        # Generate audio for each segment
        audio_files = []
        for i, segment in enumerate(segments):
            speaker = segment['speaker']
            text = segment['text']
            voice = TTS_VOICES.get(speaker, 'alloy')
            
            print(f"  🎤 Generating audio {i+1}/{len(segments)} ({speaker}: {len(text)} chars)")
            
            # Generate TTS
            response = client.audio.speech.create(
                model="tts-1",
                voice=voice,
                input=text,
                speed=1.0
            )
            
            # Save segment audio
            segment_filename = f"temp_segment_{i:03d}_{speaker}.mp3"
            with open(segment_filename, "wb") as f:
                f.write(response.content)
            
            audio_files.append(segment_filename)
        
        print("🎵 Combining audio segments...")
        
        try:
            from pydub import AudioSegment
            
            combined = AudioSegment.empty()
            for audio_file in audio_files:
                segment_audio = AudioSegment.from_mp3(audio_file)
                combined += segment_audio
                
                # Add small pause between speakers (0.5 seconds)
                combined += AudioSegment.silent(duration=500)
            
            # Export final podcast
            combined.export(output_filename, format="mp3")
            
            # Clean up temporary files
            for audio_file in audio_files:
                os.remove(audio_file)
            
            print(f"✅ Generated podcast audio: {output_filename}")
            
            # Audio stats
            duration_seconds = len(combined) / 1000
            duration_minutes = duration_seconds / 60
            print(f"   Duration: {duration_minutes:.1f} minutes")
            print(f"   File size: {os.path.getsize(output_filename) / 1024 / 1024:.1f} MB")
            
            return output_filename
            
        except ImportError:
            print("❌ pydub not installed. Install with: pip install pydub")
            return None
            
    except ImportError:
        print("❌ OpenAI library not installed. Install with: pip install openai")
        return None
    except Exception as e:
        print(f"❌ Error generating audio: {e}")
        return None

def generate_podcast_rss_feed():
    """Generate RSS feed for podcast apps."""
    print("📡 Generating podcast RSS feed...")
    
    # Find all episode files
    import glob
    audio_files = glob.glob("podcast_audio_*.mp3")
    script_files = glob.glob("podcast_script_*.txt")
    
    # Get current date info
    weekday, date_str = get_current_date_info()
    
    episodes = []
    for audio_file in sorted(audio_files, reverse=True):  # Newest first
        # Extract date and theme from filename
        # Format: podcast_audio_2026-01-24_wild_card.mp3
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
                'title': f"Tech & Impact - {theme}",
                'audio_file': audio_file,
                'pub_date': pub_date,
                'file_size': file_size,
                'episode_date': episode_date,
                'theme': theme
            })
    
    # Generate RSS XML
    rss_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
<title>Tech &amp; Impact Podcast</title>
<link>https://zirnhelt.github.io/curated-podcast-generator/</link>
<language>en-us</language>
<copyright>Erich's AI Curator</copyright>
<itunes:subtitle>AI-curated daily conversations with Riley &amp; Casey</itunes:subtitle>
<itunes:author>Riley &amp; Casey</itunes:author>
<itunes:summary>Daily conversations about technology, climate, AI, and their impact on communities. Hosts Riley and Casey discuss the most relevant stories from 50+ curated sources.</itunes:summary>
<description>Daily conversations about technology, climate, AI, and their impact on communities. Hosts Riley and Casey discuss the most relevant stories from 50+ curated sources.</description>
<itunes:owner>
<itunes:name>Erich's AI Curator</itunes:name>
<itunes:email>podcast@example.com</itunes:email>
</itunes:owner>
<itunes:image href="https://zirnhelt.github.io/curated-podcast-generator/podcast-artwork.jpg"/>
<itunes:category text="Technology"/>
<itunes:category text="News"/>
<itunes:explicit>false</itunes:explicit>
<lastBuildDate>{datetime.now().strftime("%a, %d %b %Y %H:%M:%S GMT")}</lastBuildDate>
'''
    
    # Add episodes
    for episode in episodes:
        rss_content += f'''
<item>
<title>{episode['title']}</title>
<link>https://zirnhelt.github.io/curated-podcast-generator/</link>
<pubDate>{episode['pub_date']}</pubDate>
<description>Daily tech and impact discussion covering {episode['theme'].lower()} and top news stories.</description>
<enclosure url="https://zirnhelt.github.io/curated-podcast-generator/{episode['audio_file']}" length="{episode['file_size']}" type="audio/mpeg"/>
<guid>https://zirnhelt.github.io/curated-podcast-generator/{episode['audio_file']}</guid>
<itunes:duration>30:00</itunes:duration>
<itunes:explicit>false</itunes:explicit>
</item>'''
    
    rss_content += '''
</channel>
</rss>'''
    
    # Save RSS feed
    with open('podcast-feed.xml', 'w', encoding='utf-8') as f:
        f.write(rss_content)
    
    print(f"✅ Generated RSS feed with {len(episodes)} episodes: podcast-feed.xml")
    return 'podcast-feed.xml'

def save_script_to_file(script, theme_name):
    """Save the generated script to a file."""
    if not script:
        return None
    
    script_filename, _ = get_daily_filenames(theme_name)
    
    try:
        with open(script_filename, 'w', encoding='utf-8') as f:
            f.write(f"# Curated Podcast Script - {datetime.now().strftime('%Y-%m-%d')}\n")
            f.write(f"# Theme: {theme_name}\n")
            f.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(script)
        
        print(f"💾 Saved script to: {script_filename}")
        return script_filename
        
    except Exception as e:
        print(f"❌ Error saving script: {e}")
        return None

def main():
    print("🎙️ Curated Podcast Generator")
    print("=" * 40)
    
    # Get today's theme
    today_weekday = datetime.now().weekday()
    today_theme = DAILY_THEMES[today_weekday]
    weekday, date_str = get_current_date_info()
    print(f"📅 {weekday}, {date_str} - Deep dive theme: {today_theme}")
    
    # Check for existing files
    script_exists, audio_exists, script_filename, audio_filename = check_existing_files(today_theme)
    
    # If both exist, just generate RSS and exit
    if script_exists and audio_exists:
        print("✅ Both script and audio already exist for today!")
        print(f"   Script: {script_filename}")
        print(f"   Audio:  {audio_filename}")
        
        # Still generate RSS feed
        generate_podcast_rss_feed()
        return
    
    # Load or generate script
    if script_exists:
        print("📖 Using existing script...")
        script = load_existing_script(script_filename)
    else:
        print("🆕 Generating new script...")
        
        # Fetch data from live system
        scoring_data = fetch_scoring_data()
        current_articles = fetch_feed_data()
        
        if not scoring_data or not current_articles:
            print("❌ Failed to fetch data. Exiting.")
            return
        
        # Add AI scores to articles
        scored_articles = get_article_scores(current_articles, scoring_data)
        
        # Get articles for deep dive (theme-specific)
        deep_dive_articles = categorize_articles_for_deep_dive(scored_articles, today_weekday)
        
        print(f"📊 Ready to generate podcast:")
        print(f"   News roundup: Top {min(10, len(scored_articles))} articles by score")
        print(f"   Deep dive: {len(deep_dive_articles)} articles for {today_theme}")
        
        # Generate podcast script
        script = generate_podcast_script(scored_articles, deep_dive_articles, today_theme)
        
        if not script:
            print("❌ Failed to generate script. Exiting.")
            return
        
        # Save script to file
        script_filename = save_script_to_file(script, today_theme)
    
    # Generate audio if needed
    if not audio_exists and script:
        audio_file = generate_audio_from_script(script, audio_filename)
        
        if audio_file:
            print(f"🎉 Podcast complete!")
            print(f"   Script: {script_filename}")
            print(f"   Audio:  {audio_file}")
        else:
            print(f"📝 Script ready: {script_filename}")
            print("🔊 Audio generation failed - check requirements")
    elif audio_exists:
        print(f"🎵 Audio already exists: {audio_filename}")
    
    # Generate RSS feed for podcast apps
    generate_podcast_rss_feed()
    
    print("✅ Generation complete!")

if __name__ == "__main__":
    main()
