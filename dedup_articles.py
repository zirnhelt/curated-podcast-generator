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
from pathlib import Path

PODCASTS_DIR = Path(__file__).parent / "podcasts"

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
    citations_files = glob.glob(str(PODCASTS_DIR / "citations_*.json"))
    
    recent_citations = []
    
    for filename in citations_files:
        try:
            # Extract date from filename: citations_2026-01-24_theme.json
            basename = os.path.basename(filename)
            parts = basename.replace('citations_', '').replace('.json', '').split('_')
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

def cluster_and_rescore_corpus(articles, theme_name, client=None, model=None):
    """
    Identify topic clusters within the current article batch and penalize duplicates.

    Uses Claude to detect when multiple articles cover the same underlying story.
    Within each cluster, the highest-scored article is kept at full score; the
    rest have their _boosted_score reduced to 30% of its original value and are
    tagged _cluster_suppressed=True.

    All clustered articles receive a _topic_cluster string label.

    Falls back silently (returns articles unchanged) if client is None or if
    Claude returns invalid JSON.

    Args:
        articles: list of article dicts (must have title, summary, _boosted_score or ai_score)
        theme_name: today's theme name (used in the prompt for context)
        client: Anthropic client instance (or None to skip)
        model: Claude model ID to use (defaults to claude-haiku-4-5-20251001)

    Returns:
        articles list with updated _boosted_score, _cluster_suppressed, _topic_cluster fields
    """
    if not client or not articles:
        return articles

    if model is None:
        model = "claude-haiku-4-5-20251001"

    # Build compact article list for the prompt
    article_list = []
    for i, a in enumerate(articles):
        article_list.append({
            "index": i,
            "title": a.get("title", ""),
            "summary": a.get("summary", "")[:150],
        })

    prompt = (
        f"You are helping curate a podcast about '{theme_name}'. "
        "Below is a list of articles fetched today. Identify groups where "
        "2 or more articles cover the *same underlying news story* from different "
        "sources (e.g. multiple outlets reporting the same event, announcement, or development).\n\n"
        "Article list (JSON):\n"
        f"{json.dumps(article_list, ensure_ascii=False)}\n\n"
        "Return ONLY valid JSON with no markdown fences:\n"
        '{"clusters": [{"label": "short story label", "indices": [0, 3, 7]}, ...]}\n\n'
        "Rules:\n"
        "- Only include clusters with 2 or more articles.\n"
        "- Each article index may appear in at most one cluster.\n"
        "- If no duplicate stories exist, return {\"clusters\": []}.\n"
        "- Labels should be concise (5 words or fewer)."
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        data = json.loads(raw)
        clusters = data.get("clusters", [])
    except Exception as e:
        print(f"  ⚠️  cluster_and_rescore_corpus: Claude call failed ({e}), skipping")
        return articles

    if not clusters:
        print("  ✔️  No intra-batch duplicate clusters detected")
        return articles

    # Work on copies so callers get a fresh list
    articles = [a.copy() for a in articles]

    for cluster in clusters:
        label = cluster.get("label", "")
        indices = cluster.get("indices", [])
        # Validate indices
        valid = [i for i in indices if isinstance(i, int) and 0 <= i < len(articles)]
        if len(valid) < 2:
            continue

        # Tag all cluster members
        for i in valid:
            articles[i]["_topic_cluster"] = label

        # Canonical = article with highest _boosted_score (or ai_score as fallback)
        canonical_idx = max(
            valid,
            key=lambda i: articles[i].get("_boosted_score", articles[i].get("ai_score", 0)),
        )

        # Penalize the non-canonical duplicates
        suppressed = [i for i in valid if i != canonical_idx]
        for i in suppressed:
            original = articles[i].get("_boosted_score", articles[i].get("ai_score", 0))
            articles[i]["_boosted_score"] = max(1, int(original * 0.3))
            articles[i]["_cluster_suppressed"] = True

        canonical_title = articles[canonical_idx].get("title", "")[:60]
        print(
            f"  🔗 Cluster \"{label}\": canonical=\"{canonical_title}\","
            f" suppressed {len(suppressed)} duplicate(s)"
        )

    return articles


if __name__ == "__main__":
    # Test the deduplication
    print("Testing deduplication module...")
    recent = load_recent_citations(days=7)
    print(f"\nFound {len(recent)} articles in recent history")

    if recent:
        print("\nMost recent articles:")
        for article in recent[:5]:
            print(f"  - [{article['episode_date']}] {article['title'][:80]}...")
