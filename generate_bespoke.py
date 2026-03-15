#!/usr/bin/env python3
"""
Bespoke Podcast Generator

Generates a long-form debate episode (~35-45 min) from user-curated URLs tagged
with a topic tag, augmented with auto-expanded credible sources via Brave Search.

Hosts:
  Riley (she/her,   voice: nova) — Tech optimist & empiricist, follows evidence and deployments
  Casey (they/them, voice: echo) — Community skeptic & systems thinker, follows power and context

Cariboo Signals foundation: land acknowledgment and regional awareness woven into the intro.
No news roundup, no PSA. The entire episode is the deep dive.

Usage:
    python generate_bespoke.py --tag "billionaires"
    python generate_bespoke.py --tag "middle-east" --threshold 2
"""

import argparse
import glob
import json
import os
import re
import sys
import tempfile
import xml.sax.saxutils as saxutils
from datetime import datetime, timezone
from pathlib import Path

import requests

try:
    from anthropic import Anthropic
    from openai import OpenAI
    from pydub import AudioSegment
except ImportError as e:
    print(f"Missing required library: {e}")
    print("Install with: pip install anthropic openai pydub")
    sys.exit(1)

# ── Paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
PODCASTS_DIR = SCRIPT_DIR / "podcasts"
BESPOKE_DIR = PODCASTS_DIR / "bespoke"
SEEDS_FILE = PODCASTS_DIR / "content_seeds.json"
BESPOKE_MEMORY_FILE = PODCASTS_DIR / "bespoke_debate_memory.json"

BESPOKE_INTRO_MUSIC = SCRIPT_DIR / "bespoke-theme-intro.mp3"
BESPOKE_OUTRO_MUSIC = SCRIPT_DIR / "bespoke-theme-outro.mp3"
BESPOKE_INTERVAL_MUSIC = SCRIPT_DIR / "bespoke-theme-interval.mp3"

# Fallbacks to shared cariboo-signals tracks if the bespoke theme files are absent
_CARIBOO_INTRO = SCRIPT_DIR / "cariboo-signals-intro.mp3"
_CARIBOO_OUTRO = SCRIPT_DIR / "cariboo-signals-outro.mp3"
_CARIBOO_INTERVAL = SCRIPT_DIR / "cariboo-signals-interval.mp3"

# ── Models ─────────────────────────────────────────────────────────────────
SCRIPT_MODEL = os.getenv("CLAUDE_SCRIPT_MODEL", "claude-sonnet-4-20250514")
POLISH_MODEL = os.getenv("CLAUDE_POLISH_MODEL", "claude-sonnet-4-20250514")

# ── Audio levels ───────────────────────────────────────────────────────────
TARGET_SPEECH_DBFS = -20.0
TARGET_MUSIC_DBFS = -28.0


# ── API clients ────────────────────────────────────────────────────────────

def get_anthropic_client():
    if not hasattr(get_anthropic_client, '_client'):
        api_key = os.getenv('ANTHROPIC_API_KEY')
        if not api_key:
            return None
        get_anthropic_client._client = Anthropic(api_key=api_key)
    return get_anthropic_client._client


def get_openai_client():
    if not hasattr(get_openai_client, '_client'):
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            return None
        get_openai_client._client = OpenAI(api_key=api_key)
    return get_openai_client._client


# ── Retry helper ───────────────────────────────────────────────────────────

def expand_tag(tag: str, client) -> str:
    """Expand a short, cryptic tag slug into a rich plain-English topic description.

    Tags like "billionaires" or "middle-east" are by nature terse.  This asks
    Claude to broaden them into a fuller description of the topic space so that
    the script prompt has richer context to work with.  The expanded description
    is used in prompts alongside (not instead of) the original tag slug, which
    is still used for file naming and memory keys.
    """
    prompt = (
        f"The following is a short topic tag for a long-form podcast episode: \"{tag}\"\n\n"
        "Tags are intentionally cryptic slugs.  Expand this into a rich, plain-English "
        "topic description (3-5 sentences) that:\n"
        "- States the full topic clearly and without jargon\n"
        "- Names the key tensions, debates, or dimensions worth exploring\n"
        "- Suggests the scope (historical, economic, political, ethical, etc.) that is relevant\n"
        "- Does NOT presuppose a conclusion or editorial angle\n\n"
        "Return only the plain-English description.  No preamble, no bullet points."
    )
    try:
        response = api_retry(lambda: client.messages.create(
            model=SCRIPT_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        ))
        expanded = response.content[0].text.strip()
        print(f"  Tag expanded: \"{tag}\" → {expanded[:120]}{'…' if len(expanded) > 120 else ''}")
        return expanded
    except Exception as e:
        print(f"  Tag expansion failed ({e}), using raw tag")
        return tag


def api_retry(func, max_retries=3, base_delay=2):
    import time
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            err = str(e)
            transient = any(s in err for s in ['429', '503', '502', 'timeout', 'Connection'])
            if attempt < max_retries and transient:
                delay = base_delay * (2 ** attempt)
                print(f"  Retrying in {delay}s ({attempt+1}/{max_retries}): {e}")
                time.sleep(delay)
            else:
                raise


# ── Config ─────────────────────────────────────────────────────────────────

def load_bespoke_hosts():
    with open(SCRIPT_DIR / "config" / "bespoke_hosts.json") as f:
        return json.load(f)["default_bespoke"]


def load_bespoke_config():
    cfg_file = SCRIPT_DIR / "config" / "bespoke_config.json"
    if not cfg_file.exists():
        return {}
    with open(cfg_file) as f:
        return json.load(f)


# ── Seeds ──────────────────────────────────────────────────────────────────

def load_seeds_for_tag(tag):
    tag_lower = tag.lower()
    if not SEEDS_FILE.exists():
        return []
    with open(SEEDS_FILE) as f:
        data = json.load(f)
    return [
        s for s in data.get("seeds", [])
        if s.get("tag", "").lower() == tag_lower and s.get("status") == "pending"
    ]


def mark_seeds_used(tag, seeds):
    if not SEEDS_FILE.exists() or not seeds:
        return
    seed_ids = {s["id"] for s in seeds}
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with open(SEEDS_FILE) as f:
        data = json.load(f)
    for s in data.get("seeds", []):
        if s["id"] in seed_ids:
            s["status"] = "used_bespoke"
            s["used_on"] = date_str
    with open(SEEDS_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ── Article fetching ───────────────────────────────────────────────────────

def fetch_url_content(url):
    """Fetch title, description, and simplified body text from a URL."""
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        html = resp.text

        title = ""
        m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', html, re.I | re.S)
        if m:
            title = m.group(1).strip()
        if not title:
            m = re.search(r'<title[^>]*>(.*?)</title>', html, re.I | re.S)
            if m:
                title = re.sub(r'\s+', ' ', m.group(1)).strip()

        desc = ""
        m = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']', html, re.I | re.S)
        if m:
            desc = m.group(1).strip()
        if not desc:
            m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', html, re.I | re.S)
            if m:
                desc = m.group(1).strip()

        body = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html, flags=re.I | re.S)
        body = re.sub(r'<[^>]+>', ' ', body)
        body = re.sub(r'\s+', ' ', body).strip()

        return title[:200], desc[:500], body[:3000]
    except Exception:
        return "", "", ""


# ── Source expansion via Brave Search ──────────────────────────────────────

def generate_search_queries(tag, articles_summary, client, tag_description=""):
    """Ask Claude for search queries to broaden coverage beyond user seeds."""
    topic_context = tag_description if tag_description else tag
    prompt = (
        f"You are helping find credible sources for a podcast episode about: {topic_context}\n\n"
        f"These articles have already been curated:\n{articles_summary}\n\n"
        "Generate exactly 4 diverse search queries to find DIFFERENT credible perspectives, "
        "counterarguments, historical context, or expert analysis not in the existing articles. "
        "Prefer queries that will return results from newspapers of record, think tanks, academic "
        "sources, or established policy organizations.\n\n"
        "Return ONLY the 4 queries, one per line, no numbering or extra text."
    )
    response = api_retry(lambda: client.messages.create(
        model=SCRIPT_MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    ))
    return [q.strip() for q in response.content[0].text.strip().split('\n') if q.strip()][:4]


def brave_search(query, api_key, count=5):
    try:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": api_key,
            },
            params={"q": query, "count": count, "search_lang": "en", "safesearch": "moderate"},
            timeout=10,
        )
        resp.raise_for_status()
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "description": r.get("description", "")}
            for r in resp.json().get("web", {}).get("results", [])
        ]
    except Exception as e:
        print(f"  Brave search failed for '{query[:50]}': {e}")
        return []


def expand_sources(tag, user_articles, client, config, tag_description=""):
    """Fetch additional credible sources to complement user seeds."""
    source_cfg = config.get("source_expansion", {})
    if not source_cfg.get("enabled", True):
        return []

    brave_key = os.getenv("BRAVE_SEARCH_API_KEY")
    if not brave_key:
        print("  BRAVE_SEARCH_API_KEY not set — skipping source expansion")
        return []

    max_additional = source_cfg.get("max_additional_sources", 5)

    articles_summary = "\n".join(
        f"- {a['title']}: {a.get('summary', '')[:150]}" for a in user_articles
    )

    print("  Generating search queries...")
    queries = generate_search_queries(tag, articles_summary, client, tag_description=tag_description)
    print(f"  Got {len(queries)} queries")

    seen_urls = {a['url'] for a in user_articles}
    candidates = []
    for query in queries:
        print(f"    Searching: {query[:60]}")
        for r in brave_search(query, brave_key):
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                candidates.append(r)

    print(f"  Found {len(candidates)} candidate sources, selecting top {max_additional}")
    return [
        {
            "title": r["title"],
            "url": r["url"],
            "summary": r["description"],
            "source_type": "auto",
            "ai_score": 70,
        }
        for r in candidates[:max_additional]
    ]


# ── Memory ─────────────────────────────────────────────────────────────────

def load_bespoke_memory(tag):
    if not BESPOKE_MEMORY_FILE.exists():
        return []
    with open(BESPOKE_MEMORY_FILE) as f:
        data = json.load(f)
    cutoff = datetime.now(timezone.utc).timestamp() - 90 * 86400
    return [e for e in data.get(tag.lower(), []) if e.get("timestamp", 0) > cutoff]


def save_bespoke_memory(tag, debate_summary):
    BESPOKE_MEMORY_FILE.parent.mkdir(exist_ok=True)
    data = {}
    if BESPOKE_MEMORY_FILE.exists():
        with open(BESPOKE_MEMORY_FILE) as f:
            data = json.load(f)
    tag_key = tag.lower()
    entries = data.get(tag_key, [])
    entries.append({
        "timestamp": datetime.now(timezone.utc).timestamp(),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        **debate_summary,
    })
    data[tag_key] = entries[-10:]
    with open(BESPOKE_MEMORY_FILE, "w") as f:
        json.dump(data, f, indent=2)


def format_memory_for_prompt(past_debates):
    if not past_debates:
        return ""
    lines = [f"\nPAST DEBATES ON THIS TOPIC ({len(past_debates)} previous episode(s)):"]
    for d in past_debates[-3:]:
        lines.append(
            f"- {d.get('date', '')}: \"{d.get('central_question', '')}\" "
            f"→ {d.get('resolution', 'unresolved')}"
        )
    lines.append("\nChoose a DIFFERENT central question or angle than those above.")
    return "\n".join(lines)


# ── Script generation ──────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are generating a long-form podcast episode in the style of rigorous, well-researched debate journalism in audio form.

HOSTS:
- Riley (she/her): Technology Optimist & Empiricist. Believes tech can transform rural communities but backs every claim with evidence — real deployments, named communities, measurable outcomes. Holds herself to the same evidentiary standard she holds others. Willing to concede when evidence points somewhere inconvenient, but defaults to "how do we make this work" once the evidence clears the bar. Recurring questions: "What does the evidence actually show?", "What's the cost of NOT adopting this?", "Where has this already worked in a small community?", "What would responsible deployment look like?", "How would we know if this were wrong?"
- Casey (they/them): Community Skeptic & Systems Thinker. Follows incentives, power structures, and long historical patterns. Asks who funded the research, whose interests the framing serves, and what happens to communities when the pilot money runs out. Deeply interested in digital equity and Indigenous-led innovation, but demands proof over hype. Recurring questions: "Who actually benefits from this?", "What historical pattern does this repeat?", "Whose interests does this framing serve, and whose does it obscure?", "What happens when the funding runs out?", "What's being left out of this picture?"

DYNAMIC: Neither is naive or dogmatic. Riley wants evidence of what works; Casey wants context for who it works for. Both are intellectually honest and challenge each other with specifics — not just opinions. They can reach partial agreement, maintain different views, or find the question itself is wrong. The show's value is in the quality of the thinking, not in taking predictable sides.

FORMAT:
- Speaker tags: **RILEY:** and **CASEY:** (bold name + colon, space before text)
- Optional pacing hints at the start of a turn: [overlap:-150] for a quick interjection, [pause:500] for a considered beat
- No segment markers — the entire episode is one continuous discussion

EPISODE STRUCTURE:
1. INTRO (150-200 words): Both hosts introduce themselves by name and approach. They open with a brief, natural land acknowledgment — they're broadcasting from the Cariboo, on the traditional territories of the Secwépemc, Tŝilhqot'in, and Dakelh nations. Keep it genuine, not formulaic — vary the phrasing. Then name the topic and frame the central tension or question they'll explore. Stakes established. Warm but not generic. End this section with exactly the following on its own line:
[CHIME]

2. MAIN DISCUSSION (5,000-7,000 words):
   - Open by steelmanning the strongest version of both perspectives
   - At least 5 substantive point/counterpoint exchanges where each host challenges the other with specifics
   - Each exchange should build on the previous one — complexity increases as the episode progresses
   - Every specific claim must come directly from the source articles OR be explicitly hedged ("some research suggests...", "the pattern in comparable cases...", "one documented example is...")
   - At least 3 moments where a host genuinely shifts, concedes, or refines their position based on what the other said
   - Intellectual humor is welcome when it's earned; avoid forced banter

3. RESOLUTION (200-300 words): Earned endpoint — not forced agreement. May be: shifted perspective, better-defined disagreement, mixed conclusion, or actionable framing. Close with 2-3 concrete, specific calls to action that both hosts genuinely endorse — things a listener could actually do, research, or get involved in. These must feel earned by the debate, not tacked on.

EVIDENCE RULES:
- Do NOT invent statistics, dollar amounts, program names, or study findings
- If a claim isn't in the source articles and isn't widely known public fact: hedge it
- Acceptable hedges: "some studies suggest...", "examples include...", "advocates argue...", "critics point out..."
- No weather check, no PSA segments"""


def generate_bespoke_script(tag, all_articles, past_debates, client, tag_description=""):
    user_articles = [a for a in all_articles if a.get("source_type") != "auto"]
    auto_articles = [a for a in all_articles if a.get("source_type") == "auto"]

    sources_block = "SOURCE ARTICLES:\n\n"
    if user_articles:
        sources_block += "=== Curated by host ===\n"
        for a in user_articles:
            body_excerpt = a.get("body", "")[:500]
            summary = a.get("summary", "") or body_excerpt
            sources_block += f"Title: {a['title']}\nURL: {a['url']}\nSummary: {summary[:500]}\n\n"
    if auto_articles:
        sources_block += "=== Additional credible sources (auto-expanded) ===\n"
        for a in auto_articles:
            sources_block += f"Title: {a['title']}\nURL: {a['url']}\nSummary: {a.get('summary', '')[:400]}\n\n"

    memory_block = format_memory_for_prompt(past_debates)

    topic_block = f"TOPIC TAG: {tag}\n"
    if tag_description:
        topic_block += f"TOPIC DESCRIPTION: {tag_description}\n"

    user_prompt = (
        f"{topic_block}\n"
        f"{sources_block}\n"
        f"{memory_block}\n\n"
        "Generate a complete long-form debate podcast episode on this topic. "
        "Riley and Casey should engage seriously with the source material, citing specific "
        "information from the articles above. The episode should feel like the best long-form "
        "journalism you've ever heard — rigorous, illuminating, and genuinely interesting. "
        "Do not pad or repeat. Every exchange should move the conversation forward."
    )

    print(f"  Generating script (~6000 words) with {SCRIPT_MODEL}...")
    response = api_retry(lambda: client.messages.create(
        model=SCRIPT_MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}]
    ))
    return response.content[0].text


def polish_bespoke_script(script, tag, client):
    prompt = (
        f"You are polishing a podcast script about: {tag}\n\n"
        "Review and improve:\n"
        "1. Remove repeated arguments or circular exchanges\n"
        "2. Ensure each point/counterpoint builds on the previous one\n"
        "3. Tighten passages where hosts are agreeing without adding new information\n"
        "4. Verify the resolution feels earned and specific, not generic\n"
        "5. Ensure **RILEY:** and **CASEY:** speaker tags are properly formatted throughout\n"
        "6. Maintain the overall length — do not cut substantially\n\n"
        f"SCRIPT:\n{script}\n\n"
        "Return the complete polished script. No commentary."
    )
    print("  Polishing script...")
    response = api_retry(lambda: client.messages.create(
        model=POLISH_MODEL,
        max_tokens=16000,
        messages=[{"role": "user", "content": prompt}]
    ))
    polished = response.content[0].text
    if "**RILEY:**" in polished and "**CASEY:**" in polished:
        return polished
    print("  Warning: polish may have broken format, using original")
    return script


def fact_check_bespoke_script(script, all_articles, client):
    verified_sources = "\n".join(
        f"- {a.get('title', '')} ({a.get('url', '')})\n  {a.get('summary', '')[:300]}"
        for a in all_articles
    )
    prompt = (
        "You are fact-checking a podcast script. The hosts are AI-generated and may cite specific "
        "statistics, dollar amounts, program names, and study findings that sound authoritative but "
        "are actually fabricated.\n\n"
        "VERIFIED SOURCE MATERIAL (only these can be treated as confirmed):\n"
        f"{verified_sources}\n\n"
        "RULES:\n"
        "1. Claims directly from verified sources — KEEP\n"
        "2. Well-known public facts — KEEP\n"
        "3. Specific statistics, dollar amounts, percentages, project names, or study findings NOT from "
        "the verified sources — rewrite with honest hedging: 'some research suggests...', "
        "'examples include...', 'the pattern in comparable cases...'\n"
        "4. Do NOT remove interesting arguments — just make the evidence honest\n"
        "5. Preserve all **RILEY:** and **CASEY:** speaker tags exactly\n"
        "6. Maintain the same overall script length\n\n"
        f"SCRIPT:\n{script}\n\n"
        "Return the complete fact-checked script. No commentary."
    )
    print("  Fact-checking script...")
    response = api_retry(lambda: client.messages.create(
        model=POLISH_MODEL,
        max_tokens=16000,
        messages=[{"role": "user", "content": prompt}]
    ))
    checked = response.content[0].text
    if "**RILEY:**" in checked and "**CASEY:**" in checked:
        return checked
    print("  Warning: fact-check may have broken format, using original")
    return script


def extract_debate_summary(script, tag, client):
    prompt = (
        f"Extract a structured summary from this podcast debate about '{tag}'.\n\n"
        "Return a JSON object with:\n"
        "- central_question: the main question debated (string)\n"
        "- riley_position: Riley's core argument (string)\n"
        "- casey_position: Casey's core argument (string)\n"
        "- resolution: how the debate resolved or what was left open (string)\n"
        "- topics_covered: 4-6 key topics discussed (array of strings)\n"
        "- calls_to_action: every concrete action, resource, or next step that both hosts "
        "agreed on or explicitly endorsed at the end of the episode (array of strings, "
        "empty array if none)\n\n"
        f"SCRIPT:\n{script[-3000:]}\n\n"
        "Return only valid JSON, no other text."
    )
    try:
        response = api_retry(lambda: client.messages.create(
            model=SCRIPT_MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        ))
        return json.loads(response.content[0].text)
    except Exception:
        human_tag = tag.replace("-", " ").replace("_", " ").title()
        return {"central_question": f"Discussion of {human_tag}", "resolution": "See episode", "calls_to_action": []}


# ── Audio assembly ─────────────────────────────────────────────────────────

def _extract_pacing_tag(text):
    m = re.match(r'\[(?:overlap|pause):(-?\d+)\]\s*', text)
    if m:
        return int(m.group(1)), text[m.end():]
    return None, text


def normalize_segment(audio_segment, target_dbfs):
    change = target_dbfs - audio_segment.dBFS
    return audio_segment.apply_gain(change)


def trim_tts_silence(segment, silence_thresh=-45, min_silence_len=80):
    from pydub.silence import detect_leading_silence
    lead = detect_leading_silence(segment, silence_threshold=silence_thresh, chunk_size=min_silence_len)
    trail = detect_leading_silence(segment.reverse(), silence_threshold=silence_thresh, chunk_size=min_silence_len)
    end = len(segment) - trail
    if end <= lead:
        return segment
    return segment[lead:end]


def heuristic_gap_ms(text, prev_speaker, cur_speaker):
    char_count = len(text.strip())
    if cur_speaker and prev_speaker == cur_speaker:
        return 0
    if char_count <= 25:
        return 50
    if char_count <= 80:
        return 150
    return 350


def _append_with_gap(combined, speech, gap_ms):
    if gap_ms is None:
        gap_ms = 350
    if gap_ms > 0:
        combined += AudioSegment.silent(duration=gap_ms) + speech
    elif gap_ms == 0:
        combined = combined + speech
    else:
        overlap_ms = abs(gap_ms)
        if overlap_ms >= len(combined):
            return speech
        combined = combined[:-overlap_ms].append(speech, crossfade=0)
    return combined


def parse_bespoke_script(script):
    """Parse bespoke script into a list of turn dicts.

    Each dict has: speaker, text, gap_ms.
    A special sentinel {'speaker': '__CHIME__', 'text': '', 'gap_ms': None} is
    inserted wherever the script contains a bare [CHIME] line, marking where
    the intermission chime should play between intro and main discussion.
    """
    turns = []
    current_speaker = None
    current_text = []
    current_gap_ms = None

    for line in script.split('\n'):
        line = line.strip()

        # Intermission chime marker
        if line == '[CHIME]':
            if current_speaker and current_text:
                turns.append({
                    'speaker': current_speaker,
                    'text': ' '.join(current_text).strip(),
                    'gap_ms': current_gap_ms,
                })
                current_speaker = None
                current_text = []
                current_gap_ms = None
            turns.append({'speaker': '__CHIME__', 'text': '', 'gap_ms': None})
            continue

        riley_m = re.match(r'\*\*RILEY:\*\*\s*(.*)', line)
        casey_m = re.match(r'\*\*CASEY:\*\*\s*(.*)', line)

        if riley_m or casey_m:
            if current_speaker and current_text:
                turns.append({
                    'speaker': current_speaker,
                    'text': ' '.join(current_text).strip(),
                    'gap_ms': current_gap_ms,
                })
            if riley_m:
                current_speaker = 'riley'
                text_after = riley_m.group(1) or ''
            else:
                current_speaker = 'casey'
                text_after = casey_m.group(1) or ''
            current_gap_ms, text_after = _extract_pacing_tag(text_after)
            current_text = [text_after] if text_after else []
        elif line and current_speaker:
            if not line.startswith('#') and not line.startswith('---') and not line.startswith('['):
                current_text.append(line)

    if current_speaker and current_text:
        turns.append({
            'speaker': current_speaker,
            'text': ' '.join(current_text).strip(),
            'gap_ms': current_gap_ms,
        })

    return [t for t in turns if t['speaker'] == '__CHIME__' or len(t['text']) > 10]


def generate_tts_segment(text, speaker, output_file, hosts):
    client = get_openai_client()
    if not client:
        raise ValueError("OPENAI_API_KEY not found")
    voice = hosts[speaker]["voice"]
    response = api_retry(lambda: client.audio.speech.create(
        model="tts-1",
        voice=voice,
        input=text,
        speed=1.0,
    ))
    with open(output_file, "wb") as f:
        f.write(response.content)


def generate_audio(script, output_path, hosts, config):
    """Assemble bespoke audio: [theme] + intro + [chime] + episode + [outro]."""
    if not get_openai_client():
        print("  OPENAI_API_KEY not set — skipping audio generation")
        return None

    audio_cfg = config.get("audio", {})

    intro_path = BESPOKE_INTRO_MUSIC if BESPOKE_INTRO_MUSIC.exists() else _CARIBOO_INTRO
    outro_path = BESPOKE_OUTRO_MUSIC if BESPOKE_OUTRO_MUSIC.exists() else _CARIBOO_OUTRO
    interval_path = BESPOKE_INTERVAL_MUSIC if BESPOKE_INTERVAL_MUSIC.exists() else _CARIBOO_INTERVAL

    use_theme = audio_cfg.get("use_intro_music", True) and intro_path.exists()
    use_outro = audio_cfg.get("use_outro_music", True) and outro_path.exists()
    use_chime = interval_path.exists()

    turns = parse_bespoke_script(script)
    if not turns:
        print("  No speaker turns found in script")
        return None

    speech_turns = [t for t in turns if t['speaker'] != '__CHIME__']
    print(f"  Parsed {len(speech_turns)} speaker turns")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            combined = AudioSegment.empty()

            if use_theme:
                theme_full = normalize_segment(AudioSegment.from_mp3(str(intro_path)), TARGET_MUSIC_DBFS)
                theme = theme_full[:10000].fade_out(500)
                combined = theme + AudioSegment.silent(duration=500)
                print(f"  Added intro music: {intro_path.name} ({len(theme)/1000:.1f}s, trimmed to 10s)")

            prev_speaker = None
            tts_idx = 0
            for turn in turns:
                if turn['speaker'] == '__CHIME__':
                    if use_chime:
                        chime_raw = AudioSegment.from_mp3(str(interval_path))
                        chime = normalize_segment(chime_raw[:1450], TARGET_MUSIC_DBFS).fade_out(400)
                        combined += AudioSegment.silent(duration=300) + chime + AudioSegment.silent(duration=300)
                        print(f"  Added intermission chime ({len(chime)/1000:.1f}s)")
                    else:
                        combined += AudioSegment.silent(duration=800)
                        print("  Intermission chime file not found — inserted silence")
                    prev_speaker = None
                    continue

                tts_idx += 1
                print(f"  TTS {tts_idx}/{len(speech_turns)} ({turn['speaker']}: {len(turn['text'])} chars)")
                temp_file = os.path.join(tmpdir, f"turn_{tts_idx:03d}.mp3")
                generate_tts_segment(turn['text'], turn['speaker'], temp_file, hosts)
                speech = normalize_segment(AudioSegment.from_mp3(temp_file), TARGET_SPEECH_DBFS)
                speech = trim_tts_silence(speech)
                gap = turn.get('gap_ms')
                if gap is None:
                    gap = heuristic_gap_ms(turn['text'], prev_speaker, turn['speaker'])
                combined = _append_with_gap(combined, speech, gap)
                prev_speaker = turn['speaker']

            if use_outro:
                outro = normalize_segment(AudioSegment.from_mp3(str(outro_path)), TARGET_MUSIC_DBFS)
                combined += AudioSegment.silent(duration=500) + outro
                print(f"  Added outro music ({len(outro)/1000:.1f}s)")

        combined.export(str(output_path), format="mp3")
        duration_min = len(combined) / 1000 / 60
        size_mb = output_path.stat().st_size / 1024 / 1024
        print(f"  Audio: {duration_min:.1f} min, {size_mb:.1f} MB → {output_path.name}")
        return str(output_path)

    except Exception as e:
        print(f"  Error generating audio: {e}")
        return None


# ── Citations and show notes ───────────────────────────────────────────────

def match_articles_to_script(articles, script):
    if not script:
        return [(a, True) for a in articles]
    script_lower = script.lower()
    results = []
    for article in articles:
        raw_title = article.get('title', '')
        cleaned = re.sub(r'^[^\[]*\[[^\]]*\]\s*', '', raw_title).strip()
        cleaned = re.split(r'\s*[-–—]\s*(?=[A-Z])', cleaned)[0].strip()
        if not cleaned or len(cleaned) < 6:
            results.append((article, True))
            continue
        words = cleaned.split()
        discussed = cleaned.lower() in script_lower
        if not discussed:
            for window_size in range(min(5, len(words)), 2, -1):
                for i in range(len(words) - window_size + 1):
                    phrase = ' '.join(words[i:i + window_size]).lower()
                    if len(phrase) >= 10 and phrase in script_lower:
                        discussed = True
                        break
                if discussed:
                    break
        results.append((article, discussed))
    return results


def write_citations(tag, date_str, all_articles, script, debate_summary, output_dir):
    matched = match_articles_to_script(all_articles, script)

    hedged_phrases = [
        "some research suggests", "examples include", "the pattern",
        "some communities", "programs like", "advocates argue", "critics point out",
        "studies suggest", "estimates range",
    ]
    hedged_count = sum(script.lower().count(p) for p in hedged_phrases)

    citations = {
        "episode": {
            "tag": tag,
            "title": f"Deep Dive: {tag.replace('-', ' ').title()}",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "date": date_str,
            "debate_summary": debate_summary,
        },
        "sources": [
            {
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "source_type": a.get("source_type", "user"),
                "summary": a.get("summary", "")[:300],
                "discussed": discussed,
            }
            for a, discussed in matched
        ],
        "fact_check": {
            "notes": "Specific claims are drawn from the sources listed above or explicitly hedged.",
            "hedged_phrases_count": hedged_count,
        },
        "credits": {
            "script_generation": "Claude (Anthropic)",
            "text_to_speech": "OpenAI TTS API",
            "source_expansion": "Brave Search API",
            "license": "CC BY-NC 4.0",
        },
    }

    citations_file = output_dir / f"bespoke_citations_{tag}_{date_str}.json"
    with open(citations_file, "w") as f:
        json.dump(citations, f, indent=2)
    print(f"  Citations → {citations_file.name}")
    return citations_file


def write_show_notes(tag, date_str, all_articles, debate_summary, output_dir):
    user_articles = [a for a in all_articles if a.get("source_type") != "auto"]
    auto_articles = [a for a in all_articles if a.get("source_type") == "auto"]

    title = tag.replace('-', ' ').title()
    lines = [f"# {title}", f"*Generated on {date_str}*", ""]

    if debate_summary and debate_summary.get("central_question"):
        lines += [f"**Central question:** {debate_summary['central_question']}", ""]

    if debate_summary and debate_summary.get("topics_covered"):
        topics = debate_summary["topics_covered"]
        lines += ["**Topics covered:** " + " · ".join(topics), ""]

    ctas = debate_summary.get("calls_to_action", []) if debate_summary else []
    if ctas:
        lines += ["## Calls to Action", ""]
        for cta in ctas:
            lines.append(f"- {cta}")
        lines.append("")

    lines += ["## Sources", ""]

    if user_articles:
        lines.append("### Curated by host:")
        for a in user_articles:
            title_text = a.get("title") or a.get("url", "Untitled")
            url = a.get("url", "")
            lines.append(f"- [{title_text}]({url})" if url else f"- {title_text}")
        lines.append("")

    if auto_articles:
        lines.append("### Additional sources consulted:")
        for a in auto_articles:
            title_text = a.get("title") or a.get("url", "Untitled")
            url = a.get("url", "")
            lines.append(f"- [{title_text}]({url}) *(auto-expanded)*" if url else f"- {title_text}")
        lines.append("")

    lines += [
        "---",
        "*All specific claims cited in this episode are drawn from the sources above, "
        "or are explicitly hedged as unverified.*",
        "*Generated by Claude (Anthropic) · Audio by OpenAI TTS · Sources via Brave Search*",
    ]

    shownotes_file = output_dir / f"bespoke_shownotes_{tag}_{date_str}.md"
    with open(shownotes_file, "w") as f:
        f.write("\n".join(lines))
    print(f"  Show notes → {shownotes_file.name}")
    return shownotes_file


# ── RSS feed ──────────────────────────────────────────────────────────────

BESPOKE_FEED_FILE = SCRIPT_DIR / "bespoke-feed.xml"

BESPOKE_FEED_CONFIG = {
    "title": "Cariboo Signals: Deep Dives",
    "description": (
        "Long-form debate episodes on weighty topics — politics, philosophy, economics, technology. "
        "Hosted by Riley (tech optimist & empiricist) and Casey (community skeptic & systems thinker), "
        "broadcasting from the Cariboo region of BC. "
        "Each episode is built from curated sources and auto-expanded with credible coverage."
    ),
    "author": "Riley and Casey",
    "language": "en-us",
    "explicit": False,
    "categories": ["Society & Culture", "Technology"],
}


def _get_audio_duration(filepath):
    try:
        audio = AudioSegment.from_mp3(str(filepath))
        total_secs = len(audio) // 1000
        return f"{total_secs // 60}:{total_secs % 60:02d}"
    except Exception:
        return "40:00"


def generate_bespoke_rss_feed(base_url):
    """Scan podcasts/bespoke/ and write bespoke-feed.xml."""
    print("Generating bespoke RSS feed...")

    audio_files = sorted(
        glob.glob(str(BESPOKE_DIR / "bespoke_audio_*.mp3")),
        reverse=True,
    )

    episodes = []
    for audio_file in audio_files:
        basename = os.path.basename(audio_file)
        m = re.search(r'bespoke_audio_(.+?)_(\d{4}-\d{2}-\d{2})\.mp3', basename)
        if not m:
            continue
        tag, date_str = m.group(1), m.group(2)

        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            pub_date = date_obj.strftime("%a, %d %b %Y 12:00:00 GMT")
        except ValueError:
            continue

        citations_file = BESPOKE_DIR / f"bespoke_citations_{tag}_{date_str}.json"
        central_question = ""
        topics = []
        ctas = []
        sources_html = ""
        if citations_file.exists():
            try:
                with open(citations_file) as f:
                    cdata = json.load(f)
                summary = cdata.get("episode", {}).get("debate_summary", {})
                central_question = summary.get("central_question", "")
                topics = summary.get("topics_covered", [])
                ctas = summary.get("calls_to_action", [])
                user_srcs = [s for s in cdata.get("sources", []) if s.get("source_type") == "user" and s.get("url")]
                auto_srcs = [s for s in cdata.get("sources", []) if s.get("source_type") == "auto" and s.get("url")]
                if user_srcs:
                    sources_html += "<p><strong>Curated sources:</strong><br/>"
                    sources_html += "<br/>".join(
                        f'<a href="{saxutils.escape(s["url"])}">{saxutils.escape(s.get("title","") or s["url"])}</a>'
                        for s in user_srcs
                    ) + "</p>"
                if auto_srcs:
                    sources_html += "<p><strong>Additional sources consulted:</strong><br/>"
                    sources_html += "<br/>".join(
                        f'<a href="{saxutils.escape(s["url"])}">{saxutils.escape(s.get("title","") or s["url"])}</a>'
                        for s in auto_srcs
                    ) + "</p>"
            except Exception:
                pass

        title = tag.replace("-", " ").replace("_", " ").title()
        if central_question:
            title += f": {central_question}"

        description = f"<p><strong>Topic:</strong> {saxutils.escape(tag.replace('-', ' ').replace('_', ' ').title())}</p>"
        if central_question:
            description += f"<p><strong>Central question:</strong> {saxutils.escape(central_question)}</p>"
        if topics:
            description += f"<p><strong>Topics:</strong> {saxutils.escape(', '.join(topics))}</p>"
        if ctas:
            description += "<p><strong>Calls to action:</strong><br/>"
            description += "<br/>".join(f"• {saxutils.escape(c)}" for c in ctas)
            description += "</p>"
        description += sources_html
        description += (
            "<p><em>Generated by Claude (Anthropic) · Audio by OpenAI TTS · "
            "Sources via Brave Search · CC BY-NC 4.0</em></p>"
        )

        episodes.append({
            "title": title,
            "tag": tag,
            "date_str": date_str,
            "pub_date": pub_date,
            "audio_url": f"{base_url}podcasts/bespoke/{basename}",
            "file_size": os.path.getsize(audio_file),
            "duration": _get_audio_duration(audio_file),
            "description": description,
            "guid": f"cariboo-signals-deep-dive-{tag}-{date_str}",
        })

    cfg = BESPOKE_FEED_CONFIG
    feed_url = f"{base_url}bespoke-feed.xml"
    cover_url = f"{base_url}Deepdives.png"
    now_rfc = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">',
        "<channel>",
        f"<title>{saxutils.escape(cfg['title'])}</title>",
        f"<link>{saxutils.escape(base_url)}</link>",
        f"<language>{cfg['language']}</language>",
        f"<description>{saxutils.escape(cfg['description'])}</description>",
        f"<itunes:author>{saxutils.escape(cfg['author'])}</itunes:author>",
        f"<itunes:summary>{saxutils.escape(cfg['description'])}</itunes:summary>",
        f'<itunes:image href="{saxutils.escape(cover_url)}"/>',
        f'<atom:link href="{saxutils.escape(feed_url)}" rel="self" type="application/rss+xml" xmlns:atom="http://www.w3.org/2005/Atom"/>',
        f"<itunes:explicit>{'true' if cfg['explicit'] else 'false'}</itunes:explicit>",
        "<itunes:type>episodic</itunes:type>",
    ]
    for cat in cfg["categories"]:
        lines.append(f'<itunes:category text="{saxutils.escape(cat)}"/>')
    lines.append(f"<lastBuildDate>{now_rfc}</lastBuildDate>")

    for ep in episodes[:20]:
        lines += [
            "<item>",
            f"<title>{saxutils.escape(ep['title'][:255])}</title>",
            f"<pubDate>{ep['pub_date']}</pubDate>",
            f"<description><![CDATA[{ep['description']}]]></description>",
            f"<itunes:summary><![CDATA[{ep['description']}]]></itunes:summary>",
            f'<enclosure url="{saxutils.escape(ep["audio_url"])}" length="{ep["file_size"]}" type="audio/mpeg"/>',
            f"<guid isPermaLink=\"false\">{saxutils.escape(ep['guid'])}</guid>",
            f"<itunes:duration>{ep['duration']}</itunes:duration>",
            f"<itunes:explicit>{'true' if cfg['explicit'] else 'false'}</itunes:explicit>",
            "</item>",
        ]

    lines += ["</channel>", "</rss>"]

    with open(BESPOKE_FEED_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"  Feed → {BESPOKE_FEED_FILE.name} ({len(episodes)} episode(s))")
    return BESPOKE_FEED_FILE


# ── R2 upload ─────────────────────────────────────────────────────────────

def _get_r2_client():
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


def _upload_file_to_r2(r2, bucket, local_path, object_key):
    try:
        r2.upload_file(str(local_path), bucket, object_key)
        print(f"   R2: {object_key}")
        return True
    except Exception as e:
        print(f"   R2 upload failed for {object_key}: {e}")
        return False


def sync_bespoke_to_r2(tag, date_str):
    """Upload the just-generated bespoke episode + updated feed to R2."""
    r2, bucket = _get_r2_client()
    if r2 is None:
        print("R2 credentials not configured, skipping upload")
        return

    print("Syncing bespoke episode to R2...")

    # Feed file, landing page, and cover image
    for filename in ("bespoke-feed.xml", "bespoke.html", "Deepdives.png"):
        p = SCRIPT_DIR / filename
        if p.exists():
            _upload_file_to_r2(r2, bucket, p, filename)

    # Theme song assets (full song + the three derived clips)
    for filename in (
        "string-theory-kickoff.mp3",
        "bespoke-theme-intro.mp3",
        "bespoke-theme-outro.mp3",
        "bespoke-theme-interval.mp3",
    ):
        p = SCRIPT_DIR / filename
        if p.exists():
            _upload_file_to_r2(r2, bucket, p, filename)

    # Episode files for this run
    safe_tag = tag.replace(" ", "-").lower()
    patterns = [
        f"bespoke_audio_{safe_tag}_{date_str}.mp3",
        f"bespoke_citations_{safe_tag}_{date_str}.json",
        f"bespoke_shownotes_{safe_tag}_{date_str}.md",
        f"bespoke_script_{safe_tag}_{date_str}.txt",
    ]
    for filename in patterns:
        local_path = BESPOKE_DIR / filename
        if local_path.exists():
            _upload_file_to_r2(r2, bucket, local_path, f"podcasts/bespoke/{filename}")


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate a bespoke podcast episode from tagged seeds."
    )
    parser.add_argument("--tag", required=False, help="Topic tag to generate episode for")
    parser.add_argument("--threshold", type=int, default=3,
                        help="Minimum seeds required (default: 3)")
    parser.add_argument("--sync-only", action="store_true",
                        help="Regenerate feed and upload to R2 without generating a new episode")
    args = parser.parse_args()

    base_url = os.getenv("PODCAST_BASE_URL", "https://podcast.cariboosignals.ca/")

    if args.sync_only:
        generate_bespoke_rss_feed(base_url)
        r2, bucket = _get_r2_client()
        if r2:
            for filename in ("bespoke-feed.xml", "bespoke.html", "Deepdives.png"):
                p = SCRIPT_DIR / filename
                if p.exists():
                    _upload_file_to_r2(r2, bucket, p, filename)
            for mp3 in sorted(BESPOKE_DIR.glob("bespoke_audio_*.mp3")):
                _upload_file_to_r2(r2, bucket, mp3, f"podcasts/bespoke/{mp3.name}")
            for f in sorted(BESPOKE_DIR.glob("bespoke_citations_*.json")):
                _upload_file_to_r2(r2, bucket, f, f"podcasts/bespoke/{f.name}")
            for f in sorted(BESPOKE_DIR.glob("bespoke_shownotes_*.md")):
                _upload_file_to_r2(r2, bucket, f, f"podcasts/bespoke/{f.name}")
        return

    if not args.tag:
        parser.error("--tag is required unless --sync-only is set")

    tag = args.tag.lower()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    BESPOKE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*50}")
    print(f"  Bespoke Episode: {tag}")
    print(f"  Date: {date_str}")
    print(f"{'='*50}\n")

    hosts = load_bespoke_hosts()
    config = load_bespoke_config()

    # Load seeds
    seeds = load_seeds_for_tag(tag)
    if len(seeds) < args.threshold:
        print(f"Only {len(seeds)} seed(s) found for tag '{tag}' (need {args.threshold}). Exiting.")
        sys.exit(1)
    print(f"Loaded {len(seeds)} seed(s) for tag '{tag}'")

    # Init API client
    client = get_anthropic_client()
    if not client:
        print("ANTHROPIC_API_KEY not set. Exiting.")
        sys.exit(1)

    # Expand the tag slug into plain-English topic description before anything else
    print("\nExpanding tag into topic description...")
    tag_description = expand_tag(tag, client)

    # Fetch content for user seeds
    print("\nFetching article content...")
    user_articles = []
    for seed in seeds:
        if seed.get("type") == "url":
            url = seed["url"]
            print(f"  {url[:70]}")
            title, desc, body = fetch_url_content(url)
            summary = seed.get("note", "")
            if summary and desc:
                summary = f"{summary}  —  {desc}"
            elif not summary:
                summary = desc
            user_articles.append({
                "title": title or url,
                "url": url,
                "summary": summary,
                "body": body,
                "source_type": "user",
                "seed_id": seed["id"],
            })
        elif seed.get("type") == "thought":
            user_articles.append({
                "title": f"Exploration prompt: {seed['content'][:80]}",
                "url": "",
                "summary": seed.get("content", ""),
                "source_type": "user",
                "seed_id": seed["id"],
            })

    # Expand sources
    print("\nExpanding sources via Brave Search...")
    auto_articles = expand_sources(tag, user_articles, client, config, tag_description=tag_description)
    all_articles = user_articles + auto_articles
    print(f"Total sources: {len(all_articles)} ({len(user_articles)} user, {len(auto_articles)} auto-expanded)")

    # Load memory
    past_debates = load_bespoke_memory(tag)
    if past_debates:
        print(f"\nMemory: {len(past_debates)} past debate(s) on this tag")

    # Generate script
    print("\nGenerating script...")
    script = generate_bespoke_script(tag, all_articles, past_debates, client, tag_description=tag_description)
    word_count = len(script.split())
    turn_count = script.count("**RILEY:**") + script.count("**CASEY:**")
    print(f"  Draft: {word_count} words, {turn_count} turns")

    # Polish
    script = polish_bespoke_script(script, tag, client)

    # Fact-check
    script = fact_check_bespoke_script(script, all_articles, client)

    # Write script file
    script_file = BESPOKE_DIR / f"bespoke_script_{tag}_{date_str}.txt"
    with open(script_file, "w") as f:
        f.write(script)
    print(f"\nScript → {script_file.name}")

    # Extract debate summary for memory
    print("\nExtracting debate summary...")
    debate_summary = extract_debate_summary(script, tag, client)
    save_bespoke_memory(tag, debate_summary)
    if debate_summary.get("central_question"):
        print(f"  Central question: {debate_summary['central_question']}")

    # Generate audio
    print("\nGenerating audio...")
    audio_file = BESPOKE_DIR / f"bespoke_audio_{tag}_{date_str}.mp3"
    generate_audio(script, audio_file, hosts, config)

    # Write citations + show notes
    print("\nWriting citations and show notes...")
    write_citations(tag, date_str, all_articles, script, debate_summary, BESPOKE_DIR)
    write_show_notes(tag, date_str, all_articles, debate_summary, BESPOKE_DIR)

    # Mark seeds used
    mark_seeds_used(tag, seeds)

    # Update bespoke RSS feed
    generate_bespoke_rss_feed(base_url)

    # Upload to Cloudflare R2
    sync_bespoke_to_r2(tag, date_str)

    print(f"\n{'='*50}")
    print(f"  Episode complete: {tag}")
    print(f"  Output: podcasts/bespoke/")
    print(f"  Feed:   bespoke-feed.xml")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
