#!/usr/bin/env python3
"""
Curated Podcast Generator - Cariboo Tech Progress Edition with Music & Memory
Converts RSS feed scoring data into conversational podcast scripts and generates audio with music.
All text content loaded from config/ directory for easy updates.
"""

import os
import sys
import json
import glob
import random
import xml.sax.saxutils as saxutils
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
import re
import tempfile

# Import configuration loader
from config_loader import (
    load_podcast_config,
    load_hosts_config,
    load_themes_config,
    load_credits_config,
    load_interests,
    load_prompts_config,
    get_voice_for_host,
    get_theme_for_day
)

# Import deduplication module
from dedup_articles import deduplicate_articles, format_evolving_story_context

# Import PSA selector
from psa_selector import select_psa

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

# Retry helper for API calls
def api_retry(func, max_retries=3, base_delay=2):
    """Call func() with exponential backoff on transient errors."""
    import time
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            err_str = str(e)
            is_transient = any(s in err_str for s in ['429', '503', '502', 'timeout', 'Connection'])
            if attempt < max_retries and is_transient:
                delay = base_delay * (2 ** attempt)
                print(f"  ⚠️  Retrying in {delay}s (attempt {attempt+1}/{max_retries}): {e}")
                time.sleep(delay)
            else:
                raise

# Configuration
SCRIPT_DIR = Path(__file__).parent
PODCASTS_DIR = SCRIPT_DIR / "podcasts"
PODCASTS_DIR.mkdir(exist_ok=True)
SUPER_RSS_BASE_URL = "https://zirnhelt.github.io/super-rss-feed"
SCORING_CACHE_URL = f"{SUPER_RSS_BASE_URL}/scored_articles_cache.json"
PODCAST_FEED_URL = f"{SUPER_RSS_BASE_URL}/feed-podcast.json"

# Claude model selection (override via environment variables)
SCRIPT_MODEL = os.getenv("CLAUDE_SCRIPT_MODEL", "claude-sonnet-4-20250514")
POLISH_MODEL = os.getenv("CLAUDE_POLISH_MODEL", "claude-opus-4-6")

# Music files
INTRO_MUSIC = SCRIPT_DIR / "cariboo-signals-intro.mp3"
INTERVAL_MUSIC = SCRIPT_DIR / "cariboo-signals-interval.mp3"
OUTRO_MUSIC = SCRIPT_DIR / "cariboo-signals-outro.mp3"

# Audio normalization targets (dBFS)
TARGET_SPEECH_DBFS = -20.0  # Speech louder and clear
TARGET_MUSIC_DBFS = -28.0   # Music ducked beneath speech

# Interval music duration (ms) — trim long theme to a short chime
INTERVAL_MUSIC_DURATION_MS = 2000
INTERVAL_FADE_OUT_MS = 300

# Memory Configuration (stored in podcasts/ alongside episodes)
EPISODE_MEMORY_FILE = PODCASTS_DIR / "episode_memory.json"
HOST_MEMORY_FILE = PODCASTS_DIR / "host_personality_memory.json"
MEMORY_RETENTION_DAYS = 21

# Load all config at startup
CONFIG = {
    'podcast': load_podcast_config(),
    'hosts': load_hosts_config(),
    'themes': load_themes_config(),
    'credits': load_credits_config(),
    'interests': load_interests(),
    'prompts': load_prompts_config()
}

def select_welcome_host():
    """Randomly select which host opens the show."""
    return random.choice(['riley', 'casey'])

def normalize_segment(audio_segment, target_dbfs):
    """Normalize audio segment to target dBFS level."""
    change_in_dbfs = target_dbfs - audio_segment.dBFS
    return audio_segment.apply_gain(change_in_dbfs)

def get_anthropic_client():
    """Get or create a cached Anthropic client."""
    if not hasattr(get_anthropic_client, '_client'):
        api_key = os.getenv('ANTHROPIC_API_KEY')
        if not api_key:
            return None
        get_anthropic_client._client = Anthropic(api_key=api_key)
    return get_anthropic_client._client

def get_openai_client():
    """Get or create a cached OpenAI client."""
    if not hasattr(get_openai_client, '_client'):
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            return None
        get_openai_client._client = OpenAI(api_key=api_key)
    return get_openai_client._client

def polish_script_with_claude(script, theme_name, api_key):
    """Use Claude to polish the script for better flow and less repetition."""
    print("✨ Polishing script with Claude...")

    if not script or not api_key:
        return script

    try:
        client = get_anthropic_client()
        if not client:
            return script

        # Load prompt template from config
        prompt_template = CONFIG['prompts']['script_polish']['template']

        # Format the template with actual values
        polish_prompt = prompt_template.format(
            theme_name=theme_name,
            script=script
        )

        print(f"   Using model: {POLISH_MODEL}")
        response = api_retry(lambda: client.messages.create(
            model=POLISH_MODEL,
            max_tokens=8000,
            messages=[{"role": "user", "content": polish_prompt}]
        ))

        polished_script = response.content[0].text

        # Quick validation
        if "**RILEY:**" in polished_script and "**CASEY:**" in polished_script:
            print("✅ Script polished successfully!")
            return polished_script
        else:
            print("⚠️ Polishing may have broken script format, using original")
            return script

    except Exception as e:
        print(f"⚠️ Error polishing script: {e}")
        return script

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
        print(f"🧹 Cleaned episode memory: {len(memory)} \u2192 {len(cleaned)} episodes")
    
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
            print(f"  ⚠ï¸  {category}: {e}")
            continue
        except json.JSONDecodeError as e:
            print(f"  ⚠ï¸  {category}: JSON error: {e}")
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

def fetch_podcast_feed():
    """Fetch the curated podcast feed with pre-scored, theme-sorted articles.

    Returns (feed_meta, theme_articles, bonus_articles) where feed_meta contains
    _podcast.theme and _podcast.theme_description from the feed.
    """
    print("📥 Fetching curated podcast feed...")

    try:
        response = requests.get(PODCAST_FEED_URL, timeout=10)
        response.raise_for_status()

        feed_data = response.json()

        # Extract podcast metadata from the feed
        feed_meta = {
            'theme': feed_data.get('_podcast', {}).get('theme', ''),
            'theme_description': feed_data.get('_podcast', {}).get('theme_description', ''),
        }

        items = feed_data.get('items', [])

        # Split into theme articles and bonus (off-theme) articles
        theme_articles = []
        bonus_articles = []
        for item in items:
            # Carry over feed-provided metadata
            item['_keyword_matches'] = item.get('_keyword_matches', 0)
            item['_boosted_score'] = item.get('_boosted_score', item.get('ai_score', 0))

            if item.get('_is_bonus', False):
                bonus_articles.append(item)
            else:
                theme_articles.append(item)

        print(f"  📌 Feed theme: {feed_meta['theme']}")
        print(f"  ✓ Theme articles: {len(theme_articles)}")
        print(f"  ✓ Bonus articles: {len(bonus_articles)}")
        print(f"✅ Loaded {len(items)} articles from podcast feed")
        return feed_meta, theme_articles, bonus_articles

    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching podcast feed: {e}")
        return None, [], []
    except json.JSONDecodeError as e:
        print(f"❌ Error parsing podcast feed JSON: {e}")
        return None, [], []


def get_article_scores(articles, scoring_data):
    """Match articles with their AI scores."""
    # Pre-build title->score lookup for O(1) matching
    title_to_score = {
        cache_data.get('title', ''): cache_data.get('score', 0)
        for cache_data in scoring_data.values()
    }

    scored_articles = []
    for article in articles:
        title = article.get('title', '')
        article_with_score = article.copy()
        article_with_score['ai_score'] = title_to_score.get(title, 0)
        scored_articles.append(article_with_score)

    scored_articles.sort(key=lambda x: x.get('ai_score', 0), reverse=True)
    return scored_articles

def categorize_articles_for_deep_dive(articles, theme_day):
    """Select deep dive articles from beyond the news pool, matched to theme.

    News pool = top 12 scored articles (used in Segment 1).
    Deep dive pulls from the remainder, scored by theme keyword overlap
    blended with AI score so we get relevance without being purely keyword-driven.
    """
    theme_info = CONFIG['themes'][str(theme_day)]
    theme_name = theme_info['name']

    # Build keyword list from theme name + any explicit keywords in config
    theme_keywords = [w.lower() for w in theme_name.split() if len(w) > 3]
    if 'keywords' in theme_info:
        theme_keywords.extend([k.lower() for k in theme_info['keywords']])

    # News pool is the top 12 — deep dive must pull from the rest
    news_urls = set(a.get('url', '') for a in articles[:12])
    remaining = [a for a in articles if a.get('url', '') not in news_urls]

    if not remaining:
        # Fallback: if fewer than 12 total articles, grab from positions 4+
        remaining = articles[4:]

    # Score remaining by theme relevance + AI score blend
    def theme_relevance(article):
        text = f"{article.get('title', '')} {article.get('summary', '')}".lower()
        keyword_hits = sum(1 for kw in theme_keywords if kw in text)
        ai_score_normalized = article.get('ai_score', 0) / 100.0  # 0-1 range
        # Keyword hits weighted heavier (each hit = 2 points), AI score as tiebreaker
        return keyword_hits * 2 + ai_score_normalized

    remaining.sort(key=theme_relevance, reverse=True)
    deep_dive_articles = remaining[:3]

    print(f"Deep dive: selected {len(deep_dive_articles)} articles for '{theme_name}'")
    print(f"  Pool: {len(remaining)} candidates beyond top 12 news")
    for a in deep_dive_articles:
        print(f"  - {a.get('title', '')[:70]}...")
    return deep_dive_articles


def _local_theme_relevance(article, theme_keywords):
    """Score an article's theme relevance using local keyword matching.

    Returns a float: keyword_hits * 2 + boosted_score / 100.0
    """
    text = f"{article.get('title', '')} {article.get('summary', '')}".lower()
    keyword_hits = sum(1 for kw in theme_keywords if kw in text)
    boosted = article.get('_boosted_score', article.get('ai_score', 0)) / 100.0
    return keyword_hits * 2 + boosted


def _build_theme_keywords(theme_name):
    """Build keyword list from theme config (name + explicit keywords)."""
    # Find the theme info by matching the name
    theme_info = None
    for key, info in CONFIG['themes'].items():
        if info['name'] == theme_name:
            theme_info = info
            break

    # Extract keywords from theme name (words > 3 chars)
    keywords = [w.lower() for w in theme_name.split() if len(w) > 3]

    # Add explicit keywords from config
    if theme_info and 'keywords' in theme_info:
        keywords.extend([k.lower() for k in theme_info['keywords']])

    # Add words from the description (strip punctuation)
    if theme_info and 'description' in theme_info:
        for w in theme_info['description'].split():
            cleaned = w.strip('.,;:—-').lower()
            if len(cleaned) > 3:
                keywords.append(cleaned)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)
    return unique


def select_deep_dive_from_feed(theme_articles, theme_name):
    """Select deep dive articles from pre-curated podcast feed theme articles.

    The feed already sorts articles by boosted score (theme relevance).
    Articles with _keyword_matches > 0 are strongly on-theme.
    Top 3 theme articles become the deep dive; the rest go to news.

    When the feed provides no keyword matches, falls back to local keyword
    scoring against the theme name and config keywords.
    """
    # Articles are already sorted by boosted score from the feed.
    # Prefer articles with keyword matches for deep dive.
    strong_match = [a for a in theme_articles if a.get('_keyword_matches', 0) > 0]
    weak_match = [a for a in theme_articles if a.get('_keyword_matches', 0) == 0]

    theme_keywords = _build_theme_keywords(theme_name)
    used_local_scoring = False

    if strong_match:
        # Feed provided keyword matches — use them
        deep_dive = strong_match[:3]
        if len(deep_dive) < 3:
            deep_dive.extend(weak_match[:3 - len(deep_dive)])
    else:
        # Feed provided no keyword matches — apply local theme scoring
        used_local_scoring = True
        print(f"  ⚠️  No feed keyword matches; applying local theme scoring")
        print(f"  📎 Local keywords: {theme_keywords[:10]}{'...' if len(theme_keywords) > 10 else ''}")

        scored = sorted(theme_articles, key=lambda a: _local_theme_relevance(a, theme_keywords), reverse=True)
        deep_dive = scored[:3]

    deep_dive_urls = {a.get('url', '') for a in deep_dive}
    news_articles = [a for a in theme_articles if a.get('url', '') not in deep_dive_urls]

    # When using local scoring, also sort news by theme relevance
    if used_local_scoring:
        news_articles.sort(key=lambda a: _local_theme_relevance(a, theme_keywords), reverse=True)

    print(f"Deep dive: selected {len(deep_dive)} articles for '{theme_name}'")
    print(f"  Strong keyword matches (from feed): {len(strong_match)}")
    print(f"  Local scoring fallback: {'yes' if used_local_scoring else 'no'}")
    print(f"  Remaining for news: {len(news_articles)}")
    for a in deep_dive:
        kw = a.get('_keyword_matches', 0)
        local_score = _local_theme_relevance(a, theme_keywords)
        print(f"  - [kw={kw}, local={local_score:.1f}] {a.get('title', '')[:70]}...")
    return deep_dive, news_articles

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
                "title": "News Roundup",
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
    citations_filename = PODCASTS_DIR / f"citations_{date_str}_{safe_theme}.json"
    
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


def generate_podcast_script(all_articles, deep_dive_articles, theme_name, episode_memory, host_memory, evolving_context="", psa_info=None, feed_meta=None, bonus_articles=None):
    """Generate conversational podcast script using Claude."""
    print("🎙️ Generating podcast script with Claude...")

    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        print("❌ ANTHROPIC_API_KEY not found in environment")
        return None

    weekday, date_str = get_current_date_info()
    podcast_config = CONFIG['podcast']
    hosts_config = CONFIG['hosts']

    # Randomly select welcome host
    welcome_host = select_welcome_host()
    welcome_host_name = CONFIG['hosts'][welcome_host]['name']
    other_host = 'casey' if welcome_host == 'riley' else 'riley'
    other_host_name = CONFIG['hosts'][other_host]['name']

    # Separate on-theme news from bonus articles for formatting
    if bonus_articles:
        bonus_urls = {a.get('url', '') for a in bonus_articles}
        on_theme_news = [a for a in all_articles if a.get('url', '') not in bonus_urls]
    else:
        on_theme_news = all_articles
        bonus_articles = []

    # Format on-theme news articles
    news_text = "\n".join([
        f"- [{a.get('authors', [{}])[0].get('name', 'Unknown')}] {a.get('title', '')}\n  {a.get('summary', '')[:150]}... (AI Score: {a.get('ai_score', 0)})"
        for a in on_theme_news
    ])

    # Format bonus (off-theme) articles separately
    if bonus_articles:
        bonus_text = "\n\nBONUS PICKS (off-theme but noteworthy — introduce these separately, e.g. \"Also worth noting today...\"):\n"
        bonus_text += "\n".join([
            f"- [{a.get('authors', [{}])[0].get('name', 'Unknown')}] {a.get('title', '')}\n  {a.get('summary', '')[:150]}... (AI Score: {a.get('ai_score', 0)})"
            for a in bonus_articles
        ])
        news_text += bonus_text

    deep_dive_text = "\n".join([
        f"- [{a.get('authors', [{}])[0].get('name', 'Unknown')}] {a.get('title', '')}\n  {a.get('summary', '')[:200]}... (AI Score: {a.get('ai_score', 0)})"
        for a in deep_dive_articles
    ])

    # Brief news titles so the Deep Dive can reference them without repeating summaries
    news_titles_brief = "\n".join([
        f"  {i+1}. {a.get('title', '')}"
        for i, a in enumerate(all_articles)
    ])

    # Day-aware sign-off
    weekday_lower = weekday.lower()
    if weekday_lower == 'friday':
        sign_off = "Enjoy your weekend."
    elif weekday_lower == 'saturday':
        sign_off = "Hope you're having a great weekend."
    elif weekday_lower == 'sunday':
        sign_off = "Hope you had a great weekend."
    else:
        sign_off = "Have a great rest of your day."

    memory_context = format_memory_for_prompt(episode_memory, host_memory)
    if evolving_context:
        memory_context += evolving_context + "\n"

    # Add feed theme description to memory context if available
    if feed_meta and feed_meta.get('theme_description'):
        memory_context += f"TODAY'S THEME FRAMING (from curated feed):\n{feed_meta['theme_description']}\n\n"

    # Build PSA context for the Community Spotlight segment
    if psa_info:
        psa_context = f"Featured organization: {psa_info['org_name']}\n"
        psa_context += f"Description: {psa_info['org_description']}\n"
        if psa_info.get('org_website'):
            psa_context += f"Website: {psa_info['org_website']}\n"
        if psa_info.get('psa_angle'):
            psa_context += f"Talking point: {psa_info['psa_angle']}\n"
        if psa_info.get('event_name'):
            psa_context += f"Tied to: {psa_info['event_name']}\n"
    else:
        psa_context = "No community spotlight for today's episode."

    riley = hosts_config['riley']
    casey = hosts_config['casey']

    # Load prompt template from config
    prompt_template = CONFIG['prompts']['script_generation']['template']

    # Format the template with actual values
    prompt = prompt_template.format(
        weekday=weekday,
        date_str=date_str,
        podcast_title=podcast_config['title'],
        podcast_description=podcast_config['description'],
        memory_context=memory_context,
        riley_name=riley['name'],
        riley_pronouns=riley['pronouns'],
        riley_bio=riley['full_bio'],
        casey_name=casey['name'],
        casey_pronouns=casey['pronouns'],
        casey_bio=casey['full_bio'],
        welcome_host_upper=welcome_host_name.upper(),
        welcome_host_name=welcome_host_name,
        other_host_upper=other_host_name.upper(),
        other_host_name=other_host_name,
        theme_name=theme_name,
        news_text=news_text,
        deep_dive_text=deep_dive_text,
        news_titles_brief=news_titles_brief,
        sign_off=sign_off,
        psa_context=psa_context
    )

    try:
        client = get_anthropic_client()
        if not client:
            print("❌ ANTHROPIC_API_KEY not found in environment")
            return None

        print(f"   Using model: {SCRIPT_MODEL}")
        response = api_retry(lambda: client.messages.create(
            model=SCRIPT_MODEL,
            max_tokens=7000,
            messages=[{"role": "user", "content": prompt}]
        ))

        script = response.content[0].text
        print("✅ Generated podcast script successfully!")
        return script

    except Exception as e:
        print(f"❌ Error generating script: {e}")
        return None

def parse_script_into_segments(script):
    """Parse script into welcome, news, and deep dive segments."""
    segments = {
        'welcome': [],
        'news': [],
        'community_spotlight': [],
        'deep_dive': []
    }

    current_section = 'welcome'
    current_speaker = None
    current_text = []

    for line in script.split('\n'):
        line = line.strip()

        # Detect segment transitions (support both old "SEGMENT 1/2:" and new "NEWS ROUNDUP:/DEEP DIVE:" markers)
        if 'SEGMENT 1:' in line or '**SEGMENT 1:' in line or 'NEWS ROUNDUP' in line:
            # Save welcome section
            if current_speaker and current_text:
                segments['welcome'].append({
                    'speaker': current_speaker,
                    'text': ' '.join(current_text).strip()
                })
                current_text = []
            current_section = 'news'
            continue

        if 'COMMUNITY SPOTLIGHT' in line or '**COMMUNITY SPOTLIGHT' in line:
            # Save news section
            if current_speaker and current_text:
                segments[current_section].append({
                    'speaker': current_speaker,
                    'text': ' '.join(current_text).strip()
                })
                current_text = []
            current_section = 'community_spotlight'
            continue

        if 'SEGMENT 2:' in line or '**SEGMENT 2:' in line or 'DEEP DIVE' in line:
            # Save current section (could be news or community_spotlight)
            if current_speaker and current_text:
                segments[current_section].append({
                    'speaker': current_speaker,
                    'text': ' '.join(current_text).strip()
                })
                current_text = []
            current_section = 'deep_dive'
            continue
        
        # Parse speaker tags
        riley_match = re.match(r'\*\*RILEY:\*\*\s*(.*)', line)
        casey_match = re.match(r'\*\*CASEY:\*\*\s*(.*)', line)
        
        if riley_match:
            if current_speaker and current_text:
                segments[current_section].append({
                    'speaker': current_speaker,
                    'text': ' '.join(current_text).strip()
                })
            current_speaker = 'riley'
            current_text = [riley_match.group(1)] if riley_match.group(1) else []
            
        elif casey_match:
            if current_speaker and current_text:
                segments[current_section].append({
                    'speaker': current_speaker,
                    'text': ' '.join(current_text).strip()
                })
            current_speaker = 'casey'
            current_text = [casey_match.group(1)] if casey_match.group(1) else []
            
        elif line and current_speaker:
            # Skip metadata and markers
            if (not line.startswith('#') and 
                not line.startswith('---') and
                not 'SEGMENT' in line and
                not line.startswith('[') and
                not 'AD BREAK' in line):
                current_text.append(line)
    
    # Add final segment
    if current_speaker and current_text:
        segments[current_section].append({
            'speaker': current_speaker,
            'text': ' '.join(current_text).strip()
        })
    
    # Clean up segments
    for section in segments:
        segments[section] = [s for s in segments[section] if len(s['text']) > 10]
    
    print(f"🎭 Parsed script into segments:")
    print(f"   Welcome: {len(segments['welcome'])} segments")
    print(f"   News: {len(segments['news'])} segments")
    print(f"   Community Spotlight: {len(segments['community_spotlight'])} segments")
    print(f"   Deep Dive: {len(segments['deep_dive'])} segments")
    
    return segments

def generate_tts_for_segment(text, speaker, output_file):
    """Generate TTS audio for a text segment."""
    client = get_openai_client()
    if not client:
        raise ValueError("OPENAI_API_KEY not found")

    voice = get_voice_for_host(speaker)

    response = api_retry(lambda: client.audio.speech.create(
        model="tts-1",
        voice=voice,
        input=text,
        speed=1.0
    ))

    with open(output_file, "wb") as f:
        f.write(response.content)

def generate_audio_from_script(script, output_filename):
    """Convert script to audio with music interludes."""
    print("📊 Generating audio with music interludes...")
    
    if not get_openai_client():
        return None
    
    # Check if music files exist
    music_files_exist = all([
        INTRO_MUSIC.exists(),
        INTERVAL_MUSIC.exists(),
        OUTRO_MUSIC.exists()
    ])
    
    if not music_files_exist:
        print("⚠️  Music files not found — falling back to TTS-only mode")
        return generate_audio_tts_only(script, output_filename)
    
    try:
        # Parse script into segments
        segments = parse_script_into_segments(script)
        
        if not segments['welcome'] or not segments['news'] or not segments['deep_dive']:
            print("⚠️  Segment parsing failed - falling back to TTS-only mode")
            return generate_audio_tts_only(script, output_filename)
        
        # Verify music files exist before loading
        for music_path in [INTRO_MUSIC, INTERVAL_MUSIC, OUTRO_MUSIC]:
            if not music_path.exists():
                raise FileNotFoundError(f"Music file missing: {music_path}")
            print(f"   ✅ Found: {music_path} ({music_path.stat().st_size} bytes)")

        # Load and normalize music to target level (ducked below speech)
        intro_music    = normalize_segment(AudioSegment.from_mp3(str(INTRO_MUSIC)),    TARGET_MUSIC_DBFS)
        interval_music = normalize_segment(AudioSegment.from_mp3(str(INTERVAL_MUSIC)), TARGET_MUSIC_DBFS)
        interval_music = interval_music[:INTERVAL_MUSIC_DURATION_MS].fade_out(INTERVAL_FADE_OUT_MS)
        outro_music    = normalize_segment(AudioSegment.from_mp3(str(OUTRO_MUSIC)),    TARGET_MUSIC_DBFS)
        
        silence = AudioSegment.silent(duration=500)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Start with intro music
            combined = intro_music + silence

            # Generate and add welcome section
            print("  🎤 Generating welcome section...")
            for i, segment in enumerate(segments['welcome']):
                temp_file = os.path.join(tmpdir, f"welcome_{i}.mp3")
                print(f"    {segment['speaker']}: {len(segment['text'])} chars")
                generate_tts_for_segment(segment['text'], segment['speaker'], temp_file)
                speech = normalize_segment(AudioSegment.from_mp3(temp_file), TARGET_SPEECH_DBFS)
                combined += speech + silence

            # Add interval music
            combined += interval_music + silence

            # Generate and add news section
            print("  📰 Generating news section...")
            for i, segment in enumerate(segments['news']):
                temp_file = os.path.join(tmpdir, f"news_{i}.mp3")
                print(f"    {segment['speaker']}: {len(segment['text'])} chars")
                generate_tts_for_segment(segment['text'], segment['speaker'], temp_file)
                speech = normalize_segment(AudioSegment.from_mp3(temp_file), TARGET_SPEECH_DBFS)
                combined += speech + silence

            # Add interval music before community spotlight / deep dive
            combined += interval_music + silence

            # Generate and add community spotlight section (if present)
            if segments['community_spotlight']:
                print("  🏘️  Generating community spotlight...")
                for i, segment in enumerate(segments['community_spotlight']):
                    temp_file = os.path.join(tmpdir, f"spotlight_{i}.mp3")
                    print(f"    {segment['speaker']}: {len(segment['text'])} chars")
                    generate_tts_for_segment(segment['text'], segment['speaker'], temp_file)
                    speech = normalize_segment(AudioSegment.from_mp3(temp_file), TARGET_SPEECH_DBFS)
                    combined += speech + silence

            # Generate and add deep dive section
            print("  🔍 Generating deep dive section...")
            for i, segment in enumerate(segments['deep_dive']):
                temp_file = os.path.join(tmpdir, f"deep_{i}.mp3")
                print(f"    {segment['speaker']}: {len(segment['text'])} chars")
                generate_tts_for_segment(segment['text'], segment['speaker'], temp_file)
                speech = normalize_segment(AudioSegment.from_mp3(temp_file), TARGET_SPEECH_DBFS)
                combined += speech + silence

        # Add outro music (after tmpdir context - files cleaned up)
        combined += outro_music
        
        # Export
        combined.export(output_filename, format="mp3")
        
        duration_minutes = len(combined) / 1000 / 60
        file_size_mb = os.path.getsize(output_filename) / 1024 / 1024
        
        print(f"✅ Generated podcast audio with music!")
        print(f"   Duration: {duration_minutes:.1f} minutes")
        print(f"   File size: {file_size_mb:.1f} MB")
        
        return output_filename
        
    except Exception as e:
        print(f"❌ Error generating audio with music: {e}")
        print("⚠️  Falling back to TTS-only mode")
        return generate_audio_tts_only(script, output_filename)

def generate_audio_tts_only(script, output_filename):
    """Fallback: Generate audio without music (TTS only)."""
    print("📊 Generating TTS-only audio...")

    if not get_openai_client():
        print("❌ OPENAI_API_KEY not found in environment")
        return None

    try:
        # Reuse the structured parser and flatten all sections
        parsed = parse_script_into_segments(script)
        segments = parsed['welcome'] + parsed['news'] + parsed['community_spotlight'] + parsed['deep_dive']
        segments = [s for s in segments if len(s['text']) > 10]

        if not segments:
            print("❌ No speaking segments found in script")
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            combined = AudioSegment.empty()
            for i, segment in enumerate(segments):
                print(f"  🎤 Generating audio {i+1}/{len(segments)} ({segment['speaker']}: {len(segment['text'])} chars)")
                temp_file = os.path.join(tmpdir, f"seg_{i:03d}.mp3")
                generate_tts_for_segment(segment['text'], segment['speaker'], temp_file)
                combined += AudioSegment.from_mp3(temp_file)
                combined += AudioSegment.silent(duration=500)

        combined.export(output_filename, format="mp3")

        duration_minutes = len(combined) / 1000 / 60
        file_size_mb = os.path.getsize(output_filename) / 1024 / 1024

        print(f"✅ Generated podcast audio (TTS only)")
        print(f"   Duration: {duration_minutes:.1f} minutes")
        print(f"   File size: {file_size_mb:.1f} MB")

        return output_filename

    except Exception as e:
        print(f"❌ Error generating TTS audio: {e}")
        return None

CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".html": "text/html",
    ".xml": "application/rss+xml",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".txt": "text/plain",
    ".css": "text/css",
    ".js": "application/javascript",
}


def _get_r2_client():
    """Return (boto3 S3 client, bucket name) or (None, None) if credentials missing."""
    account_id = os.environ.get("CF_ACCOUNT_ID")
    access_key = os.environ.get("R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")

    if not all([account_id, access_key, secret_key]):
        return None, None

    import boto3
    r2 = boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )
    bucket = os.environ.get("R2_BUCKET_NAME", "cariboo-signals")
    return r2, bucket


def _upload_file_to_r2(r2_client, bucket, file_path, object_key):
    """Upload a single file to R2. Returns True on success."""
    try:
        ext = os.path.splitext(file_path)[1].lower()
        content_type = CONTENT_TYPES.get(ext, "application/octet-stream")
        r2_client.upload_file(
            file_path,
            bucket,
            object_key,
            ExtraArgs={"ContentType": content_type},
        )
        print(f"   ☁️  Uploaded {object_key} ({content_type})")
        return True
    except Exception as e:
        print(f"   ⚠️  R2 upload failed for {object_key}: {e}")
        return False


def upload_to_r2(file_path, object_key):
    """Upload a file to Cloudflare R2 (S3-compatible).

    Requires environment variables: CF_ACCOUNT_ID, R2_ACCESS_KEY_ID,
    R2_SECRET_ACCESS_KEY. Optional: R2_BUCKET_NAME (default: cariboo-signals).
    Silently skips if credentials are not configured.
    Content type is auto-detected from file extension.
    """
    r2, bucket = _get_r2_client()
    if r2 is None:
        print("   ⏭️  R2 credentials not configured, skipping upload")
        return False
    return _upload_file_to_r2(r2, bucket, file_path, object_key)


def _regenerate_index_html():
    """Regenerate index.html so the latest episodes are reflected."""
    try:
        from generate_html import generate_index_html
        generate_index_html()
    except Exception as e:
        print(f"   ⚠️  Could not regenerate index.html: {e}")


def sync_site_to_r2():
    """Upload all site assets and podcast episodes to R2.

    Uploads: index.html, podcast-feed.xml, cover image, and all audio files.
    """
    r2, bucket = _get_r2_client()
    if r2 is None:
        print("⏭️  R2 credentials not configured, skipping site sync")
        return

    print("☁️  Syncing site to R2...")
    base_dir = Path(__file__).parent

    # Site assets
    site_files = [
        ("index.html", "index.html"),
        ("podcast-feed.xml", "podcast-feed.xml"),
        ("cariboo-signals.png", "cariboo-signals.png"),
    ]
    for local_name, r2_key in site_files:
        local_path = base_dir / local_name
        if local_path.exists():
            _upload_file_to_r2(r2, bucket, str(local_path), r2_key)
        else:
            print(f"   ⚠️  {local_name} not found, skipping")

    # All podcast audio files (backlog + today)
    audio_files = sorted(glob.glob(str(PODCASTS_DIR / "podcast_audio_*.mp3")))
    if audio_files:
        print(f"   Uploading {len(audio_files)} audio episodes...")
        for audio_file in audio_files:
            r2_key = f"podcasts/{os.path.basename(audio_file)}"
            _upload_file_to_r2(r2, bucket, audio_file, r2_key)
    else:
        print("   No audio files to upload")

def generate_podcast_rss_feed():
    """Generate RSS feed with detailed citations for each episode."""
    print("📡 Generating podcast RSS feed with citations...")
    
    podcast_config = CONFIG['podcast']
    credits_config = CONFIG['credits']
    
    podcasts_dir = str(PODCASTS_DIR)
    audio_files = glob.glob(os.path.join(podcasts_dir, "podcast_audio_*.mp3"))
    episodes = []

    # Try to load pydub for actual duration; fall back to config default
    def get_audio_duration(filepath):
        try:
            audio = AudioSegment.from_mp3(filepath)
            total_secs = len(audio) // 1000
            return f"{total_secs // 60}:{total_secs % 60:02d}"
        except Exception:
            return podcast_config["episode_duration"]

    for audio_file in sorted(audio_files, reverse=True):
        audio_basename = os.path.basename(audio_file)
        match = re.search(r'podcast_audio_(\d{4}-\d{2}-\d{2})_(.+)\.mp3', audio_basename)
        if match:
            date_str, theme = match.groups()

            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                pub_date = date_obj.strftime("%a, %d %b %Y 05:00:00 PST")

                # Load corresponding citations file
                safe_theme = theme.replace(' ', '_').replace('&', 'and').lower()
                citations_file = os.path.join(podcasts_dir, f"citations_{date_str}_{safe_theme}.json")

                episode_description = podcast_config["description"]

                # Add citations if file exists
                if os.path.exists(citations_file):
                    try:
                        with open(citations_file, 'r', encoding='utf-8') as f:
                            citations_data = json.load(f)
                        
                        # Add theme context
                        theme_display = theme.replace('_', ' ').title()
                        episode_description += f"\n\nToday's focus: {theme_display}"
                        
                        # Add sources
                        if citations_data.get('segments'):
                            episode_description += "\n\nSources cited in this episode:\n"
                            
                            source_num = 1
                            for segment_name, segment_data in citations_data['segments'].items():
                                for article in segment_data.get('articles', []):
                                    source_name = article.get('source', 'Unknown')
                                    title = article.get('title', '')[:60]
                                    if len(article.get('title', '')) > 60:
                                        title += "..."
                                    episode_description += f"{source_num}. {source_name}: {title}\n"
                                    source_num += 1
                    except Exception as e:
                        print(f"   ⚠️ Could not load citations for {audio_file}: {e}")
                
                # Add credits
                episode_description += credits_config['text']
                
                episodes.append({
                    'title': f"{podcast_config['title']} - {theme.replace('_', ' ').title()}",
                    'audio_url_path': f"podcasts/{audio_basename}",
                    'audio_file': audio_file,
                    'pub_date': pub_date,
                    'file_size': os.path.getsize(audio_file),
                    'duration': get_audio_duration(audio_file),
                    'description': episode_description
                })
            except ValueError:
                continue

    episodes = episodes[:10]  # Keep last 10 episodes
    
    # Generate RSS XML
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
    
    # Use R2 audio URL if configured, otherwise fall back to GitHub Pages
    audio_base = podcast_config.get("audio_base_url", podcast_config["url"])

    # Add episodes with detailed descriptions
    for episode in episodes:
        escaped_title = saxutils.escape(episode['title'])
        escaped_description = saxutils.escape(episode['description'])

        rss_lines.extend([
            '<item>',
            f'<title>{escaped_title}</title>',
            f'<link>{podcast_config["url"]}</link>',
            f'<pubDate>{episode["pub_date"]}</pubDate>',
            f'<description>{escaped_description}</description>',
            f'<itunes:summary>{escaped_description}</itunes:summary>',
            f'<enclosure url="{saxutils.escape(audio_base + episode["audio_url_path"], {chr(34): "&quot;"})}" length="{episode["file_size"]}" type="audio/mpeg"/>',
            f'<guid isPermaLink="false">{podcast_config["title"].lower().replace(" ", "-")}-{os.path.basename(episode["audio_file"]).replace("podcast_audio_", "").replace(".mp3", "")}</guid>',
            f'<itunes:duration>{episode["duration"]}</itunes:duration>',
            f'<itunes:explicit>{"true" if podcast_config["explicit"] else "false"}</itunes:explicit>',
            '</item>'
        ])
    
    rss_lines.extend([
        '</channel>',
        '</rss>'
    ])
    
    with open('podcast-feed.xml', 'w', encoding='utf-8') as f:
        f.write('\n'.join(rss_lines))
    
    print(f"✅ Generated RSS feed with {len(episodes)} episodes (with citations)")

def save_script_to_file(script, theme_name):
    """Save the generated script to a file."""
    if not script:
        return None
    
    pacific_now = get_pacific_now()
    date_str = pacific_now.strftime("%Y-%m-%d")
    safe_theme = theme_name.replace(" ", "_").replace("&", "and").lower()
    script_filename = str(PODCASTS_DIR / f"podcast_script_{date_str}_{safe_theme}.txt")

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

def extract_topics_and_themes(script, news_articles=None, deep_dive_articles=None):
    """Extract main topics from script and source articles for memory."""
    if not script:
        return [], []

    script_lower = script.lower()

    # Extract topics from article titles (more specific than keyword matching)
    topics = []
    if news_articles or deep_dive_articles:
        all_source = (news_articles or [])[:5] + (deep_dive_articles or [])
        for article in all_source:
            title = article.get('title', '').split(' - ')[0].strip()
            if title and len(title) > 10:
                topics.append(title[:60])

    # Supplement with keyword matching for broader themes
    tech_keywords = [
        'AI', 'artificial intelligence', 'machine learning', 'automation',
        'rural broadband', 'digital divide', 'innovation', 'sustainability',
        'community development', 'technology adoption', 'infrastructure',
        'renewable energy', 'solar', 'EV', 'electric vehicle', '3D printing',
        'mesh network', 'fiber optic', 'satellite internet', 'smart home',
        'data sovereignty', 'open source', 'homelab', 'climate tech',
        'precision agriculture', 'telemedicine', 'remote work',
    ]

    for keyword in tech_keywords:
        if keyword.lower() in script_lower and keyword not in topics:
            topics.append(keyword)

    themes = []
    if 'rural' in script_lower or 'community' in script_lower:
        themes.append('rural development')
    if 'innovation' in script_lower or 'technology' in script_lower:
        themes.append('technology adoption')
    if 'sustainability' in script_lower or 'environment' in script_lower:
        themes.append('environmental impact')
    if 'indigenous' in script_lower or 'first nations' in script_lower:
        themes.append('Indigenous tech')
    if 'broadband' in script_lower or 'connectivity' in script_lower:
        themes.append('connectivity')

    return topics[:8], themes[:4]

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
    
    # Check for existing files (stored in podcasts/ subfolder)
    date_key = pacific_now.strftime("%Y-%m-%d")
    safe_theme = today_theme.replace(" ", "_").replace("&", "and").lower()
    script_filename = str(PODCASTS_DIR / f"podcast_script_{date_key}_{safe_theme}.txt")
    audio_filename = str(PODCASTS_DIR / f"podcast_audio_{date_key}_{safe_theme}.mp3")

    script_exists = os.path.exists(script_filename)
    audio_exists = os.path.exists(audio_filename)
    
    if script_exists and audio_exists:
        print(f"✅ Today's episode already exists:")
        print(f"   Script: {script_filename}")
        print(f"   Audio: {audio_filename}")
        generate_podcast_rss_feed()
        _regenerate_index_html()
        sync_site_to_r2()
        return
    
    # Generate script if needed
    if not script_exists:
        print("🆕 Generating new script...")

        # Fetch curated podcast feed (pre-scored, theme-sorted)
        feed_meta, theme_articles, bonus_articles = fetch_podcast_feed()

        if feed_meta is None or not theme_articles:
            # Fallback: use legacy multi-category fetch if podcast feed unavailable
            print("⚠️  Podcast feed unavailable, falling back to category feeds...")
            scoring_data = fetch_scoring_data()
            current_articles = fetch_feed_data()

            if not scoring_data or not current_articles:
                print("❌ Failed to fetch data. Exiting.")
                sys.exit(1)

            scored_articles = get_article_scores(current_articles, scoring_data)
            scored_articles, evolving_stories = deduplicate_articles(scored_articles)
            deep_dive_articles = categorize_articles_for_deep_dive(scored_articles, today_weekday)
            news_articles = scored_articles[:12]
            feed_meta = None
        else:
            # Use the curated podcast feed
            # Override theme from feed if available
            if feed_meta.get('theme'):
                today_theme = feed_meta['theme']
                safe_theme = today_theme.replace(" ", "_").replace("&", "and").lower()
                script_filename = str(PODCASTS_DIR / f"podcast_script_{date_key}_{safe_theme}.txt")
                audio_filename = str(PODCASTS_DIR / f"podcast_audio_{date_key}_{safe_theme}.mp3")

            # Deduplicate all articles against recent episodes
            all_feed_articles = theme_articles + bonus_articles
            all_feed_articles, evolving_stories = deduplicate_articles(all_feed_articles)

            # Re-split after dedup
            bonus_urls = {a.get('url', '') for a in bonus_articles}
            theme_articles = [a for a in all_feed_articles if a.get('url', '') not in bonus_urls]
            bonus_articles = [a for a in all_feed_articles if a.get('url', '') in bonus_urls]

            # Select deep dive from theme articles; rest go to news
            deep_dive_articles, news_articles = select_deep_dive_from_feed(theme_articles, today_theme)

            # Append bonus articles to news, flagged for separate intro
            news_articles = news_articles + bonus_articles

        print(f"📊 Ready to generate podcast:")
        print(f"   News roundup: {len(news_articles)} articles")
        print(f"   Deep dive: {len(deep_dive_articles)} articles")
        print(f"   Theme: {today_theme}")
        if feed_meta and feed_meta.get('theme_description'):
            print(f"   Theme description: {feed_meta['theme_description'][:80]}...")
        print(f"   Memory context: {len(episode_memory)} recent episodes")

        # Generate citations
        citations_file = generate_citations_file(news_articles, deep_dive_articles, today_theme)

        # Inject evolving story context into memory for the prompt
        evolving_context = format_evolving_story_context(evolving_stories)

        # Select today's PSA / Community Spotlight
        psa_info = select_psa(pacific_now.date())
        if psa_info:
            print(f"🏘️  Community Spotlight: {psa_info['org_name']} ({psa_info['source']})")
            if psa_info.get('event_name'):
                print(f"   Event: {psa_info['event_name']}")
        else:
            print("🏘️  No community spotlight for today")

        # Generate script
        script = generate_podcast_script(
            news_articles, deep_dive_articles, today_theme,
            episode_memory, host_memory, evolving_context,
            psa_info=psa_info, feed_meta=feed_meta,
            bonus_articles=bonus_articles
        )

        # Polish the script for better flow
        if script:
            api_key = os.getenv('ANTHROPIC_API_KEY')
            script = polish_script_with_claude(script, today_theme, api_key)

        if not script:
            print("❌ Failed to generate script. Exiting.")
            sys.exit(1)

        # Save script
        script_filename = save_script_to_file(script, today_theme)

        # Update memory
        if script:
            topics, themes = extract_topics_and_themes(script, news_articles, deep_dive_articles)
            update_episode_memory(date_key, topics, themes)

            # Update host memory
            host_insights = {
                'riley': [t for t in topics if 'tech' in t.lower() or 'AI' in t][:2],
                'casey': [t for t in topics if 'community' in t.lower() or 'rural' in t.lower()][:2]
            }
            update_host_memory(host_insights)
    else:
        print(f"🔄 Using existing script: {script_filename}")
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
            print("📊 Audio generation failed")
    elif audio_exists:
        print(f"🎵 Audio already exists: {audio_filename}")

    # Generate RSS feed, regenerate index.html, and sync everything to R2
    generate_podcast_rss_feed()
    _regenerate_index_html()
    sync_site_to_r2()

    print("✅ Generation complete!")

if __name__ == "__main__":
    main()

