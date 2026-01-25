#!/usr/bin/env python3
"""
Article Deduplication Module for Range Signals Podcast
Checks last 7 days of citations to avoid repeating stories.
Detects evolving stories (same topic, different URL) for contextual references.
"""

import os
import json
import glob
from datetime import datetime, timedelta
from difflib import SequenceMatcher

def normalize_title(title):
    """Normalize title for comparison by removing source tags and cleaning."""
    # Remove source tags like [Source Name]
    import re
    cleaned = re.sub(r'\[.*?\]\s*', '', title)
    cleaned = cleaned.lower().strip()
    return cleaned

def title_similarity(title1, title2):
    """Calculate similarity ratio between two titles (0.0 to 1.0)."""
    norm1 = normalize_title(title1)
    norm2 = normalize_title(title2)
    return SequenceMatcher(None, norm1, norm2).ratio()

def load_recent_citations(days=7):
    """Load citations from the last N days."""
    cutoff_date = datetime.now() - timedelta(days=days)
    citations_files = glob.glob("citations_*.json")
    
    recent_citations = []
    
    for filename in citations_files:
        try:
            # Extract date from filename: citations_2026-01-24_theme.json
            parts = filename.replace('citations_', '').replace('.json', '').split('_')
            if len(parts) >= 2:
                date_str = parts[0]
                file_date = datetime.strptime(date_str, '%Y-%m-%d')
                
                if file_date >= cutoff_date:
                    with open(filename, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        episode_date = data['episode']['date']
                        
                        # Collect all articles from this episode
                        for segment_name, segment_data in data['segments'].items():
                            for article in segment_data['articles']:
                                recent_citations.append({
                                    'url': article['url'],
                                    'title': article['title'],
                                    'episode_date': episode_date,
                                    'segment': segment_name
                                })
        except Exception as e:
            print(f"  ⚠️ Error loading {filename}: {e}")
            continue
    
    print(f"📚 Loaded {len(recent_citations)} articles from last {days} days")
    return recent_citations

def deduplicate_articles(new_articles, similarity_threshold=0.70):
    """
    Deduplicate articles against recent history.
    
    Returns:
        - filtered_articles: Articles not previously covered
        - evolving_stories: Articles that are updates to previous coverage
    """
    recent_citations = load_recent_citations(days=7)
    
    if not recent_citations:
        print("  ℹ️ No recent citations found, no deduplication needed")
        return new_articles, []
    
    # Build lookup structures
    covered_urls = {c['url'] for c in recent_citations}
    
    filtered_articles = []
    evolving_stories = []
    skipped_count = 0
    
    for article in new_articles:
        article_url = article.get('url', '')
        article_title = article.get('title', '')
        
        # Check for exact URL match (duplicate)
        if article_url in covered_urls:
            skipped_count += 1
            continue
        
        # Check for evolving story (similar title, different URL)
        is_evolving = False
        for past_article in recent_citations:
            similarity = title_similarity(article_title, past_article['title'])
            
            if similarity >= similarity_threshold and article_url != past_article['url']:
                # This is an update to a previous story
                evolving_stories.append({
                    'article': article,
                    'original_date': past_article['episode_date'],
                    'original_title': past_article['title'],
                    'similarity': similarity
                })
                is_evolving = True
                break
        
        # Add article (whether evolving or new)
        filtered_articles.append(article)
    
    print(f"  ✅ Filtered: {len(filtered_articles)} articles")
    print(f"  🔄 Evolving stories: {len(evolving_stories)} updates to previous coverage")
    print(f"  ⏭️  Skipped: {skipped_count} exact duplicates")
    
    return filtered_articles, evolving_stories

def format_evolving_story_context(evolving_stories):
    """Format evolving stories for Claude prompt context."""
    if not evolving_stories:
        return ""
    
    context_lines = ["\n**EVOLVING STORIES - Updates to Previous Coverage:**"]
    
    for story in evolving_stories:
        article = story['article']
        context_lines.append(
            f"- \"{article.get('title', '')}\" is an update to coverage from {story['original_date']}"
        )
    
    return "\n".join(context_lines)

if __name__ == "__main__":
    # Test the deduplication
    print("Testing deduplication module...")
    recent = load_recent_citations(days=7)
    print(f"\nFound {len(recent)} articles in recent history")
    
    if recent:
        print("\nMost recent articles:")
        for article in recent[:5]:
            print(f"  - [{article['episode_date']}] {article['title'][:80]}...")
