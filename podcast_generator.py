#!/usr/bin/env python3
"""
Curated Podcast Generator - Cariboo Tech Progress Edition with Memory System & Citations
Converts RSS feed scoring data into conversational podcast scripts and generates audio.
All text content loaded from config/ directory for easy updates.
"""

import os
import sys
import json
import glob
import xml.sax.saxutils as saxutils
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
import re

# Import configuration loader
from config_loader import (
    load_podcast_config,
    load_hosts_config,
    load_themes_config,
    load_credits_config,
    load_interests,
    get_voice_for_host,
    get_theme_for_day
)

# Try importing required libraries
try:
    from anthropic import Anthropic
    from openai import OpenAI
    from pydub import AudioSegment
except ImportError as e:
    print(f"⚠️  Missing required library: {e}")
    print("Please install with: pip install anthropic openai pydub")
    print("Also ensure ffmpeg is installed for audio processing")
    sys.exit(1)

# Configuration
SCRIPT_DIR = Path(__file__).parent
SUPER_RSS_BASE_URL = "https://zirnhelt.github.io/super-rss-feed"
SCORING_CACHE_URL = f"{SUPER_RSS_BASE_URL}/scored_articles_cache.json"

# Memory Configuration
EPISODE_MEMORY_FILE = SCRIPT_DIR / "episode_memory.json"
HOST_MEMORY_FILE = SCRIPT_DIR / "host_personality_memory.json"
MEMORY_RETENTION_DAYS = 21

# Load all config at startup
CONFIG = {
    'podcast': load_podcast_config(),
    'hosts': load_hosts_config(),
    'themes': load_themes_config(),
    'credits': load_credits_config(),
    'interests': load_interests()
}

def get_pacific_now():
    """Get current datetime in Pacific timezone."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Vancouver"))
    except ImportError:
        import pytz
        return datetime.now(pytz.timezone("America/Vancouver"))

def load_memory(filename):
    """Load JSON memory file, return empty dict if doesn't exist."""
    if filename.exists():
        try:
            with open(filename, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            pass
    return {}

def save_memory(filename, data):
    """Save memory data to JSON file."""
    with open(filename, 'w') as f:
        json.dump(data, f, indent=2)

def get_episode_memory():
    """Load and clean episode memory (keep last MEMORY_RETENTION_DAYS)."""
    memory = load_memory(EPISODE_MEMORY_FILE)
    
    cutoff = get_pacific_now().timestamp() - (MEMORY_RETENTION_DAYS * 24 * 3600)
    
    # Defensive: skip any malformed entries (must be dicts with timestamp)
    cleaned = {}
    for k, v in memory.items():
        if isinstance(v, dict) and 'timestamp' in v:
            if v.get('timestamp', 0) > cutoff:
                cleaned[k] = v
        else:
            print(f"  ⚠️  Skipping malformed memory entry: {k}")
    
    if len(cleaned) != len(memory):
        save_memory(EPISODE_MEMORY_FILE, cleaned)
        print(f"🧹 Cleaned episode memory: {len(memory)} → {len(cleaned)} episodes")
    
    return cleaned

def get_host_personality_memory():
    """Load host personality evolution memory."""
    return load_memory(HOST_MEMORY_FILE)

def update_episode_memory(date_key, topics, themes):
    """Update episode memory with new episode data."""
    memory = get_episode_memory()
    memory[date_key] = {
        "timestamp": get_pacific_now().timestamp(),
        "topics": topics,
        "themes": themes,
        "date": date_key
    }
    save_memory(EPISODE_MEMORY_FILE, memory)

def update_host_memory(insights_by_host):
    """Update host personality memory with new insights."""
    memory = get_host_personality_memory()
    
    for host_key, insights in insights_by_host.items():
        if host_key not in memory:
            host_config = CONFIG['hosts'][host_key]
            memory[host_key] = {
                "consistent_interests": host_config['consistent_interests'].copy(),
                "recurring_questions": host_config['recurring_questions'].copy(),
                "evolving_opinions": {}
            }
        
        for insight in insights:
            if insight not in memory[host_key]["consistent_interests"]:
                memory[host_key]["consistent_interests"].append(insight)
        
        # Keep only recent interests (last 10)
        memory[host_key]["consistent_interests"] = memory[host_key]["consistent_interests"][-10:]
    
    save_memory(HOST_MEMORY_FILE, memory)

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
    """Fetch and combine articles from all category feeds."""
    print("📥 Fetching current feed data from all categories...")
    
    categories = ['local', 'ai-tech', 'climate', 'homelab', 'news', 'science', 'scifi']
    all_articles = []
    
    for category in categories:
        feed_url = f"{SUPER_RSS_BASE_URL}/feed-{category}.json"
        try:
            response = requests.get(feed_url, timeout=10)
            response.raise_for_status()
            
            feed_data = response.json()
            articles = feed_data.get('items', [])
            print(f"  ✓ {category}: {len(articles)} articles")
            all_articles.extend(articles)
            
        except requests.exceptions.RequestException as e:
            print(f"  ⚠️  {category}: {e}")
            continue
        except json.JSONDecodeError as e:
            print(f"  ⚠️  {category}: JSON error: {e}")
            continue
    
    # Deduplicate by URL
    seen_urls = set()
    unique_articles = []
    for article in all_articles:
        url = article.get('url', '')
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_articles.append(article)
    
    print(f"✅ Loaded {len(unique_articles)} unique articles from {len(categories)} categories")
    return unique_articles

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
    
    scored_articles.sort(key=lambda x: x.get('ai_score', 0), reverse=True)
    return scored_articles

def categorize_articles_for_deep_dive(articles, theme_day):
    """Categorize articles for deep dive segment based on daily theme."""
    theme_info = CONFIG['themes'][str(theme_day)]
    theme_name = theme_info['name']
    
    # Simple keyword matching based on theme
    # Could be expanded with more sophisticated logic
    theme_articles = articles[:4]  # For now, just take top 4
    
    print(f"🎯 Selected {len(theme_articles)} articles for '{theme_name}'")
    return theme_articles

def get_current_date_info():
    """Get properly formatted current date and day in Pacific timezone."""
    pacific_now = get_pacific_now()
    weekday = pacific_now.strftime("%A")
    date_str = pacific_now.strftime("%B %d, %Y")
    
    return weekday, date_str

def generate_episode_description(news_articles, deep_dive_articles, theme_name):
    """Generate episode description with sources and credits."""
    weekday, formatted_date = get_current_date_info()
    podcast_config = CONFIG['podcast']
    
    # Get top story titles for teaser
    top_stories = [article.get('title', '').split(' - ')[0] for article in news_articles[:3]]
    top_stories = [story for story in top_stories if story]
    
    if len(top_stories) >= 2:
        stories_preview = f"{top_stories[0]} and {top_stories[1]}"
        if len(top_stories) > 2:
            stories_preview += f", plus {len(top_stories)-2} more stories"
    elif len(top_stories) == 1:
        stories_preview = top_stories[0]
    else:
        stories_preview = "the week's top tech developments"
    
    hosts = CONFIG['hosts']
    riley_bio = hosts['riley']['short_bio']
    casey_bio = hosts['casey']['short_bio']
    
    description = f"""Riley and Casey explore technology and society in rural communities. Today's focus: {theme_name}.

NEWS ROUNDUP: We break down {stories_preview}, and explore what these developments mean for communities like ours.

RURAL CONNECTIONS: Deep dive into {theme_name.lower()}, discussing how rural and remote communities can thoughtfully adopt and adapt emerging technologies.

Hosts: Riley ({riley_bio}) and Casey ({casey_bio})."""
    
    # Add sources
    citations_text = "\n\nSources:\n"
    
    for i, article in enumerate(news_articles[:12], 1):
        source_name = article.get('authors', [{}])[0].get('name', 'Unknown Source')
        article_title = article.get('title', 'Untitled')[:60] + ("..." if len(article.get('title', '')) > 60 else "")
        citations_text += f"{i}. {source_name}: {article_title}\n"
    
    for i, article in enumerate(deep_dive_articles, len(news_articles[:12]) + 1):
        source_name = article.get('authors', [{}])[0].get('name', 'Unknown Source')
        article_title = article.get('title', 'Untitled')[:60] + ("..." if len(article.get('title', '')) > 60 else "")
        citations_text += f"{i}. {source_name}: {article_title}\n"
    
    # Add credits
    description += citations_text + CONFIG['credits']['text']
    
    return description

def generate_citations_file(news_articles, deep_dive_articles, theme_name):
    """Generate citations file for the episode."""
    pacific_now = get_pacific_now()
    date_str = pacific_now.strftime("%Y-%m-%d")
    weekday, formatted_date = get_current_date_info()
    
    podcast_config = CONFIG['podcast']
    episode_description = generate_episode_description(news_articles, deep_dive_articles, theme_name)
    
    citations_data = {
        "episode": {
            "date": date_str,
            "formatted_date": f"{weekday}, {formatted_date}",
            "theme": theme_name,
            "title": f"{podcast_config['title']} - {theme_name}",
            "description": episode_description,
            "generated_at": pacific_now.isoformat()
        },
        "segments": {
            "news_roundup": {
                "title": "The Week's Tech - News Roundup",
                "articles": []
            },
            "deep_dive": {
                "title": f"Cariboo Connections - {theme_name}",
                "articles": []
            }
        },
        "credits": CONFIG['credits']['structured']
    }
    
    # Add articles
    for article in news_articles:
        citation = {
            "title": article.get('title', ''),
            "url": article.get('url', ''),
            "source": article.get('authors', [{}])[0].get('name', 'Unknown Source'),
            "ai_score": article.get('ai_score', 0),
            "date_published": article.get('date_published', ''),
            "summary": article.get('summary', '')[:200] + "..." if len(article.get('summary', '')) > 200 else article.get('summary', '')
        }
        citations_data["segments"]["news_roundup"]["articles"].append(citation)
    
    for article in deep_dive_articles:
        citation = {
            "title": article.get('title', ''),
            "url": article.get('url', ''),
            "source": article.get('authors', [{}])[0].get('name', 'Unknown Source'),
            "ai_score": article.get('ai_score', 0),
            "date_published": article.get('date_published', ''),
            "summary": article.get('summary', '')[:200] + "..." if len(article.get('summary', '')) > 200 else article.get('summary', '')
        }
        citations_data["segments"]["deep_dive"]["articles"].append(citation)
    
    # Save citations file
    safe_theme = theme_name.replace(" ", "_").replace("&", "and").lower()
    citations_filename = SCRIPT_DIR / f"citations_{date_str}_{safe_theme}.json"
    
    try:
        with open(citations_filename, 'w', encoding='utf-8') as f:
            json.dump(citations_data, f, indent=2, ensure_ascii=False)
        
        print(f"📋 Saved citations to: {citations_filename.name}")
        return citations_filename
        
    except Exception as e:
        print(f"❌ Error saving citations: {e}")
        return None

def format_memory_for_prompt(episode_memory, host_memory):
    """Format memory into context for Claude prompt."""
    context = ""
    
    recent_episodes = list(episode_memory.values())[-5:]
    if recent_episodes:
        context += "RECENT EPISODE CONTEXT (for natural callbacks):\n"
        for episode in recent_episodes:
            topics = episode.get('topics', [])
            if topics:
                context += f"- {episode['date']}: {', '.join(topics)}\n"
        context += "\n"
    
    hosts_config = CONFIG['hosts']
    if host_memory:
        context += "HOST PERSONALITY CONTEXT:\n"
        for host_key, host_data in hosts_config.items():
            if host_key in host_memory:
                context += f"{host_data['name']} tends to focus on: {', '.join(host_memory[host_key].get('consistent_interests', []))}\n"
        context += "\n"
    
    return context

def generate_podcast_script(all_articles, deep_dive_articles, theme_name, episode_memory, host_memory):
    """Generate conversational podcast script using Claude."""
    print("🎙️ Generating podcast script with Claude...")
    
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        print("❌ ANTHROPIC_API_KEY not found in environment")
        return None
    
    weekday, date_str = get_current_date_info()
    podcast_config = CONFIG['podcast']
    hosts_config = CONFIG['hosts']
    
    # Prepare articles
    top_news = all_articles[:12]
    
    news_text = "\n".join([
        f"- [{a.get('authors', [{}])[0].get('name', 'Unknown')}] {a.get('title', '')}\n  {a.get('summary', '')[:150]}... (AI Score: {a.get('ai_score', 0)})"
        for a in top_news
    ])
    
    deep_dive_text = "\n".join([
        f"- [{a.get('authors', [{}])[0].get('name', 'Unknown')}] {a.get('title', '')}\n  {a.get('summary', '')[:200]}... (AI Score: {a.get('ai_score', 0)})"
        for a in deep_dive_articles
    ])
    
    memory_context = format_memory_for_prompt(episode_memory, host_memory)
    interests = CONFIG['interests']
    
    riley = hosts_config['riley']
    casey = hosts_config['casey']
    
    prompt = f"""Create a 30-minute DAILY podcast script for "{weekday}, {date_str}" focusing on "Technological and Societal Progress in the Cariboo."

PODCAST THEME: "{podcast_config['title']}"
{podcast_config['description']}

THIS IS A DAILY PODCAST - we publish every day with weekly themes. Say "today's episode" not "weekly show."

{memory_context}

**INDIGENOUS CONTEXT:**
The Cariboo region encompasses the traditional territories of the Secwépemc (Shuswap), Tŝilhqot'in (Chilcotin), and Dakelh (Carrier) nations. When discussing regional development, infrastructure, or community initiatives:
- Acknowledge Indigenous perspectives and leadership where relevant
- Mention Indigenous-led tech initiatives, data sovereignty, or community projects when they appear in the news
- Don't force it into every episode, but be ready to discuss it naturally when the topic arises
- Avoid stereotypes or "exotic" framing - treat Indigenous innovation as part of the regional tech landscape

**CRITICAL ANTI-REPETITION REQUIREMENTS:**

1. VARIED VOCABULARY: Never use the same descriptive phrase twice. Vary sentence structures significantly.

2. NO CIRCULAR REASONING: Don't repeat the same argument in different words. Each point should add NEW information.

3. NATURAL TRANSITIONS: Avoid formulaic transitions like "Speaking of..." Use conversational bridges: "That reminds me of...", "Here's where it gets interesting..."

4. DEPTH OVER BREADTH: Better to explore 2-3 stories deeply than skim 5 stories superficially. Each story should have: what happened, why it matters, what's the rural angle. Then move on.

5. CONVERSATIONAL FLOW: Build on each other's points, don't repeat them. Use "Yeah, and..." not "Yes, exactly, let me restate that..."

6. SEGMENT VARIETY: News Roundup should be efficient and fact-focused (like NPR). Deep Dive should be relaxed and exploratory. These should SOUND different.

**EXAMPLE - WHAT TO AVOID:**
BAD: "This could help rural communities." / "Absolutely, rural areas could benefit." / "Yeah, for communities like ours, this would be useful." [Same point three times]

GOOD: "This could help with last-mile connectivity." / "True, though maintenance costs in smaller populations..." / "Maybe a co-op model like Olds Fiber?" [Each adds something new]


HOSTS:
- {riley['name']} ({riley['pronouns']}): {riley['full_bio']}
- {casey['name']} ({casey['pronouns']}): {casey['full_bio']}

IMPORTANT: These are AI hosts - do not include personal human experiences like "my dad" or family references. Keep it professional and focused on rural tech perspectives.

EPISODE STRUCTURE:

**SEGMENT 1 (20 minutes): "The Week's Tech" - Professional News Roundup**
Professional news anchor delivery covering these TOP-SCORED articles:
{news_text}

Style: Professional news anchor format - structured, authoritative, informative.

## [AD BREAK PLACEHOLDER - Future Sponsorship Spot]
[NATURAL TRANSITION: "We'll be right back after this short break to dive deeper into today's theme: {theme_name}"]

**SEGMENT 2 (10 minutes): "Cariboo Connections - {theme_name}"**
VERY CONVERSATIONAL analysis:
{deep_dive_text}

Style: Relaxed, natural conversation about today's theme and rural tech implications.

CRITICAL REQUIREMENTS:
- NO STAGE DIRECTIONS: Never write "(shuffles papers)", "(laughs)", "*chuckles*" or ANY performance cues
- DAILY FREQUENCY: Say "today's episode" - NEVER "weekly show"
- NO HUMAN PRETENSE: These are AI hosts - no personal family references
- AVOID REPETITION: Don't repeat the same points
- Regional lens: "What does this mean for communities like ours?"
- USE MEMORY: Reference past episodes naturally when relevant
- FEEDBACK INVITATION: End with "We'd love to hear your thoughts"
- Current date is {weekday}, {date_str}

OUTPUT: ~4,500-5,000 words with **RILEY:** and **CASEY:** speaker tags only."""

    try:
        client = Anthropic(api_key=api_key)
        
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
        
        # Check for speaker tags FIRST
        riley_match = re.match(r'\*\*RILEY:\*\*\s*(.*)', line)
        casey_match = re.match(r'\*\*CASEY:\*\*\s*(.*)', line)
        
        if riley_match:
            if current_speaker and current_text:
                segments.append({
                    'speaker': current_speaker,
                    'text': ' '.join(current_text).strip()
                })
            current_speaker = 'riley'
            current_text = [riley_match.group(1)] if riley_match.group(1) else []
            
        elif casey_match:
            if current_speaker and current_text:
                segments.append({
                    'speaker': current_speaker,
                    'text': ' '.join(current_text).strip()
                })
            current_speaker = 'casey'
            current_text = [casey_match.group(1)] if casey_match.group(1) else []
            
        elif line and current_speaker:
            # Skip metadata and stage directions
            if (not line.startswith('#') and 
                not line.startswith('---') and 
                not line.startswith('*End of') and
                not line.startswith('[') and
                not line.endswith(']') and
                not ('(' in line and ')' in line)):
                current_text.append(line)
    
    # Add final segment
    if current_speaker and current_text:
        segments.append({
            'speaker': current_speaker,
            'text': ' '.join(current_text).strip()
        })
    
    # Filter short segments
    cleaned_segments = []
    for segment in segments:
        clean_text = re.sub(r'\([^)]*\)', '', segment['text'])
        clean_text = re.sub(r'\*[^*]*\*', '', clean_text)
        clean_text = ' '.join(clean_text.split())
        
        if len(clean_text) > 10:
            cleaned_segments.append({
                'speaker': segment['speaker'],
                'text': clean_text
            })
    
    print(f"🎭 Parsed script into {len(cleaned_segments)} speaking segments")
    return cleaned_segments

def generate_audio_from_script(script, output_filename):
    """Convert script to audio using OpenAI TTS."""
    print("🔊 Generating audio with OpenAI TTS...")
    
    openai_api_key = os.getenv('OPENAI_API_KEY')
    if not openai_api_key:
        print("❌ OPENAI_API_KEY not found in environment")
        return None
    
    try:
        client = OpenAI(api_key=openai_api_key)
        
        segments = parse_script_by_speaker(script)
        if not segments:
            print("❌ No speaking segments found in script")
            return None
        
        audio_files = []
        for i, segment in enumerate(segments):
            speaker = segment['speaker']
            text = segment['text']
            voice = get_voice_for_host(speaker)
            
            print(f"  🎤 Generating audio {i+1}/{len(segments)} ({speaker}: {len(text)} chars)")
            
            response = client.audio.speech.create(
                model="tts-1",
                voice=voice,
                input=text,
                speed=1.0
            )
            
            segment_filename = f"temp_segment_{i:03d}_{speaker}.mp3"
            with open(segment_filename, "wb") as f:
                f.write(response.content)
            
            audio_files.append(segment_filename)
        
        print("🎵 Combining audio segments...")
        
        combined = AudioSegment.empty()
        for audio_file in audio_files:
            segment_audio = AudioSegment.from_mp3(audio_file)
            combined += segment_audio
            combined += AudioSegment.silent(duration=500)
        
        combined.export(output_filename, format="mp3")
        
        # Clean up
        for audio_file in audio_files:
            os.remove(audio_file)
        
        duration_minutes = len(combined) / 1000 / 60
        file_size_mb = os.path.getsize(output_filename) / 1024 / 1024
        
        print(f"✅ Generated podcast audio: {output_filename}")
        print(f"   Duration: {duration_minutes:.1f} minutes")
        print(f"   File size: {file_size_mb:.1f} MB")
        
        return output_filename
        
    except Exception as e:
        print(f"❌ Error generating audio: {e}")
        return None

def generate_podcast_rss_feed():
    """Generate RSS feed for podcast distribution."""
    print("📡 Generating podcast RSS feed...")
    
    podcast_config = CONFIG['podcast']
    credits_config = CONFIG['credits']
    
    audio_files = glob.glob("podcast_audio_*.mp3")
    episodes = []
    
    for audio_file in sorted(audio_files, reverse=True):
        match = re.search(r'podcast_audio_(\d{4}-\d{2}-\d{2})_(.+)\.mp3', audio_file)
        if match:
            date_str, theme = match.groups()
            
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                pub_date = date_obj.strftime("%a, %d %b %Y 05:00:00 PST")
                
                episodes.append({
                    'title': f"{podcast_config['title']} - {theme.replace('_', ' ').title()}",
                    'audio_file': audio_file,
                    'pub_date': pub_date,
                    'file_size': os.path.getsize(audio_file)
                })
            except ValueError:
                continue
    
    episodes = episodes[:10]  # Keep last 10 episodes
    
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
    
    for category in podcast_config["categories"]:
        rss_lines.append(f'<itunes:category text="{saxutils.escape(category)}"/>')
    
    rss_lines.extend([
        f'<itunes:explicit>{"true" if podcast_config["explicit"] else "false"}</itunes:explicit>',
        f'<lastBuildDate>{get_pacific_now().strftime("%a, %d %b %Y %H:%M:%S GMT")}</lastBuildDate>'
    ])
    
    for episode in episodes:
        escaped_title = saxutils.escape(episode['title'])
        description_with_credits = saxutils.escape(
            podcast_config["description"] + credits_config['text']
        )
        
        rss_lines.extend([
            '<item>',
            f'<title>{escaped_title}</title>',
            f'<link>{podcast_config["url"]}</link>',
            f'<pubDate>{episode["pub_date"]}</pubDate>',
            f'<description>{description_with_credits}</description>',
            f'<enclosure url="{podcast_config["url"]}{episode["audio_file"]}" length="{episode["file_size"]}" type="audio/mpeg"/>',
            f'<guid>{podcast_config["url"]}{episode["audio_file"]}</guid>',
            f'<itunes:duration>{podcast_config["episode_duration"]}</itunes:duration>',
            f'<itunes:explicit>{"true" if podcast_config["explicit"] else "false"}</itunes:explicit>',
            '</item>'
        ])
    
    rss_lines.extend([
        '</channel>',
        '</rss>'
    ])
    
    with open('podcast-feed.xml', 'w', encoding='utf-8') as f:
        f.write('\n'.join(rss_lines))
    
    print(f"✅ Generated RSS feed with {len(episodes)} episodes")

def save_script_to_file(script, theme_name):
    """Save the generated script to a file."""
    if not script:
        return None
    
    pacific_now = get_pacific_now()
    date_str = pacific_now.strftime("%Y-%m-%d")
    safe_theme = theme_name.replace(" ", "_").replace("&", "and").lower()
    script_filename = f"podcast_script_{date_str}_{safe_theme}.txt"
    
    try:
        with open(script_filename, 'w', encoding='utf-8') as f:
            f.write(f"# {CONFIG['podcast']['title']} Podcast Script - {date_str}\n")
            f.write(f"# Theme: {theme_name}\n")
            f.write(f"# Generated: {pacific_now.strftime('%Y-%m-%d %H:%M:%S %Z')}\n\n")
            f.write(script)
        
        print(f"💾 Saved script to: {script_filename}")
        return script_filename
        
    except Exception as e:
        print(f"❌ Error saving script: {e}")
        return None

def extract_topics_and_themes(script):
    """Extract main topics from script for memory."""
    if not script:
        return [], []
    
    # Simple keyword extraction
    tech_keywords = [
        'AI', 'artificial intelligence', 'machine learning', 'automation',
        'rural broadband', 'digital divide', 'innovation', 'sustainability',
        'community development', 'technology adoption', 'infrastructure'
    ]
    
    topics = []
    script_lower = script.lower()
    
    for keyword in tech_keywords:
        if keyword.lower() in script_lower:
            topics.append(keyword)
    
    themes = []
    if 'rural' in script_lower or 'community' in script_lower:
        themes.append('rural development')
    if 'innovation' in script_lower or 'technology' in script_lower:
        themes.append('technology adoption')
    if 'sustainability' in script_lower or 'environment' in script_lower:
        themes.append('environmental impact')
    
    return topics[:5], themes[:3]

def main():
    """Main podcast generation workflow."""
    print("🎙️ Starting Cariboo Tech Progress generation...")
    print("=" * 60)
    
    # Load configuration
    podcast_config = CONFIG['podcast']
    print(f"📻 Podcast: {podcast_config['title']}")
    
    # Get today's theme
    pacific_now = get_pacific_now()
    today_weekday = pacific_now.weekday()
    today_theme = get_theme_for_day(today_weekday)
    weekday, date_str = get_current_date_info()
    
    print(f"📅 {weekday}, {date_str} - Theme: {today_theme}")
    
    # Load memories
    episode_memory = get_episode_memory()
    host_memory = get_host_personality_memory()
    
    # Check for existing files
    date_key = pacific_now.strftime("%Y-%m-%d")
    safe_theme = today_theme.replace(" ", "_").replace("&", "and").lower()
    script_filename = f"podcast_script_{date_key}_{safe_theme}.txt"
    audio_filename = f"podcast_audio_{date_key}_{safe_theme}.mp3"
    
    script_exists = os.path.exists(script_filename)
    audio_exists = os.path.exists(audio_filename)
    
    if script_exists and audio_exists:
        print(f"✅ Today's episode already exists:")
        print(f"   Script: {script_filename}")
        print(f"   Audio: {audio_filename}")
        generate_podcast_rss_feed()
        return
    
    # Generate script if needed
    if not script_exists:
        print("🆕 Generating new script...")
        
        # Fetch data
        scoring_data = fetch_scoring_data()
        current_articles = fetch_feed_data()
        
        if not scoring_data or not current_articles:
            print("❌ Failed to fetch data. Exiting.")
            sys.exit(1)
        
        # Score and categorize
        scored_articles = get_article_scores(current_articles, scoring_data)
        deep_dive_articles = categorize_articles_for_deep_dive(scored_articles, today_weekday)
        
        print(f"📊 Ready to generate podcast:")
        print(f"   News roundup: Top 12 articles")
        print(f"   Theme: {today_theme}")
        print(f"   Memory context: {len(episode_memory)} recent episodes")
        
        # Generate citations
        citations_file = generate_citations_file(scored_articles[:12], deep_dive_articles, today_theme)
        
        # Generate script
        script = generate_podcast_script(scored_articles, deep_dive_articles, today_theme, episode_memory, host_memory)
        
        if not script:
            print("❌ Failed to generate script. Exiting.")
            sys.exit(1)
        
        # Save script
        script_filename = save_script_to_file(script, today_theme)
        
        # Update memory
        if script:
            topics, themes = extract_topics_and_themes(script)
            update_episode_memory(date_key, topics, themes)
            
            # Update host memory
            host_insights = {
                'riley': [t for t in topics if 'tech' in t.lower() or 'AI' in t][:2],
                'casey': [t for t in topics if 'community' in t.lower() or 'rural' in t.lower()][:2]
            }
            update_host_memory(host_insights)
    else:
        print(f"📄 Using existing script: {script_filename}")
        with open(script_filename, 'r', encoding='utf-8') as f:
            script = f.read()
    
    # Generate audio if needed
    if not audio_exists and script:
        audio_file = generate_audio_from_script(script, audio_filename)
        
        if audio_file:
            print(f"🎉 Podcast complete!")
            print(f"   Script: {script_filename}")
            print(f"   Audio:  {audio_file}")
        else:
            print(f"📝 Script ready: {script_filename}")
            print("🔊 Audio generation failed")
    elif audio_exists:
        print(f"🎵 Audio already exists: {audio_filename}")
    
    # Generate RSS feed
    generate_podcast_rss_feed()
    
    print("✅ Generation complete!")

if __name__ == "__main__":
    main()
