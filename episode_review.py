#!/usr/bin/env python3
"""Daily editorial review of the episode-generation run, published to the site.

Runs at the end of the day's last scheduled trigger (see the `review` job in
daily-podcast.yml). The concurrency group serializes the three crons, so by then
the earlier triggers have exited — which matters, because a run that never
started is often the day's story. On 2026-08-19 two of the three triggers were
cancelled while pending and the survivor waited 2h12m for a runner; none of that
is visible from inside the run that made the episode.

So the facts come from two places: the Actions API for what happened *to* the
runs, and the generating run's job log for what happened *inside* one. Claude
turns the merged facts into prose; the numbers table is templated here, because
a model that can restate a number can also restate it wrong.

    python episode_review.py                          # today, Pacific
    python episode_review.py --date 2026-08-19
    python episode_review.py --log run.log --no-llm   # parse a saved log, no spend

Writes podcasts/reviews/episode-review-YYYY-MM-DD.html and regenerates
episode-reviews.xml, which the site deploys and super-rss-feed subscribes to
like any other source.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import requests

from config_loader import (
    atomic_write_json,
    atomic_write_text,
    format_static_tell_block,
    load_prompts_config,
)

API_ROOT = "https://api.github.com"
REPO = os.getenv("GITHUB_REPOSITORY", "zirnhelt/curated-podcast-generator")
WORKFLOW_FILE = "daily-podcast.yml"
GENERATING_JOB = "generate-podcast"

SITE_URL = os.getenv("SITE_BASE_URL", "https://zirnhelt.github.io/curated-podcast-generator")
REVIEWS_DIR = Path("podcasts/reviews")
INDEX_FILE = REVIEWS_DIR / "index.json"
FEED_FILE = Path("episode-reviews.xml")
FEED_LIMIT = 30

REVIEW_MODEL = os.getenv("CLAUDE_REVIEW_MODEL", "claude-haiku-4-5-20251001")
REVIEW_MAX_TOKENS = 2200

# The three crons, by the hour they are scheduled for. GitHub fires them late —
# 43, 28 and 30 minutes late on 2026-08-19 — so a run is matched to its slot by
# _trigger_label and the drift is kept as a fact rather than smoothed away.
CRON_HOURS = {8: "Primary (1:05 AM Pacific)",
              9: "Fallback 1 (2:05 AM Pacific)",
              10: "Fallback 2 (3:05 AM Pacific)"}


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------

def _session() -> requests.Session:
    s = requests.Session()
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    s.headers["Accept"] = "application/vnd.github+json"
    return s


def fetch_runs(session: requests.Session, date: str) -> list[dict]:
    """Every Daily Podcast Generation run created on `date`, cancelled ones included."""
    url = f"{API_ROOT}/repos/{REPO}/actions/workflows/{WORKFLOW_FILE}/runs"
    resp = session.get(url, params={"created": date, "per_page": 50}, timeout=30)
    resp.raise_for_status()
    runs = resp.json().get("workflow_runs", [])
    return sorted(runs, key=lambda r: r.get("created_at", ""))


def fetch_job_log(session: requests.Session, run_id: int) -> tuple[str, list[dict]]:
    """Return (log text, step records) for a run's generate-podcast job.

    Empty log rather than an exception when the job is missing: a cancelled run
    has no jobs at all, and a review that crashes on the expected case is worse
    than one with a gap in it.
    """
    jobs_url = f"{API_ROOT}/repos/{REPO}/actions/runs/{run_id}/jobs"
    resp = session.get(jobs_url, params={"per_page": 50}, timeout=30)
    resp.raise_for_status()
    jobs = [j for j in resp.json().get("jobs", []) if j.get("name") == GENERATING_JOB]
    if not jobs:
        return "", []

    job = jobs[0]
    steps = [{"name": s.get("name"),
              "conclusion": s.get("conclusion"),
              "seconds": _duration(s.get("started_at"), s.get("completed_at"))}
             for s in job.get("steps", [])]

    log = ""
    try:
        log_resp = session.get(f"{API_ROOT}/repos/{REPO}/actions/jobs/{job['id']}/logs",
                               timeout=60)
        log_resp.raise_for_status()
        log = log_resp.text
    except requests.RequestException as exc:
        print(f"  ⚠️  Could not read job log ({exc}) — reviewing from run metadata alone")
    return log, steps


def _duration(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    return (_parse(end) - _parse(start)).total_seconds()


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# Run metadata → facts
# ---------------------------------------------------------------------------

def _trigger_label(created: datetime) -> tuple[str, int]:
    """Which cron a run came from, and how many minutes late it fired.

    The latest slot at or before the run, not the nearest one: a cron fires at
    or after its scheduled minute and never before it. 08:48 is 43 minutes late
    for the 08:05 primary, and "nearest" reads it as the 09:05 fallback firing
    17 minutes early — which cannot happen.
    """
    minutes = created.hour * 60 + created.minute
    slots = sorted(CRON_HOURS)
    hour = next((h for h in reversed(slots) if minutes >= h * 60 + 5), slots[0])
    return CRON_HOURS[hour], max(0, minutes - (hour * 60 + 5))


def summarize_runs(runs: list[dict]) -> list[dict]:
    out = []
    for r in runs:
        created = _parse(r["created_at"])
        label, late = _trigger_label(created)
        jobs_started = r.get("run_started_at")
        out.append({
            "run_id": r["id"],
            "trigger": label if r.get("event") == "schedule" else "Manual (workflow_dispatch)",
            "minutes_late": late,
            "created_at": r["created_at"],
            "updated_at": r.get("updated_at"),
            "status": r.get("status"),
            "conclusion": r.get("conclusion"),
            "url": r.get("html_url"),
            "run_started_at": jobs_started,
        })
    return out


# ---------------------------------------------------------------------------
# Log → facts
# ---------------------------------------------------------------------------

# Single-value extractions: key → (pattern, [casts per group]).
_SINGLE: dict[str, tuple[str, list[type]]] = {
    "theme_configured": (r"📅 \w+, [^-]+ - Theme: (.+)", [str]),
    "focus": (r"🎯 Focus \(week (\d+)/(\d+)\): (.+)", [int, int, str]),
    "theme_feed": (r"📌 Feed theme: (.+)", [str]),
    "articles_loaded": (r"✅ Loaded (\d+) articles from podcast feed", [int]),
    "articles_theme": (r"✓ Theme articles: (\d+)", [int]),
    "articles_bonus": (r"✓ Bonus articles: (\d+)", [int]),
    "blocklist_removed": (r"🚫 Blocklist removed (\d+)", [int]),
    "bad_news_removed": (r"🚫 Bad news filter removed (\d+)", [int]),
    "dedup_filtered": (r"✅ Filtered: (\d+) articles", [int]),
    "focus_routing": (r"🔀 Focus routing: released (\d+), held (\d+)", [int, int]),
    "focus_fallback": (r"🎯 focus_fallback: (.+)", [str]),
    "roundup_pool": (r"🧵 Roundup pool: (\d+) stories [^—]*— (.+)", [int, str]),
    "roundup_dropped": (r"dropped (\d+) over budget", [int]),
    "deep_dive_count": (r"Deep dive: selected (\d+) articles", [int]),
    "short_script": (r"Script complete but short \((\d+) words < (\d+) target\)", [int, int]),
    "quality": (r"Total pattern hits: (\d+)\s+\|\s+Voice ratio Casey/Riley: ([\d.]+)\s+\|\s+Words: (\d+)",
                [int, float, int]),
    "citation_alignment": (r"Citation alignment: (\d+)/(\d+) news, (\d+)/(\d+) deep-dive", [int, int, int, int]),
    "anchor": (r"❓ This week's question: (.+)", [str]),
    "debate_question": (r"Debate question: (.+)", [str]),
    "psa": (r"🏘️  Community Spotlight: (.+)", [str]),
    "audio_minutes": (r"Duration: ([\d.]+) minutes", [float]),
    "audio_mb": (r"File size: ([\d.]+) MB", [float]),
    "rss_episodes": (r"Generated RSS feed with (\d+) episodes", [int]),
    "spend": (r"Anthropic input tokens: ([\d,]+) \| API call counts: (.+)", [str, str]),
    "video": (r"Video rendered: \S+ \(([\d.]+) MB, ([\d.]+) min\)", [float, float]),
}

# Repeating extractions: key → pattern. Every match is kept, in order.
_MULTI: dict[str, str] = {
    "clusters": r'🔗 Cluster "([^"]+)": canonical="([^"]*)", suppressed (\d+)',
    "held": r"📥 Held for (\S+) \(([^)]+)\): (.+)",
    "released": r"📤 Released from holding \(held ([^)]+)\): (.+)",
    "deep_dive": r"- \[kw=\d+[^\]]*\] (.+)",
    "degradations": r"##\[warning\]Degraded '([^']+)': (.+)",
    "canary_failures": r"Gemini TTS canary failed on (.+?): (.+)",
    "brave_failures": r"Brave Answers API failed for '([^']+)': (.+)",
}

# Things worth counting rather than listing — nine near-identical warnings are
# one fact, not nine.
_COUNTS: dict[str, str] = {
    "tts_short_segments": r"TTS duration check: expected",
    "tts_retry_failed": r"Retry didn't recover the missing words",
    "brave_enriched": r"🔎 Brave-enriched sparse article",
}


_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_ECHO_COLOR = "\x1b[36;1m"


def _strip_command_echo(text: str) -> str:
    """Drop the runner's echo of each step's own source, then ANSI and timestamps.

    Actions prints every step's script back before running it, in cyan. That
    source contains the *unfired* warning strings — `echo "::warning::Anthropic
    usage limit reached"` sits in the workflow whether or not the budget was
    ever hit. Scanning it hands the model failures that did not happen, so the
    echo is removed before anything else looks at the log.
    """
    kept = [line for line in text.splitlines()
            if _ECHO_COLOR not in line and "##[group]Run " not in line]
    return re.sub(r"^\S*Z ", "", _ANSI.sub("", "\n".join(kept)), flags=re.MULTILINE)


def scan_log(text: str) -> dict[str, Any]:
    """Pull structured facts out of the job log.

    Every extraction is optional. The log is the pipeline's own stdout, which
    moves whenever a print statement does; a missing fact is a gap in one
    paragraph, an exception is no review at all.
    """
    facts: dict[str, Any] = {}
    clean = _strip_command_echo(text)

    for key, (pattern, casts) in _SINGLE.items():
        m = re.search(pattern, clean)
        if not m:
            continue
        values = [cast(g.strip().replace(",", "") if cast in (int, float) else g.strip())
                  for cast, g in zip(casts, m.groups())]
        facts[key] = values[0] if len(values) == 1 else values

    for key, pattern in _MULTI.items():
        matches = [[g.strip() for g in m.groups()] for m in re.finditer(pattern, clean)]
        if matches:
            facts[key] = [m[0] if len(m) == 1 else m for m in matches]

    for key, pattern in _COUNTS.items():
        count = len(re.findall(pattern, clean))
        if count:
            facts[key] = count

    # Warnings the pipeline emitted that no extractor above claimed. These are
    # the unknown unknowns — a new failure mode has no regex until it has
    # happened once, and the model can still write about it.
    known = re.compile("|".join(list(_COUNTS.values()) + [
        r"Gemini TTS canary failed", r"Brave Answers API failed",
        r"Script complete but short"]))
    facts["other_warnings"] = sorted({
        line.strip() for line in clean.splitlines()
        if ("⚠️" in line or "::warning::" in line) and not known.search(line)
    })[:12]

    return facts


# ---------------------------------------------------------------------------
# Facts → labelled facts
# ---------------------------------------------------------------------------

# The extractors keep their groups positional. The numbers table can read that
# — it owns the format strings — and the model cannot: it sees a key and a bare
# list, and guesses. `citation_alignment: [5, 15, 1, 3]` was published as "four
# citation checks returned scores of 5, 15, 1 and 3 out of an apparent ceiling
# of 10 or 100" (2026-08-21); `video: [55.4, 19.5]` (MB, minutes) became a
# 55-minute video against a 19.5-minute audio mix, reported in the review's own
# voice as a discrepancy worth investigating (2026-08-20). Every group gets a
# name and the name carries the unit.
_FIELDS: dict[str, list[str]] = {
    "focus": ["cycle_week", "cycle_length", "focus_name"],
    "focus_routing": ["articles_released", "articles_held"],
    "roundup_pool": ["stories_kept", "composition"],
    "short_script": ["first_draft_words", "target_words"],
    "quality": ["ai_tell_pattern_hits", "voice_ratio_casey_over_riley", "script_words"],
    "citation_alignment": ["roundup_citations_matched", "roundup_citations_total",
                           "deep_dive_citations_matched", "deep_dive_citations_total"],
    "spend": ["anthropic_input_tokens", "api_calls_by_service"],
    "video": ["video_size_mb", "video_minutes"],
    "clusters": ["cluster_key", "canonical_title", "duplicates_suppressed"],
    "held": ["release_date", "target_focus", "article_title"],
    "released": ["held_since", "article_title"],
    "degradations": ["segment", "reason"],
    "canary_failures": ["model", "error"],
    "brave_failures": ["query", "error"],
}

# What a count means when the number alone does not say it. The review invented
# the consequence three days running — eight retries that stayed short were
# published as "eight TTS segments failed retry and were skipped … these
# segments do not appear in the final audio", which is the opposite of what the
# code does. The behaviour ships with the count.
_MEANS: dict[str, str] = {
    "tts_short_segments": ("TTS takes that came back shorter than their word count predicts. "
                           "Each is retried once."),
    "tts_retry_failed": ("of those retries, the ones still short. The longer of the two takes is "
                         "kept, so nothing is dropped from the episode."),
    "brave_enriched": ("articles whose body was too thin to script from, topped up from Brave "
                       "search results."),
    "short_script": ("the first draft came in under target and was sent back for one expand pass. "
                     "quality.script_words is what shipped."),
}


def label_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """Name every positional group, and say what a bare count means.

    Only for the prompt — `render_numbers_table` reads the positional form and
    is the authority on the numbers either way.
    """
    labelled: dict[str, Any] = {}
    for key, value in facts.items():
        names = _FIELDS.get(key)
        if names and isinstance(value, list) and value:
            if isinstance(value[0], list):
                value = [dict(zip(names, item)) for item in value]
            elif len(value) == len(names):
                value = dict(zip(names, value))
        if key in _MEANS:
            value = {"value": value, "means": _MEANS[key]}
        labelled[key] = value
    return labelled


# ---------------------------------------------------------------------------
# Narrative
# ---------------------------------------------------------------------------

def generate_narrative(facts: dict[str, Any]) -> str:
    """One Claude call: facts in, HTML fragment out. Empty string on failure."""
    import anthropic

    prompts = load_prompts_config()
    template = prompts.get("episode_review", {}).get("template", "")
    if not template:
        print("  ⚠️  No episode_review prompt configured — skipping narrative")
        return ""

    prompt = template.format(
        facts_json=json.dumps(label_facts(facts), indent=2, ensure_ascii=False),
        tell_block=format_static_tell_block(),
    )
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    try:
        resp = client.messages.create(
            model=REVIEW_MODEL,
            max_tokens=REVIEW_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        print(f"  ⚠️  Narrative generation failed ({exc}) — publishing the facts table alone")
        return ""

    usage = getattr(resp, "usage", None)
    if usage:
        print(f"  [api] service=claude model={REVIEW_MODEL} "
              f"input_tokens={usage.input_tokens} output_tokens={usage.output_tokens}")
    body = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    return _scrub_html(re.sub(r"^```(?:html)?\s*|\s*```$", "", body))


_UNSAFE_BLOCK = re.compile(r"<(script|style|iframe|object|embed)\b.*?</\1>",
                           re.IGNORECASE | re.DOTALL)
_UNSAFE_TAG = re.compile(r"</?(script|style|iframe|object|embed)\b[^>]*>", re.IGNORECASE)
_EVENT_ATTR = re.compile(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)


def _scrub_html(fragment: str) -> str:
    """Strip anything executable out of the narrative before it is published.

    The prompt asks for a plain fragment and the model obliges, but this page
    goes on the public site and its facts are partly third-party article
    titles. Cheaper to make the bad case impossible than to rely on it staying
    unlikely.
    """
    fragment = _UNSAFE_BLOCK.sub("", fragment)
    fragment = _UNSAFE_TAG.sub("", fragment)
    return _EVENT_ATTR.sub("", fragment)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_run_table(runs: list[dict]) -> str:
    if not runs:
        return ""
    rows = []
    for r in runs:
        late = f"{r['minutes_late']:+d} min" if r.get("minutes_late") is not None else "—"
        rows.append(
            f"<tr><td>{escape(r['trigger'])}</td><td>{escape(r['created_at'])}</td>"
            f"<td>{late}</td><td>{escape(r.get('conclusion') or r.get('status') or '—')}</td></tr>")
    return ("<h3>The day's triggers</h3>\n<table>\n"
            "<thead><tr><th>Trigger</th><th>Created</th><th>Drift</th><th>Outcome</th></tr></thead>\n"
            f"<tbody>\n{chr(10).join(rows)}\n</tbody>\n</table>")


def render_numbers_table(facts: dict[str, Any]) -> str:
    """Templated locally on purpose — a model that can restate a number can
    also restate it wrong, and these are the load-bearing ones."""
    def row(label: str, key: str, fmt: str) -> tuple[str, str] | None:
        value = facts.get(key)
        if value is None:
            return None
        return label, fmt.format(*(value if isinstance(value, list) else [value]))

    rows = [
        row("Articles loaded", "articles_loaded", "{}"),
        row("Kept after dedup", "dedup_filtered", "{}"),
        row("Held / released", "focus_routing", "{} released, {} held"),
        row("Roundup pool", "roundup_pool", "{} stories — {}"),
        row("Deep dive", "deep_dive_count", "{} articles"),
        row("Script", "quality", "{} pattern hit(s), voice ratio {}, {} words"),
        row("Audio", "audio_minutes", "{} min"),
        row("Audio size", "audio_mb", "{} MB"),
        row("Citations matched", "citation_alignment", "{}/{} roundup, {}/{} deep dive"),
        row("Feed", "rss_episodes", "{} episodes"),
        row("Anthropic spend", "spend", "{} input tokens, calls {}"),
    ]
    body = "\n".join(f"<tr><td>{label}</td><td>{escape(str(value))}</td></tr>"
                     for label, value in filter(None, rows))
    if not body:
        return ""
    return ("<h3>What shipped</h3>\n<table>\n"
            "<thead><tr><th>Measure</th><th>Value</th></tr></thead>\n"
            f"<tbody>\n{body}\n</tbody>\n</table>")


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 800px; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; color: #222; }}
    h1 {{ border-bottom: 2px solid #333; padding-bottom: 0.5rem; font-size: 1.6rem; }}
    h3 {{ margin-top: 1.5rem; font-size: 1.1rem; color: #444; }}
    table {{ border-collapse: collapse; width: 100%; margin: 0.5rem 0; }}
    th, td {{ border: 1px solid #ddd; padding: 0.4rem 0.8rem; text-align: left; }}
    th {{ background: #f5f5f5; }}
    .meta {{ color: #666; font-size: 0.9rem; margin-bottom: 1.5rem; }}
    a {{ color: #0066cc; }}
    code {{ background: #f5f5f5; padding: 0.1rem 0.3rem; border-radius: 3px; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p class="meta">Published {published} &middot; <a href="{site}">Cariboo Signals</a></p>
  {content}
</body>
</html>
"""


def build_review(date: str, runs: list[dict], facts: dict[str, Any], narrative: str) -> tuple[str, str, str]:
    """Return (title, content_html, page_html)."""
    theme = facts.get("theme_feed") or facts.get("theme_configured") or ""
    title = f"Episode Review — Cariboo Signals, {_human_date(date)}"
    generated = next((r for r in runs if r.get("conclusion") == "success"), None)

    parts = ["What the pipeline did, what broke, and what it chose when something broke."]
    if theme:
        parts.append(f"Theme: {escape(theme)}.")
    if generated and generated.get("url"):
        parts.append(f'Run <a href="{escape(generated["url"])}">{generated["run_id"]}</a>.')
    lede = f"<p><em>{' '.join(parts)}</em></p>"

    content = "\n\n".join(part for part in [
        lede, narrative, render_run_table(runs), render_numbers_table(facts)] if part)
    page = PAGE.format(title=escape(title), published=_human_date(date),
                       site=SITE_URL, content=content)
    return title, content, page


def _human_date(date: str) -> str:
    return datetime.strptime(date, "%Y-%m-%d").strftime("%B %-d, %Y")


# ---------------------------------------------------------------------------
# Index + RSS
# ---------------------------------------------------------------------------

def update_index(date: str, title: str, content: str) -> list[dict]:
    """Record this review in the index, replacing any entry for the same date."""
    index = []
    if INDEX_FILE.exists():
        try:
            index = json.loads(INDEX_FILE.read_text("utf-8"))
        except json.JSONDecodeError:
            print("  ⚠️  reviews index unreadable — rebuilding from this review alone")
    index = [e for e in index if e.get("date") != date]
    index.append({
        "date": date,
        "title": title,
        "url": f"{SITE_URL}/podcasts/reviews/episode-review-{date}.html",
        "content_html": content,
        "published": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    index.sort(key=lambda e: e["date"], reverse=True)
    atomic_write_json(INDEX_FILE, index[:FEED_LIMIT], ensure_ascii=False)
    return index[:FEED_LIMIT]


def build_feed(index: list[dict]) -> str:
    items = []
    for entry in index:
        pub = _parse(entry["published"]) if entry.get("published") else _parse(entry["date"] + "T12:00:00+00:00")
        items.append(f"""    <item>
      <title>{escape(entry['title'])}</title>
      <link>{escape(entry['url'])}</link>
      <guid isPermaLink="true">{escape(entry['url'])}</guid>
      <pubDate>{format_datetime(pub)}</pubDate>
      <description>{escape(entry['content_html'])}</description>
    </item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Cariboo Signals — Episode Reviews</title>
    <link>{SITE_URL}</link>
    <description>A daily editorial review of the Cariboo Signals generation run: what it produced, what failed, and what it chose instead.</description>
    <language>en-ca</language>
    <lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>
{chr(10).join(items)}
  </channel>
</rss>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Episode date (YYYY-MM-DD). Defaults to today, Pacific.")
    parser.add_argument("--log", help="Read the job log from a file instead of the API.")
    parser.add_argument("--no-llm", action="store_true", help="Skip the narrative call.")
    parser.add_argument("--dry-run", action="store_true", help="Print the facts, write nothing.")
    args = parser.parse_args()

    date = args.date or (datetime.now(timezone.utc) - timedelta(hours=7)).strftime("%Y-%m-%d")
    print(f"📝 Reviewing the {date} generation run")

    runs: list[dict] = []
    slow_steps: list[dict] = []
    log = ""
    if args.log:
        log = Path(args.log).read_text("utf-8", errors="replace")
        print(f"  📄 Log from {args.log} ({len(log.splitlines())} lines)")
    else:
        session = _session()
        try:
            raw_runs = fetch_runs(session, date)
            runs = summarize_runs(raw_runs)
            print(f"  🔁 {len(runs)} trigger(s): "
                  + ", ".join(f"{r['trigger'].split()[0]}={r.get('conclusion') or r['status']}" for r in runs))
            generated = next((r for r in runs if r.get("conclusion") == "success"), None)
            if generated:
                log, steps = fetch_job_log(session, generated["run_id"])
                # Only the steps worth a sentence: sub-minute ones are setup.
                slow_steps = [s for s in steps if (s.get("seconds") or 0) >= 60]
        except requests.RequestException as exc:
            print(f"  ⚠️  Actions API unreachable ({exc}) — reviewing from the log alone")

    facts = scan_log(log) if log else {}
    facts["date"] = date
    facts["runs"] = runs
    if slow_steps:
        facts["slow_steps"] = slow_steps
    print(f"  🔍 {len(facts)} fact group(s) extracted")

    if args.dry_run:
        print(json.dumps(facts, indent=2, ensure_ascii=False))
        return 0

    if not facts.get("runs") and not log:
        print("❌ Nothing to review — no runs and no log.")
        return 1

    narrative = "" if args.no_llm else generate_narrative(facts)
    title, content, page = build_review(date, runs, facts, narrative)

    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    page_path = REVIEWS_DIR / f"episode-review-{date}.html"
    atomic_write_text(page_path, page)
    index = update_index(date, title, content)
    atomic_write_text(FEED_FILE, build_feed(index))

    print(f"✅ {page_path}")
    print(f"✅ {FEED_FILE} ({len(index)} review(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
