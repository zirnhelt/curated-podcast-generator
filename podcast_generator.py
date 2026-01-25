#!/usr/bin/env python3
"""
Curated Podcast Generator - Cariboo Signals Edition with Memory System
Theme: "Daily Tech from Rural BC"
Converts RSS feed scoring data into conversational podcast scripts and generates audio.
Includes episode memory (2-3 weeks) and host personality tracking for continuity.
"""

import os
import sys
import json
import glob
import xml.sax.saxutils as saxutils
from datetime import datetime, timezone
from pathlib import Path
import requests
import re

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
RSS_DATA_URL = "https://zirnhelt.github.io/super-rss-feed/scored_articles.json"
EPISODE_MEMORY_FILE = SCRIPT_DIR / "episode_memory.json"
HOST_MEMORY_FILE = SCRIPT_DIR / "host_personality_memory.json"
CITATIONS_DIR = SCRIPT_DIR

def load_rss_data():
    """Load the latest scored RSS articles from the public feed."""
    try:
        print("🔄 Fetching latest scored articles...")
        response = requests.get(RSS_DATA_URL, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        print(f"📊 Loaded {len(data)} scored articles")
        return data
    
    except Exception as e:
        print(f"❌ Failed to load RSS data: {e}")
        return []

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
    """Load and clean episode memory (keep last 2-3 weeks)."""
    memory = load_memory(EPISODE_MEMORY_FILE)
    
    # Clean old episodes (keep last 21 days)
    cutoff = datetime.now().timestamp() - (21 * 24 * 3600)
    cleaned = {k: v for k, v in memory.items() if v.get('timestamp', 0) > cutoff}
    
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
        "timestamp": datetime.now().timestamp(),
        "topics": topics,
        "themes": themes,
        "date": date_key
    }
    save_memory(EPISODE_MEMORY_FILE, memory)

def update_host_memory(alex_insights, casey_insights):
    """Update host personality memory with new insights."""
    memory = get_host_personality_memory()
    timestamp = datetime.now().isoformat()
    
    # Update Alex
    if "alex" not in memory:
        memory["alex"] = {"consistent_interests": [], "recurring_questions": [], "evolving_opinions": {}}
    
    # Add new insights to Alex
    for insight in alex_insights:
        if insight not in memory["alex"]["consistent_interests"]:
            memory["alex"]["consistent_interests"].append(insight)
    
    # Update Casey
    if "casey" not in memory:
        memory["casey"] = {"consistent_interests": [], "recurring_questions": [], "evolving_opinions": {}}
    
    # Add new insights to Casey  
    for insight in casey_insights:
        if insight not in memory["casey"]["consistent_interests"]:
            memory["casey"]["consistent_interests"].append(insight)
    
    # Keep only recent interests (last 10)
    memory["alex"]["consistent_interests"] = memory["alex"]["consistent_interests"][-10:]
    memory["casey"]["consistent_interests"] = memory["casey"]["consistent_interests"][-10:]
    
    save_memory(HOST_MEMORY_FILE, memory)

def select_and_prepare_content(articles):
    """Select top articles and prepare them for script generation."""
    if not articles:
        return []
    
    # Sort by score descending
    sorted_articles = sorted(articles, key=lambda x: x.get('score', 0), reverse=True)
    
    # Take top 8-12 articles, focusing on variety
    selected = []
    seen_sources = set()
    categories = set()
    
    for article in sorted_articles:
        # Skip if we already have 2 from this source
        source = article.get('source', 'Unknown')
        source_count = sum(1 for a in selected if a.get('source') == source)
        if source_count >= 2:
            continue
            
        # Add category diversity
        category = article.get('category', 'general')
        categories.add(category)
        
        selected.append(article)
        seen_sources.add(source)
        
        if len(selected) >= 10:  # Good episode length
            break
    
    print(f"📝 Selected {len(selected)} articles from {len(seen_sources)} sources")
    print(f"📚 Categories: {', '.join(sorted(categories))}")
    
    return selected

def generate_podcast_script(articles, episode_memory, host_memory):
    """Generate conversational podcast script using Claude."""
    
    # Prepare context
    memory_context = ""
    if episode_memory:
        recent_episodes = list(episode_memory.values())[-5:]  # Last 5 episodes
        recent_topics = []
        for ep in recent_episodes:
            recent_topics.extend(ep.get('topics', []))
        
        if recent_topics:
            memory_context = f"\n\nRECENT EPISODE TOPICS (avoid repetition):\n{', '.join(set(recent_topics))}"
    
    # Prepare host personality context
    host_context = ""
    if host_memory:
        alex_traits = host_memory.get('alex', {})
        casey_traits = host_memory.get('casey', {})
        
        alex_interests = alex_traits.get('consistent_interests', [])
        casey_interests = casey_traits.get('consistent_interests', [])
        
        if alex_interests or casey_interests:
            host_context = f"""
HOST PERSONALITY CONSISTENCY:
Alex (Tech Analyst): Interests - {', '.join(alex_interests[-5:])}
Casey (Community Voice): Interests - {', '.join(casey_interests[-5:])}
"""

    # Prepare article summaries
    article_summaries = []
    for i, article in enumerate(articles, 1):
        summary = f"{i}. **{article.get('title', 'Untitled')}** (Score: {article.get('score', 0)})"
        if article.get('summary'):
            summary += f" - {article['summary'][:200]}..."
        if article.get('source'):
            summary += f" [Source: {article['source']}]"
        article_summaries.append(summary)
    
    content_block = "\n".join(article_summaries)
    
    # Generate today's episode theme
    today = datetime.now().strftime("%B %d, %Y")
    
    # Create the prompt
    prompt = f"""You are creating a script for "Cariboo Signals" - a daily podcast about technology and society from rural British Columbia. Today is {today}.

PODCAST CONCEPT:
- Title: "Cariboo Signals" 
- Tagline: "Daily tech from rural BC"
- Theme: How do rural communities grow alongside technology?
- Hosts: Alex (tech analyst) and Casey (community voice)
- Length: ~15 minutes (2,500-3,000 words)
- Tone: Conversational, thoughtful, grounded

HOST PERSONALITIES:
- Alex: Technical depth, industry analysis, asks "how does this work?" and "what are the implications?"
- Casey: Community impact, practical applications, asks "how does this affect people like us?" and "what can we learn?"

Both hosts are curious, respectful, and bring different perspectives to create engaging dialogue.{host_context}{memory_context}

TODAY'S ARTICLES:
{content_block}

SCRIPT REQUIREMENTS:

1. **Natural Conversation**: Write as realistic dialogue between two people who know each other well
2. **Structured Flow**: 
   - Opening: Brief personal check-in, then introduce today's theme
   - Main segments: 3-4 topics with natural transitions
   - Closing: Key takeaways and tomorrow's preview
3. **Authentic Voices**: Each host has distinct speaking patterns and interests
4. **Rural Context**: Connect global tech trends to rural/small-town implications
5. **Balanced Coverage**: Mix of technical depth (Alex) and community impact (Casey)

FORMAT:
- Use speaker names (Alex: / Casey:)
- Include natural speech patterns (pauses, "you know", "actually")
- Add stage directions in [brackets] for important context
- No explicit ad breaks or sponsor mentions

Generate an engaging script that feels like a genuine conversation between two knowledgeable friends discussing how technology shapes rural communities."""

    try:
        # Initialize Anthropic client
        client = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
        
        print("🎙️  Generating podcast script with Claude...")
        
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
            system="You are an expert podcast script writer specializing in conversational, authentic dialogue about technology and society."
        )
        
        script_content = response.content[0].text
        print(f"✅ Generated script: {len(script_content)} characters")
        return script_content
        
    except Exception as e:
        print(f"❌ Script generation failed: {e}")
        return None

def extract_topics_and_themes(script_content):
    """Extract main topics and themes from the generated script for memory."""
    if not script_content:
        return [], []
    
    # Simple extraction - look for common tech terms and themes
    tech_keywords = [
        'AI', 'artificial intelligence', 'machine learning', 'automation', 
        'rural broadband', 'digital divide', 'innovation', 'sustainability',
        'community development', 'technology adoption', 'infrastructure'
    ]
    
    topics = []
    script_lower = script_content.lower()
    
    for keyword in tech_keywords:
        if keyword.lower() in script_lower:
            topics.append(keyword)
    
    # Extract themes based on content patterns
    themes = []
    if 'rural' in script_lower or 'community' in script_lower:
        themes.append('rural development')
    if 'innovation' in script_lower or 'technology' in script_lower:
        themes.append('technology adoption')
    if 'sustainability' in script_lower or 'environment' in script_lower:
        themes.append('environmental impact')
    
    return topics[:5], themes[:3]  # Limit to keep focused

def create_citations_file(articles, date_key):
    """Create a citations file for the episode."""
    citations = {
        "episode_date": date_key,
        "episode_title": f"Cariboo Signals - {date_key}",
        "sources": []
    }
    
    for article in articles:
        citation = {
            "title": article.get('title', 'Untitled'),
            "url": article.get('url', ''),
            "source": article.get('source', 'Unknown'),
            "score": article.get('score', 0),
            "summary": article.get('summary', '')[:300] + "..." if article.get('summary') else ""
        }
        citations["sources"].append(citation)
    
    filename = CITATIONS_DIR / f"citations_{date_key}.json"
    with open(filename, 'w') as f:
        json.dump(citations, f, indent=2)
    
    print(f"📋 Created citations file: {filename}")
    return filename

def text_to_speech(text, filename):
    """Convert text to speech using OpenAI's API."""
    try:
        client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
        
        print("🎵 Converting text to speech...")
        
        response = client.audio.speech.create(
            model="tts-1",
            voice="nova",  # Clear, friendly voice
            input=text,
            speed=1.0
        )
        
        # Save the audio file
        with open(filename, 'wb') as f:
            f.write(response.content)
        
        print(f"✅ Audio saved: {filename}")
        return True
        
    except Exception as e:
        print(f"❌ Text-to-speech failed: {e}")
        return False

def get_file_size(filepath):
    """Get file size in bytes."""
    try:
        return os.path.getsize(filepath)
    except:
        return 0

def generate_podcast_rss_feed():
    """Generate RSS feed for podcast distribution."""
    print("📡 Generating podcast RSS feed...")
    
    # Find all audio files
    audio_files = glob.glob("audio_*.mp3")
    episodes = []
    
    for audio_file in sorted(audio_files, reverse=True):  # Newest first
        # Extract date from filename: audio_2024-01-15_theme.mp3
        match = re.search(r'audio_(\d{4}-\d{2}-\d{2})_(.+)\.mp3', audio_file)
        if match:
            date_str, theme = match.groups()
            
            # Convert to proper date format
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                pub_date = date_obj.strftime("%a, %d %b %Y 05:00:00 PST")
                
                episodes.append({
                    "title": f"Cariboo Signals - {date_obj.strftime('%B %d, %Y')}",
                    "audio_file": audio_file,
                    "pub_date": pub_date,
                    "file_size": get_file_size(audio_file)
                })
            except ValueError:
                continue
    
    # Limit to most recent 10 episodes
    episodes = episodes[:10]
    
    # Generate RSS XML
    rss_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">',
        '<channel>',
        '<title>Cariboo Signals</title>',
        '<description>How do rural communities grow alongside technology? Daily conversations about responsible tech progress in the Cariboo region.</description>',
        '<link>https://zirnhelt.github.io/curated-podcast-generator/</link>',
        '<language>en-us</language>',
        '<copyright>2025 Erich\'s AI Curator</copyright>',
        '<itunes:author>Erich\'s AI Curator</itunes:author>',
        '<itunes:summary>How do rural communities grow alongside technology? Daily conversations about responsible tech progress in the Cariboo region.</itunes:summary>',
        '<itunes:owner>',
        '<itunes:name>Erich\'s AI Curator</itunes:name>',
        '<itunes:email>podcast@example.com</itunes:email>',
        '</itunes:owner>',
        '<itunes:image href="https://zirnhelt.github.io/curated-podcast-generator/cariboo-signals.png"/>',
        '<itunes:category text="Technology"/>',
        '<itunes:explicit>false</itunes:explicit>',
        f'<lastBuildDate>{datetime.now().strftime("%a, %d %b %Y %H:%M:%S GMT")}</lastBuildDate>'
    ]
    
    # Add episodes with proper XML escaping
    for episode in episodes:
        escaped_title = saxutils.escape(episode['title'])
        
        rss_lines.extend([
            '<item>',
            f'<title>{escaped_title}</title>',
            '<link>https://zirnhelt.github.io/curated-podcast-generator/</link>',
            f'<pubDate>{episode["pub_date"]}</pubDate>',
            '<description>Technology and societal progress in the Cariboo region.</description>',
            f'<enclosure url="https://zirnhelt.github.io/curated-podcast-generator/{episode["audio_file"]}" length="{episode["file_size"]}" type="audio/mpeg"/>',
            f'<guid>https://zirnhelt.github.io/curated-podcast-generator/{episode["audio_file"]}</guid>',
            '<itunes:duration>15:00</itunes:duration>',
            '<itunes:explicit>false</itunes:explicit>',
            '</item>'
        ])
    
    rss_lines.extend([
        '</channel>',
        '</rss>'
    ])
    
    # Save RSS feed
    rss_content = '\n'.join(rss_lines)
    with open('podcast-feed.xml', 'w', encoding='utf-8') as f:
        f.write(rss_content)
    
    print(f"✅ Generated RSS feed with {len(episodes)} episodes")
    print("📡 Feed URL: https://zirnhelt.github.io/curated-podcast-generator/podcast-feed.xml")

def main():
    """Main podcast generation workflow."""
    print("🎙️  Starting Cariboo Signals generation...")
    print("=" * 50)
    
    # Load RSS data
    articles = load_rss_data()
    if not articles:
        print("❌ No articles found. Exiting.")
        return
    
    # Select content for today's episode
    selected_articles = select_and_prepare_content(articles)
    if not selected_articles:
        print("❌ No suitable articles found. Exiting.")
        return
    
    # Load memories
    episode_memory = get_episode_memory()
    host_memory = get_host_personality_memory()
    
    # Generate episode
    today = datetime.now()
    date_key = today.strftime("%Y-%m-%d")
    theme = "daily_signals"  # Could be made dynamic
    
    # Check if today's episode already exists
    script_filename = f"podcast_script_{date_key}_{theme}.txt"
    audio_filename = f"audio_{date_key}_{theme}.mp3"
    
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
        script_content = generate_podcast_script(selected_articles, episode_memory, host_memory)
        
        if script_content:
            with open(script_filename, 'w', encoding='utf-8') as f:
                f.write(script_content)
            
            print(f"✅ Script saved: {script_filename}")
            
            # Update memories
            topics, themes = extract_topics_and_themes(script_content)
            update_episode_memory(date_key, topics, themes)
            
            # Update host memory with simple pattern extraction
            alex_insights = [t for t in topics if 'AI' in t or 'tech' in t.lower()][:2]
            casey_insights = [t for t in topics if 'community' in t.lower() or 'rural' in t.lower()][:2]
            update_host_memory(alex_insights, casey_insights)
            
        else:
            print("❌ Failed to generate script")
            return
    else:
        print(f"📄 Using existing script: {script_filename}")
        with open(script_filename, 'r', encoding='utf-8') as f:
            script_content = f.read()
    
    # Create citations file
    citations_filename = create_citations_file(selected_articles, date_key)
    
    # Generate audio if needed
    if not audio_exists and script_content:
        success = text_to_speech(script_content, audio_filename)
        if success:
            print(f"🎵 Audio generated successfully!")
            print(f"📁 Episode files created:")
            print(f"   Script: {script_filename}")
            print(f"   Audio:  {audio_filename}")
            print(f"   Citations: {citations_filename}")
        else:
            print(f"📝 Script ready: {script_filename}")
            print("🔊 Audio generation failed - check requirements")
    elif audio_exists:
        print(f"🎵 Audio already exists: {audio_filename}")
    
    # Generate RSS feed for podcast apps
    generate_podcast_rss_feed()
    
    print("✅ Cariboo Signals generation complete!")

if __name__ == "__main__":
    main()
