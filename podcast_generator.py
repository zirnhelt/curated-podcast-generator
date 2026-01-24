#!/usr/bin/env python3
"""
Curated Podcast Generator
Converts RSS feed scoring data into conversational podcast scripts.
"""

import os
import sys
import json
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import anthropic
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
SUPER_RSS_BASE_URL = "https://zirnhelt.github.io/super-rss-feed"
SCORING_CACHE_URL = "https://raw.githubusercontent.com/zirnhelt/super-rss-feed/main/scored_articles_cache.json"
FEED_URL = f"{SUPER_RSS_BASE_URL}/super-feed.json"

# Daily themes (Wednesday = Local News)
DAILY_THEMES = {
    0: "AI/ML Infrastructure",        # Monday
    1: "Climate & Clean Energy",      # Tuesday  
    2: "Local News & Canadian Focus", # Wednesday
    3: "Smart Home & Homelab",        # Thursday
    4: "Sci-Fi & Future Tech",        # Friday
    5: "Wild Card",                   # Saturday
    6: "Wild Card"                    # Sunday
}

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

def categorize_articles_by_theme(articles, theme_day):
    """Categorize articles based on daily theme."""
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
    
    # Categorize articles
    theme_articles = []
    general_articles = []
    
    for article in articles:
        title = article.get('title', '').lower()
        summary = article.get('summary', '').lower()
        content = f"{title} {summary}"
        
        # Check if article matches theme
        if theme == "Wild Card" or any(keyword in content for keyword in keywords):
            theme_articles.append(article)
        else:
            general_articles.append(article)
    
    print(f"🎯 Found {len(theme_articles)} articles for '{theme}' theme")
    print(f"📰 Found {len(general_articles)} general articles")
    
    return theme_articles, general_articles

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

def generate_podcast_script(general_articles, theme_articles, theme_name):
    """Generate conversational podcast script with Riley & Casey."""
    print("🎙️ Generating podcast script with Claude...")
    
    # Check for API key
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        print("❌ ANTHROPIC_API_KEY not found in .env file")
        return None
    
    # Prepare articles for script generation
    top_general = general_articles[:8]  # Top 8 for headlines
    top_theme = theme_articles[:3]      # Top 3 for deep dive
    
    # Create article summaries for Claude
    headlines_text = "\n".join([
        f"- [{a.get('authors', [{}])[0].get('name', 'Unknown')}] {a.get('title', '')} (Score: {a.get('ai_score', 0)})"
        for a in top_general
    ])
    
    theme_text = "\n".join([
        f"- [{a.get('authors', [{}])[0].get('name', 'Unknown')}] {a.get('title', '')}\n  {a.get('summary', '')[:200]}... (Score: {a.get('ai_score', 0)})"
        for a in top_theme
    ])
    
    prompt = f"""Create a 30-minute conversational podcast script between two hosts discussing today's curated news.

HOSTS:
- Riley (she/her): Tech systems thinker, engineering background, asks "how does this scale?", optimistic about tech solutions
- Casey (they/them): Impact journalist, focuses on real-world implications, asks "who benefits?", constructive but skeptical

FORMAT:
**SEGMENT 1 (15 minutes): Headlines Roundup**
Natural conversation about these top articles:
{headlines_text}

**SEGMENT 2 (15 minutes): Deep Dive - {theme_name}**
Detailed discussion of these articles with broader context:
{theme_text}

STYLE:
- Natural back-and-forth dialogue
- Different perspectives from each host
- Smooth transitions: "Speaking of X, did you see..."
- Questions to each other: "What's your take on..."
- 30 minutes total (roughly 4,500 words)

Generate the full script with [RILEY:] and [CASEY:] speaker tags."""

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

def save_script_to_file(script, theme_name):
    """Save the generated script to a file."""
    if not script:
        return None
    
    # Create filename with date and theme
    date_str = datetime.now().strftime("%Y-%m-%d")
    safe_theme = theme_name.replace(" ", "_").replace("&", "and").lower()
    filename = f"podcast_script_{date_str}_{safe_theme}.txt"
    
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(f"# Curated Podcast Script - {date_str}\n")
            f.write(f"# Theme: {theme_name}\n")
            f.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(script)
        
        print(f"💾 Saved script to: {filename}")
        return filename
        
    except Exception as e:
        print(f"❌ Error saving script: {e}")
        return None

def main():
    print("🎙️ Curated Podcast Generator")
    print("=" * 40)
    
    # Get today's theme
    today_weekday = datetime.now().weekday()
    today_theme = DAILY_THEMES[today_weekday]
    print(f"📅 Today's theme: {today_theme}")
    
    # Fetch data from live system
    scoring_data = fetch_scoring_data()
    current_articles = fetch_feed_data()
    
    if not scoring_data or not current_articles:
        print("❌ Failed to fetch data. Exiting.")
        return
    
    # Categorize articles
    theme_articles, general_articles = categorize_articles_by_theme(current_articles, today_weekday)
    
    # Add AI scores to articles
    theme_articles = get_article_scores(theme_articles, scoring_data)
    general_articles = get_article_scores(general_articles, scoring_data)
    
    print(f"📊 Ready to generate podcast with {len(current_articles)} total articles")
    
    # Generate podcast script
    script = generate_podcast_script(general_articles, theme_articles, today_theme)
    
    # Save script to file
    if script:
        filename = save_script_to_file(script, today_theme)
        if filename:
            print(f"🎉 Podcast script ready: {filename}")
    
    print("✅ Script generation complete!")

if __name__ == "__main__":
    main()