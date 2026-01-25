#!/usr/bin/env python3
"""
Curated Podcast Generator - Cariboo Focus Edition with Memory System & Citations
Theme: "Technological and Societal Progress in the Cariboo" (pronounced CARE-ih-boo, like caribou)
Converts RSS feed scoring data into conversational podcast scripts and generates audio.
Includes episode memory (2-3 weeks) and host personality tracking for continuity.
Generates citations file for each episode.
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

# Memory Configuration
EPISODE_MEMORY_FILE = 'episode_memory.json'
HOST_MEMORY_FILE = 'host_personality_memory.json'
MEMORY_RETENTION_DAYS = 21  # 3 weeks of episode memory

# Daily themes for Deep Dive - focused on Cariboo tech/society connections
DAILY_THEMES = {
    0: "Community-Controlled Infrastructure",    # Monday - local control of tech
    1: "Sustainable Innovation",                 # Tuesday - climate tech that works here
    2: "Local Voices & Digital Equity",         # Wednesday - local news, digital access
    3: "Rural Smart Solutions",                  # Thursday - tech adapted for rural needs
    4: "Future-Ready Communities",               # Friday - preparing for what's coming
    5: "Cariboo Innovation Stories",             # Saturday - local successes
    6: "Regional Resilience"                     # Sunday - building strong communities
}

def load_episode_memory():
    """Load recent episode summaries for continuity."""
    try:
        with open(EPISODE_MEMORY_FILE, 'r', encoding='utf-8') as f:
            memory = json.load(f)
        
        # Clean old episodes (older than MEMORY_RETENTION_DAYS) using Pacific time
        cutoff_date = get_pacific_now() - timedelta(days=MEMORY_RETENTION_DAYS)
        recent_episodes = []
        
        for episode in memory.get('recent_episodes', []):
            try:
                episode_date = datetime.strptime(episode['date'], '%Y-%m-%d')
                if episode_date > cutoff_date:
                    recent_episodes.append(episode)
            except:
                continue
        
        memory['recent_episodes'] = recent_episodes
        print(f"🧠 Loaded {len(recent_episodes)} episodes from memory")
        return memory
        
    except FileNotFoundError:
        print("🧠 No episode memory found, starting fresh")
        return {'recent_episodes': []}
    except Exception as e:
        print(f"⚠️ Episode memory load error: {e}")
        return {'recent_episodes': []}

def load_host_memory():
    """Load host personality and opinion tracking."""
    try:
        with open(HOST_MEMORY_FILE, 'r', encoding='utf-8') as f:
            memory = json.load(f)
        print("🎭 Loaded host personality memory")
        return memory
        
    except FileNotFoundError:
        print("🎭 No host memory found, initializing defaults")
        return {
            "riley": {
                "consistent_interests": ["rural tech deployment", "community infrastructure", "practical solutions"],
                "recurring_questions": ["How can this work here?", "What would responsible deployment look like?"],
                "evolving_opinions": {}
            },
            "casey": {
                "consistent_interests": ["digital equity", "community development", "rural innovation"],
                "recurring_questions": ["How does this serve people like us?", "What can we learn from other rural communities?"],
                "evolving_opinions": {}
            }
        }
    except Exception as e:
        print(f"⚠️ Host memory load error: {e}")
        return {}

def extract_key_topics_from_script(script, theme):
    """Extract key discussion points from generated script using Claude."""
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        return []
    
    try:
        client = anthropic.Anthropic(api_key=api_key)
        
        prompt = f"""Extract 3-4 key topics that were discussed in this podcast script. Focus on specific technologies, events, or concepts that Riley and Casey spent significant time on.

Script excerpt:
{script[:2000]}...

Return a simple JSON array of strings, like:
["Rural broadband infrastructure challenges", "Community-controlled renewable energy", "Digital equity in remote areas"]

Just the JSON array, no other text."""

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        
        topics_text = response.content[0].text.strip()
        # Try to parse JSON
        if topics_text.startswith('[') and topics_text.endswith(']'):
            topics = json.loads(topics_text)
            return topics[:4]  # Limit to 4 topics
        else:
            return []
            
    except Exception as e:
        print(f"⚠️ Topic extraction error: {e}")
        return []

def extract_host_positions_from_script(script):
    """Extract notable positions/opinions from Riley and Casey."""
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        return []
    
    try:
        client = anthropic.Anthropic(api_key=api_key)
        
        prompt = f"""Extract 2-3 notable positions or viewpoints that Riley and Casey expressed in this script. Focus on their distinct perspectives on rural tech and community development.

Script excerpt:
{script[:2000]}...

Return a simple JSON array of strings prefixed with speaker name, like:
["Riley emphasized community ownership of infrastructure", "Casey highlighted digital equity concerns in rural areas", "Riley supported incremental tech adoption over wholesale changes"]

Just the JSON array, no other text."""

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        
        positions_text = response.content[0].text.strip()
        if positions_text.startswith('[') and positions_text.endswith(']'):
            positions = json.loads(positions_text)
            return positions[:3]  # Limit to 3 positions
        else:
            return []
            
    except Exception as e:
        print(f"⚠️ Position extraction error: {e}")
        return []

def update_episode_memory(script, theme, date_str):
    """Add current episode to memory."""
    memory = load_episode_memory()
    
    # Extract key topics and positions
    key_topics = extract_key_topics_from_script(script, theme)
    notable_discussions = extract_host_positions_from_script(script)
    
    # Add current episode
    current_episode = {
        'date': date_str,
        'theme': theme,
        'key_topics': key_topics,
        'notable_discussions': notable_discussions
    }
    
    # Add to beginning of list (most recent first)
    memory['recent_episodes'].insert(0, current_episode)
    
    # Keep only recent episodes (limit to ~20 episodes)
    memory['recent_episodes'] = memory['recent_episodes'][:20]
    
    # Save updated memory
    try:
        with open(EPISODE_MEMORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(memory, f, indent=2)
        print(f"🧠 Updated episode memory with {len(key_topics)} topics")
    except Exception as e:
        print(f"⚠️ Memory save error: {e}")

def update_host_memory(script):
    """Update host personality tracking based on script content."""
    memory = load_host_memory()
    
    # For now, just save the memory as-is
    # In future iterations, we could analyze script to update evolving opinions
    try:
        with open(HOST_MEMORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(memory, f, indent=2)
    except Exception as e:
        print(f"⚠️ Host memory save error: {e}")

def format_memory_for_prompt(episode_memory, host_memory):
    """Format memory into context for Claude prompt."""
    context = ""
    
    # Recent episodes context
    recent_episodes = episode_memory.get('recent_episodes', [])[:5]  # Last 5 episodes
    if recent_episodes:
        context += "RECENT EPISODE CONTEXT (for natural callbacks):\n"
        for episode in recent_episodes:
            context += f"- {episode['date']} ({episode['theme']}): {', '.join(episode.get('key_topics', []))}\n"
            for discussion in episode.get('notable_discussions', []):
                context += f"  * {discussion}\n"
        context += "\n"
    
    # Host personality context
    riley_info = host_memory.get('riley', {})
    casey_info = host_memory.get('casey', {})
    
    if riley_info or casey_info:
        context += "HOST PERSONALITY CONTEXT:\n"
        if riley_info:
            context += f"Riley tends to focus on: {', '.join(riley_info.get('consistent_interests', []))}\n"
            context += f"Riley often asks: {', '.join(riley_info.get('recurring_questions', []))}\n"
        if casey_info:
            context += f"Casey tends to focus on: {', '.join(casey_info.get('consistent_interests', []))}\n"
            context += f"Casey often asks: {', '.join(casey_info.get('recurring_questions', []))}\n"
        context += "\n"
    
    return context

def get_pacific_now():
    """Get current datetime in Pacific timezone."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Vancouver"))
    except ImportError:
        # Fallback for older Python versions
        import pytz
        return datetime.now(pytz.timezone("America/Vancouver"))

def get_daily_filenames(theme_name):
    """Get expected filenames for today's script and audio using Pacific timezone."""
    pacific_now = get_pacific_now()
    date_str = pacific_now.strftime("%Y-%m-%d")
    safe_theme = theme_name.replace(" ", "_").replace("&", "and").lower()
    
    script_filename = f"podcast_script_{date_str}_{safe_theme}.txt"
    audio_filename = f"podcast_audio_{date_str}_{safe_theme}.mp3"
    citations_filename = f"citations_{date_str}_{safe_theme}.json"
    
    return script_filename, audio_filename, citations_filename

def check_existing_files(theme_name):
    """Check if today's script and/or audio already exist."""
    script_filename, audio_filename, citations_filename = get_daily_filenames(theme_name)
    
    script_exists = os.path.exists(script_filename)
    audio_exists = os.path.exists(audio_filename)
    citations_exist = os.path.exists(citations_filename)
    
    if script_exists:
        print(f"📝 Found existing script: {script_filename}")
    if audio_exists:
        print(f"🎵 Found existing audio: {audio_filename}")
    if citations_exist:
        print(f"📚 Found existing citations: {citations_filename}")
    
    return script_exists, audio_exists, script_filename, audio_filename, citations_filename

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
    """Categorize articles for deep dive segment based on Cariboo focus."""
    theme = DAILY_THEMES[theme_day]
    
    # Keywords for each Cariboo-focused theme
    theme_keywords = {
        "Community-Controlled Infrastructure": ["infrastructure", "broadband", "internet", "community", "local control", "municipal", "cooperative"],
        "Sustainable Innovation": ["climate", "solar", "renewable", "battery", "sustainability", "environment", "green tech", "carbon"],
        "Local Voices & Digital Equity": ["local news", "journalism", "digital divide", "internet access", "rural connectivity", "media"],
        "Rural Smart Solutions": ["smart home", "automation", "rural", "remote", "satellite", "farming", "agriculture", "precision"],
        "Future-Ready Communities": ["AI", "automation", "future of work", "skills", "training", "adaptation", "planning"],
        "Cariboo Innovation Stories": ["startup", "innovation", "local business", "entrepreneur", "BC", "canada", "rural success"],
        "Regional Resilience": ["resilience", "disaster", "emergency", "backup", "redundancy", "self-reliance", "independence"]
    }
    
    keywords = theme_keywords.get(theme, [])
    
    # Filter articles for theme
    theme_articles = []
    for article in articles:
        title = article.get('title', '').lower()
        summary = article.get('summary', '').lower()
        content = f"{title} {summary}"
        
        if any(keyword in content for keyword in keywords):
            theme_articles.append(article)
    
    # If we don't have enough theme articles, supplement with highest-scoring general articles
    if len(theme_articles) < 4:
        remaining_needed = 4 - len(theme_articles)
        # Get articles not already in theme_articles
        used_urls = {a.get('url', '') for a in theme_articles}
        general_articles = [a for a in articles if a.get('url', '') not in used_urls]
        theme_articles.extend(general_articles[:remaining_needed])
    
    # Take top 4 articles
    deep_dive_articles = theme_articles[:4]
    print(f"🎯 Found {len(deep_dive_articles)} articles for '{theme}' (Cariboo focus)")
    
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
    """Get properly formatted current date and day in Pacific timezone."""
    try:
        from zoneinfo import ZoneInfo
        # Use Pacific timezone (handles PST/PDT automatically)
        pacific_tz = ZoneInfo("America/Vancouver") 
        now = datetime.now(pacific_tz)
    except ImportError:
        # Fallback for older Python versions
        import pytz
        pacific_tz = pytz.timezone("America/Vancouver")
        now = datetime.now(pacific_tz)
    
    weekday = now.strftime("%A")
    date_str = now.strftime("%B %d, %Y")
    
    return weekday, date_str

def generate_episode_description(news_articles, deep_dive_articles, theme_name):
    """Generate clean episode description for podcast apps with citations at bottom."""
    weekday, formatted_date = get_current_date_info()
    
    # Get top story titles for teaser
    top_stories = [article.get('title', '').split(' - ')[0] for article in news_articles[:3]]
    top_stories = [story for story in top_stories if story]  # Remove empty
    
    if len(top_stories) >= 2:
        stories_preview = f"{top_stories[0]} and {top_stories[1]}"
        if len(top_stories) > 2:
            stories_preview += f", plus {len(top_stories)-2} more stories"
    elif len(top_stories) == 1:
        stories_preview = top_stories[0]
    else:
        stories_preview = "the week's top tech developments"
    
    # Clean description without internal guidance
    description = f"""Riley and Casey explore technology and society in rural communities. Today's focus: {theme_name}.

NEWS ROUNDUP: We break down {stories_preview}, and explore what these developments mean for communities like ours.

RURAL CONNECTIONS: Deep dive into {theme_name.lower()}, discussing how rural and remote communities can thoughtfully adopt and adapt emerging technologies.

Hosts: Riley (rural tech systems) and Casey (community development)."""
    
    # Add simple citations at bottom
    citations_text = "\n\nSources:\n"
    
    # Add news sources
    for i, article in enumerate(news_articles[:12], 1):  # Updated to 12 for longer news segment
        source_name = article.get('authors', [{}])[0].get('name', 'Unknown Source')
        article_title = article.get('title', 'Untitled')[:60] + ("..." if len(article.get('title', '')) > 60 else "")
        citations_text += f"{i}. {source_name}: {article_title}\n"
    
    # Add deep dive sources  
    for i, article in enumerate(deep_dive_articles, len(news_articles[:12]) + 1):
        source_name = article.get('authors', [{}])[0].get('name', 'Unknown Source')
        article_title = article.get('title', 'Untitled')[:60] + ("..." if len(article.get('title', '')) > 60 else "")
        citations_text += f"{i}. {source_name}: {article_title}\n"
    
    return description + citations_text

def generate_citations_file(news_articles, deep_dive_articles, theme_name):
    """Generate citations file for the episode."""
    pacific_now = get_pacific_now()
    date_str = pacific_now.strftime("%Y-%m-%d")
    weekday, formatted_date = get_current_date_info()
    
    # Generate episode description
    episode_description = generate_episode_description(news_articles, deep_dive_articles, theme_name)
    
    citations_data = {
        "episode": {
            "date": date_str,
            "formatted_date": f"{weekday}, {formatted_date}",
            "theme": theme_name,
            "title": f"Cariboo Tech Progress - {theme_name}",
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
        }
    }
    
    # Add news articles
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
    
    # Add deep dive articles
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
    _, _, citations_filename = get_daily_filenames(theme_name)
    
    try:
        with open(citations_filename, 'w', encoding='utf-8') as f:
            json.dump(citations_data, f, indent=2, ensure_ascii=False)
        
        print(f"📚 Saved citations to: {citations_filename}")
        return citations_filename
        
    except Exception as e:
        print(f"❌ Error saving citations: {e}")
        return None

def generate_podcast_script(all_articles, deep_dive_articles, theme_name, episode_memory, host_memory):
    """Generate conversational podcast script with Cariboo focus including memory context."""
    print("🎙️ Generating Cariboo-focused podcast script with Claude (including memory)...")
    
    # Check for API key
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        print("❌ ANTHROPIC_API_KEY not found in .env file")
        return None
    
    # Get current date info
    weekday, date_str = get_current_date_info()
    
    # Prepare articles for script generation
    top_news = all_articles[:12]  # More stories for longer news segment (20 minutes target)
    
    # Create article summaries for Claude
    news_text = "\n".join([
        f"- [{a.get('authors', [{}])[0].get('name', 'Unknown')}] {a.get('title', '')}\n  {a.get('summary', '')[:150]}... (AI Score: {a.get('ai_score', 0)})"
        for a in top_news
    ])
    
    deep_dive_text = "\n".join([
        f"- [{a.get('authors', [{}])[0].get('name', 'Unknown')}] {a.get('title', '')}\n  {a.get('summary', '')[:200]}... (AI Score: {a.get('ai_score', 0)})"
        for a in deep_dive_articles
    ])
    
    # Format memory context
    memory_context = format_memory_for_prompt(episode_memory, host_memory)
    
    prompt = f"""Create a 30-minute DAILY podcast script for "{weekday}, {date_str}" focusing on "Technological and Societal Progress in the Caribou Region."

PODCAST THEME: "Technological and Societal Progress in the Caribou Region" 
NOTE: For TTS pronunciation, use "Caribou" (like the animal) in all spoken content, but keep "Cariboo" in any written references
How do rural and remote communities like ours grow and evolve alongside technology that typically benefits urban areas first? Focus on responsible, evolutionary approaches to progress.

THIS IS A DAILY PODCAST - we publish every day with weekly themes. Say "today's episode" not "weekly show."

{memory_context}

HOSTS:
- Riley (she/her): Tech systems thinker with rural roots, engineering background, asks "how can this work here?" and "what would responsible deployment look like?"
- Casey (they/them): Community development focus, asks "how does this serve people like us?" and "what are we learning from other rural innovators?"

IMPORTANT: These are AI hosts - do not include personal human experiences like "my dad" or family references. Keep it professional and focused on rural tech perspectives.

EPISODE STRUCTURE:

**SEGMENT 1 (20 minutes): "The Week's Tech" - Professional News Roundup**
Professional news anchor delivery covering these TOP-SCORED articles (use ALL of them for longer segment):
{news_text}

Style: Professional news anchor format - structured, authoritative, informative. Each story format:
- Lead with headline: "Our top story today..."
- Brief context: "According to [SOURCE]..."  
- Key facts in clear, concise language
- Brief analysis of rural implications
- Clean transitions: "In other technology news..." / "Also making headlines..."
- 2-3 minutes per story, cover ALL stories provided
- Professional, authoritative tone throughout

## [AD BREAK PLACEHOLDER - Future Sponsorship Spot]
[NATURAL TRANSITION: "We'll be right back after this short break to dive deeper into today's theme: {theme_name}"]

**SEGMENT 2 (10 minutes): "Caribou Connections - {theme_name}"**
VERY CONVERSATIONAL analysis - like two friends chatting over coffee about tech:
{deep_dive_text}

Style: Relaxed, natural conversation. Let personalities flow - interrupt each other, build on ideas, use "you know?" and "right?" naturally. Disagree sometimes, then find common ground. Ask each other questions like "What do you think about..." Connect to: rural innovation, community-controlled tech, lessons for smaller communities. Build to strong thematic conclusion about progress in our region.

CRITICAL REQUIREMENTS:
- NO STAGE DIRECTIONS: Never write "(shuffles papers)", "(laughs)", "*chuckles*" or ANY performance cues
- SEGMENT 1: Professional news anchor delivery - cover ALL provided articles in headline+summary format
- SEGMENT 2: Natural friends conversation - interruptions, casual language, building on each other's thoughts
- DAILY FREQUENCY: Say "today's episode" or "on today's show" - NEVER "weekly show" or "this week's episode"
- ONGOING MANDATE: Don't say "as we continue our week" - this is the podcast's permanent mission, not a limited series
- NO HUMAN PRETENSE: These are AI hosts - no personal family references, keep it professional
- CARIBOU PRONUNCIATION: Use "Caribou" in all spoken content (for TTS), keep "Cariboo" only in written references
- AVOID REPETITION: Don't repeat the same points - let variety and personality flow
- Regional lens: "What does this mean for communities like ours?" "How could this work in rural areas?"
- USE MEMORY: Reference past episodes naturally when relevant ("Remember when we talked about...")
- FEEDBACK INVITATION: End with "We'd love to hear your thoughts" but don't specify how (we'll add contact info later)
- Current date is {weekday}, {date_str} - use this correctly

OUTPUT: ~4,500-5,000 words with **RILEY:** and **CASEY:** speaker tags only."""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}]
        )
        
        script = response.content[0].text
        print("✅ Generated Cariboo-focused podcast script successfully!")
        return script
        
    except Exception as e:
        print(f"❌ Error generating script: {e}")
        return None

def parse_script_by_speaker(script):
    """Parse script into segments by speaker, filtering out stage directions."""
    if not script:
        return []
    
    segments = []
    current_speaker = None
    current_text = []
    
    for line in script.split('\n'):
        line = line.strip()
        
        # Check for speaker tags FIRST, before any filtering
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
            # Skip metadata lines, empty lines, and stage directions
            if (not line.startswith('#') and 
                not line.startswith('---') and 
                not line.startswith('*End of') and
                not line.startswith('##') and
                not line.startswith('###') and
                not line.startswith('[') and  # Skip [NATURAL AD BREAK TRANSITION]
                not line.endswith(']')):
                
                # Filter out stage directions but keep regular content
                if not (('(' in line and ')' in line) or
                        'shuffles' in line.lower() or 
                        'laughs' in line.lower() or
                        'chuckles' in line.lower()):
                    current_text.append(line)
    
    # Add final segment
    if current_speaker and current_text:
        segments.append({
            'speaker': current_speaker,
            'text': ' '.join(current_text).strip()
        })
    
    # Filter out very short segments and clean up text
    cleaned_segments = []
    for segment in segments:
        # Remove any remaining stage directions from text
        clean_text = re.sub(r'\([^)]*\)', '', segment['text'])  # Remove (parenthetical)
        clean_text = re.sub(r'\*[^*]*\*', '', clean_text)      # Remove *single asterisk actions*
        clean_text = ' '.join(clean_text.split())              # Clean up whitespace
        
        if len(clean_text) > 10:  # Only keep substantial segments
            cleaned_segments.append({
                'speaker': segment['speaker'],
                'text': clean_text
            })
    
    print(f"🎭 Parsed script into {len(cleaned_segments)} speaking segments")
    return cleaned_segments

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
    """Generate RSS feed for podcast apps with rich episode descriptions."""
    print("📡 Generating podcast RSS feed with episode descriptions...")
    
    # Find all episode files
    import glob
    audio_files = glob.glob("podcast_audio_*.mp3")
    
    episodes = []
    for audio_file in sorted(audio_files, reverse=True):  # Newest first
        # Extract date and theme from filename
        parts = audio_file.replace('podcast_audio_', '').replace('.mp3', '').split('_')
        if len(parts) >= 2:
            episode_date = parts[0]  # 2026-01-24
            theme = ' '.join(parts[1:]).replace('_', ' ').title()
            
            # Skip test files
            if 'test' in theme.lower():
                continue
            
            # Load episode description from citations file if available
            citations_file = f"citations_{episode_date}_{'_'.join(parts[1:])}.json"
            episode_description = "Daily tech conversations for rural communities."
            
            try:
                with open(citations_file, 'r') as f:
                    citations_data = json.load(f)
                    episode_description = citations_data['episode'].get('description', episode_description)
            except:
                pass  # Use default description if citations file not found
            
            # Get file size
            file_size = os.path.getsize(audio_file)
            
            # Convert date for RSS
            try:
                date_obj = datetime.strptime(episode_date, "%Y-%m-%d")
                # RSS pubDate should be in GMT
                pub_date = date_obj.strftime("%a, %d %b %Y 06:00:00 GMT")
            except:
                pacific_now = get_pacific_now()
                pub_date = pacific_now.strftime("%a, %d %b %Y 06:00:00 GMT")
            
            episodes.append({
                'title': f"Cariboo Tech Progress - {theme}",
                'audio_file': audio_file,
                'pub_date': pub_date,
                'file_size': file_size,
                'episode_date': episode_date,
                'theme': theme,
                'description': episode_description
            })
    
    # Generate RSS XML with proper escaping and rich metadata
    import xml.sax.saxutils as saxutils
    
    rss_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:content="http://purl.org/rss/1.0/modules/content/">',
        '<channel>',
        '<title>Cariboo Tech Progress</title>',
        '<link>https://zirnhelt.github.io/curated-podcast-generator/</link>',
        '<language>en-us</language>',
        '<copyright>© 2026 Erich\'s AI Curator</copyright>',
        '<itunes:subtitle>Technology and society in rural BC with Riley and Casey</itunes:subtitle>',
        '<itunes:author>Riley and Casey</itunes:author>',
        '<itunes:summary>How do rural communities grow alongside technology? Daily conversations about responsible tech progress in the Cariboo region. Riley brings tech systems thinking with rural roots, while Casey focuses on community development. New episodes every day with weekly themes.</itunes:summary>',
        '<description>How do rural communities grow alongside technology? Daily conversations about responsible tech progress in the Cariboo region.</description>',
        '<itunes:owner>',
        '<itunes:name>Erich\'s AI Curator</itunes:name>',
        '<itunes:email>podcast@example.com</itunes:email>',
        '</itunes:owner>',
        '<itunes:image href="https://zirnhelt.github.io/curated-podcast-generator/podcast-cover.png"/>',
        '<itunes:category text="Technology">',
        '<itunes:category text="News"/>',
        '</itunes:category>',
        '<itunes:category text="Society &amp; Culture"/>',
        '<itunes:explicit>false</itunes:explicit>',
        '<itunes:type>episodic</itunes:type>',
        f'<lastBuildDate>{get_pacific_now().strftime("%a, %d %b %Y %H:%M:%S GMT")}</lastBuildDate>'
    ]
    
    # Add episodes with rich descriptions and proper XML escaping
    for episode in episodes:
        escaped_title = saxutils.escape(episode['title'])
        escaped_description = saxutils.escape(episode['description'])
        
        rss_lines.extend([
            '<item>',
            f'<title>{escaped_title}</title>',
            '<link>https://zirnhelt.github.io/curated-podcast-generator/</link>',
            f'<pubDate>{episode["pub_date"]}</pubDate>',
            f'<description>{escaped_description}</description>',
            f'<itunes:summary>{escaped_description}</itunes:summary>',
            f'<itunes:subtitle>Daily tech progress - {episode["theme"]}</itunes:subtitle>',
            f'<enclosure url="https://zirnhelt.github.io/curated-podcast-generator/{episode["audio_file"]}" length="{episode["file_size"]}" type="audio/mpeg"/>',
            f'<guid isPermaLink="false">cariboo-tech-progress-{episode["episode_date"]}</guid>',
            '<itunes:duration>30:00</itunes:duration>',
            '<itunes:explicit>false</itunes:explicit>',
            '<itunes:episodeType>full</itunes:episodeType>',
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
    
    print(f"✅ Generated RSS feed with {len(episodes)} episodes and rich descriptions: podcast-feed.xml")
    return 'podcast-feed.xml'

def save_script_to_file(script, theme_name):
    """Save the generated script to a file."""
    if not script:
        return None
    
    script_filename, _, _ = get_daily_filenames(theme_name)
    pacific_now = get_pacific_now()
    
    try:
        with open(script_filename, 'w', encoding='utf-8') as f:
            f.write(f"# Cariboo Tech Progress Podcast Script - {pacific_now.strftime('%Y-%m-%d')}\n")
            f.write(f"# Theme: {theme_name}\n")
            f.write(f"# Generated: {pacific_now.strftime('%Y-%m-%d %H:%M:%S %Z')}\n\n")
            f.write(script)
        
        print(f"💾 Saved script to: {script_filename}")
        return script_filename
        
    except Exception as e:
        print(f"❌ Error saving script: {e}")
        return None

def main():
    print("🏔️ Cariboo Tech Progress Podcast Generator with Memory & Citations")
    print("=" * 60)
    
    # Get today's theme using Pacific timezone
    pacific_now = get_pacific_now()
    today_weekday = pacific_now.weekday()
    today_theme = DAILY_THEMES[today_weekday]
    weekday, date_str = get_current_date_info()
    print(f"📅 {weekday}, {date_str} - Deep dive theme: {today_theme}")
    
    # Load memory systems
    episode_memory = load_episode_memory()
    host_memory = load_host_memory()
    
    # Check for existing files
    script_exists, audio_exists, script_filename, audio_filename, citations_filename = check_existing_files(today_theme)
    
    # If both script and audio exist, check if we need to generate citations
    if script_exists and audio_exists:
        print("✅ Both script and audio already exist for today!")
        print(f"   Script: {script_filename}")
        print(f"   Audio:  {audio_filename}")
        
        # Check if citations exist, if not generate them from existing script
        citations_exist = os.path.exists(citations_filename)
        if not citations_exist:
            print("📚 Generating citations for existing episode...")
            
            # Load existing script
            script = load_existing_script(script_filename)
            if script:
                # We need the original article data to generate citations
                # Fetch data from live system
                scoring_data = fetch_scoring_data()
                current_articles = fetch_feed_data()
                
                if scoring_data and current_articles:
                    # Add AI scores to articles
                    scored_articles = get_article_scores(current_articles, scoring_data)
                    
                    # Get articles for deep dive (Cariboo-themed)
                    deep_dive_articles = categorize_articles_for_deep_dive(scored_articles, today_weekday)
                    
                    # Generate citations file
                    citations_file = generate_citations_file(scored_articles[:12], deep_dive_articles, today_theme)
                    
                    if citations_file:
                        print(f"✅ Generated citations: {citations_file}")
                    else:
                        print("⚠️ Failed to generate citations")
                else:
                    print("⚠️ Could not fetch article data for citations")
            else:
                print("⚠️ Could not load existing script for citations")
        else:
            print(f"📚 Citations already exist: {citations_filename}")
        
        # Still generate RSS feed
        generate_podcast_rss_feed()
        return
    
    # Load or generate script
    if script_exists:
        print("📖 Using existing script...")
        script = load_existing_script(script_filename)
    else:
        print("🆕 Generating new Cariboo-focused script with memory context...")
        
        # Fetch data from live system
        scoring_data = fetch_scoring_data()
        current_articles = fetch_feed_data()
        
        if not scoring_data or not current_articles:
            print("❌ Failed to fetch data. Exiting.")
            return
        
        # Add AI scores to articles
        scored_articles = get_article_scores(current_articles, scoring_data)
        
        # Get articles for deep dive (Cariboo-themed)
        deep_dive_articles = categorize_articles_for_deep_dive(scored_articles, today_weekday)
        
        print(f"📊 Ready to generate Cariboo Tech Progress podcast:")
        print(f"   News roundup: Top {min(8, len(scored_articles))} articles by score")
        print(f"   Cariboo connections: {len(deep_dive_articles)} articles for {today_theme}")
        print(f"   Memory context: {len(episode_memory.get('recent_episodes', []))} recent episodes")
        
        # Generate citations file
        citations_file = generate_citations_file(scored_articles[:12], deep_dive_articles, today_theme)
        
        # Generate podcast script with memory and Cariboo focus
        script = generate_podcast_script(scored_articles, deep_dive_articles, today_theme, episode_memory, host_memory)
        
        if not script:
            print("❌ Failed to generate script. Exiting.")
            return
        
        # Save script to file
        script_filename = save_script_to_file(script, today_theme)
        
        # Update memory with new episode
        if script:
            current_date = get_pacific_now().strftime("%Y-%m-%d")
            update_episode_memory(script, today_theme, current_date)
            update_host_memory(script)
    
    # Generate audio if needed
    if not audio_exists and script:
        audio_file = generate_audio_from_script(script, audio_filename)
        
        if audio_file:
            print(f"🎉 Cariboo Tech Progress podcast complete!")
            print(f"   Script: {script_filename}")
            print(f"   Audio:  {audio_file}")
            print(f"   Citations: {citations_filename}")
        else:
            print(f"📝 Script ready: {script_filename}")
            print("🔊 Audio generation failed - check requirements")
    elif audio_exists:
        print(f"🎵 Audio already exists: {audio_filename}")
    
    # Generate RSS feed for podcast apps
    generate_podcast_rss_feed()
    
    print("✅ Cariboo Tech Progress generation complete!")

if __name__ == "__main__":
    main()
