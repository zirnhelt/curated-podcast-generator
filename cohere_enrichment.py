"""
Cohere AI enrichment for article selection and deduplication.

Activated by setting USE_COHERE=1 in the environment. All public functions
return None (the sentinel meaning "use the existing fallback") when Cohere
is disabled or when an API call fails, so callers never need to handle errors.

Single revert: set USE_COHERE=0 or unset the variable entirely.

Tunable thresholds (via env vars):
  COHERE_DEDUP_THRESHOLD   — cosine similarity for evolving-story detection (default 0.88)
  COHERE_CLUSTER_THRESHOLD — cosine similarity for same-story clustering (default 0.85)
"""

import math
import os

COHERE_ENABLED = os.getenv("USE_COHERE", "0").strip() == "1"

EMBED_DEDUP_THRESHOLD = float(os.getenv("COHERE_DEDUP_THRESHOLD", "0.88"))
EMBED_CLUSTER_THRESHOLD = float(os.getenv("COHERE_CLUSTER_THRESHOLD", "0.85"))

_client = None


def _get_client():
    global _client
    if _client is None:
        import cohere  # deferred — only loaded when USE_COHERE=1
        _client = cohere.ClientV2(api_key=os.environ["COHERE_API_KEY"])
    return _client


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    mag = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
    return dot / mag if mag else 0.0


def _embed(texts):
    """Embed a list of strings; returns list-of-vectors or None on failure."""
    if not texts:
        return []
    try:
        client = _get_client()
        resp = client.embed(
            texts=texts,
            model="embed-english-v3.0",
            input_type="search_document",
            embedding_types=["float"],
        )
        return resp.embeddings.float
    except Exception as exc:
        print(f"  ⚠️  Cohere embed failed ({exc}), falling back")
        return None


def detect_evolving_stories(new_articles, past_articles):
    """
    Find semantically similar past articles for each new article.

    Returns a list parallel to new_articles where each element is either an
    evolving-story dict (same shape as dedup_articles.py uses) or None when
    no close match was found. Returns None (not a list) when Cohere is
    disabled or the API call fails, signalling callers to use the original
    string-similarity path.
    """
    if not COHERE_ENABLED or not new_articles or not past_articles:
        return None

    new_texts = [f"{a.get('title', '')} {a.get('summary', '')[:150]}" for a in new_articles]
    past_texts = [f"{a.get('title', '')} {a.get('summary', '')[:150]}" for a in past_articles]

    embeddings = _embed(new_texts + past_texts)
    if embeddings is None:
        return None

    new_emb = embeddings[:len(new_texts)]
    past_emb = embeddings[len(new_texts):]

    results = []
    for article, emb in zip(new_articles, new_emb):
        article_url = article.get("url", "")
        best_sim, best_past = 0.0, None
        for past, p_emb in zip(past_articles, past_emb):
            sim = _cosine(emb, p_emb)
            if sim > best_sim:
                best_sim, best_past = sim, past

        if best_sim >= EMBED_DEDUP_THRESHOLD and best_past and article_url != best_past.get("url", ""):
            results.append({
                "article": article,
                "original_date": best_past.get("episode_date", ""),
                "original_title": best_past.get("title", ""),
                "similarity": best_sim,
            })
        else:
            results.append(None)

    return results


def cluster_articles(articles):
    """
    Detect duplicate-story clusters using embedding cosine similarity.

    Returns a copy of articles with _topic_cluster, _cluster_suppressed, and
    _boosted_score fields set — the same contract as cluster_and_rescore_corpus.
    Returns None when Cohere is disabled or the API call fails.
    """
    if not COHERE_ENABLED or len(articles) < 2:
        return None

    texts = [f"{a.get('title', '')} {a.get('summary', '')[:150]}" for a in articles]
    embeddings = _embed(texts)
    if embeddings is None:
        return None

    articles = [a.copy() for a in articles]
    assigned = [-1] * len(articles)
    cluster_id = 0

    for i in range(len(articles)):
        if assigned[i] != -1:
            continue
        members = [i]
        for j in range(i + 1, len(articles)):
            if assigned[j] != -1:
                continue
            if _cosine(embeddings[i], embeddings[j]) >= EMBED_CLUSTER_THRESHOLD:
                members.append(j)
                assigned[j] = cluster_id

        if len(members) < 2:
            continue

        assigned[i] = cluster_id
        cluster_id += 1
        label = f"cluster-{cluster_id}"

        for idx in members:
            articles[idx]["_topic_cluster"] = label

        canonical = max(
            members,
            key=lambda idx: articles[idx].get("_boosted_score", articles[idx].get("ai_score", 0)),
        )
        suppressed = [idx for idx in members if idx != canonical]
        for idx in suppressed:
            orig = articles[idx].get("_boosted_score", articles[idx].get("ai_score", 0))
            articles[idx]["_boosted_score"] = max(1, int(orig * 0.3))
            articles[idx]["_cluster_suppressed"] = True

        canonical_title = articles[canonical].get("title", "")[:60]
        print(
            f"  🔗 Semantic cluster \"{label}\": canonical=\"{canonical_title}\","
            f" suppressed {len(suppressed)} duplicate(s)"
        )

    if cluster_id == 0:
        print("  ✔️  No intra-batch duplicate clusters detected (semantic)")
    return articles


def rerank_for_deep_dive(theme_name, articles, top_n):
    """
    Rerank candidate articles against the day's theme using Cohere Rerank.

    Returns a list of length top_n in relevance order, or None when Cohere
    is disabled or the API call fails (caller uses keyword-sort fallback).
    """
    if not COHERE_ENABLED or not articles:
        return None
    try:
        client = _get_client()
        docs = [f"{a.get('title', '')} {a.get('summary', '')[:300]}" for a in articles]
        resp = client.rerank(
            query=f"Best articles for a podcast deep dive on: {theme_name}",
            documents=docs,
            model="rerank-english-v3.0",
            top_n=top_n,
        )
        reranked = [articles[r.index] for r in resp.results]
        print(f"  🎯 Cohere rerank: selected {len(reranked)} deep-dive articles for '{theme_name}'")
        for a in reranked:
            print(f"    - {a.get('title', '')[:70]}...")
        return reranked
    except Exception as exc:
        print(f"  ⚠️  Cohere rerank failed ({exc}), falling back to keyword sort")
        return None
