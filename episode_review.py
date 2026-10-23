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

Then the day's findings are folded into ROADMAP.md, because a review nobody
distils is a review nobody acts on — see the roadmap section below.

    python episode_review.py                          # today, Pacific
    python episode_review.py --date 2026-08-19
    python episode_review.py --log run.log --no-llm   # parse a saved log, no spend
    python episode_review.py --no-roadmap             # publish only, leave the roadmap

Writes podcasts/reviews/episode-review-YYYY-MM-DD.html, regenerates
episode-reviews.xml — which the site deploys and super-rss-feed subscribes to
like any other source — and updates ROADMAP.md plus podcasts/roadmap_ledger.json.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import requests

from config_loader import (
    HAIKU_MODEL,
    atomic_write_json,
    atomic_write_text,
    format_static_tell_block,
    json_output_config,
    load_prompts_config,
    message_text,
    thinking_off,
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

REVIEW_MODEL = os.getenv("CLAUDE_REVIEW_MODEL") or HAIKU_MODEL
REVIEW_MAX_TOKENS = 2200

# Where the day's runs come from, by UTC hour (BC is permanent UTC-7). The
# ladder arrives as workflow_dispatch from the Cloudflare Worker, which fires on
# the minute; the one GitHub cron left is the 4:05 AM backstop, and GitHub fires
# that late — so its drift is kept as a fact rather than smoothed away. Until
# 2026-09-23 this still mapped the retired three-cron ladder: the backstop read
# as "Fallback 2, 291 minutes late" and the Worker's rungs as manual runs, and
# the roadmap carried both as a problem for three weeks.
LADDER_HOURS = {8: "Primary (1:05 AM Pacific)",
                9: "Fallback 1 (2:05 AM Pacific)",
                10: "Fallback 2 (3:05 AM Pacific)"}
BACKSTOP_HOUR, BACKSTOP_LABEL = 11, "Backstop (4:05 AM Pacific) — GitHub cron"
# A dispatch this soon after a rung is the Worker's; later is a person.
DISPATCH_WINDOW_MINUTES = 10
# Every clock time the review shows, to the reader or the model, is Pacific.
# The API's raw "...T10:05:35Z" went to the model as-is, and it read 10:05 UTC
# as "10:05 AM Pacific, 34 minutes after its scheduled window" (2026-09-28).
PACIFIC = timezone(timedelta(hours=-7), "PT")


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


def _pacific(ts: str | None) -> str | None:
    """An API timestamp as a Pacific clock time, e.g. "1:05 AM Pacific"."""
    if not ts:
        return None
    return _parse(ts).astimezone(PACIFIC).strftime("%-I:%M %p Pacific")


# ---------------------------------------------------------------------------
# Run metadata → facts
# ---------------------------------------------------------------------------

def _trigger_label(created: datetime, event: str) -> tuple[str, int | None]:
    """Which trigger a run came from, and how many minutes late it fired.

    A cron fires at or after its scheduled minute and never before it, so the
    backstop's drift is measured from 11:05 and floored at zero. A dispatch is
    a ladder rung only inside DISPATCH_WINDOW_MINUTES of one; anything else is
    someone running it by hand, and has no schedule to be late for.
    """
    minutes = created.hour * 60 + created.minute
    if event == "schedule":
        return BACKSTOP_LABEL, max(0, minutes - (BACKSTOP_HOUR * 60 + 5))
    for hour, label in LADDER_HOURS.items():
        late = minutes - (hour * 60 + 5)
        if 0 <= late <= DISPATCH_WINDOW_MINUTES:
            return f"{label} — Cloudflare scheduler", late
    return "Manual (workflow_dispatch)", None


def summarize_runs(runs: list[dict]) -> list[dict]:
    """One row per run. The run this review is executing inside is marked as
    such: it is always still going when the review reads it, and unmarked it
    was published every night as a trigger that never finished."""
    own_run = os.getenv("GITHUB_RUN_ID", "")
    out = []
    for r in runs:
        created = _parse(r["created_at"])
        label, late = _trigger_label(created, r.get("event", ""))
        is_own = str(r["id"]) == own_run
        out.append({
            "run_id": r["id"],
            "trigger": f"{label} — this review's own run" if is_own else label,
            "minutes_late": late,
            "created": _pacific(r["created_at"]),
            "finished": None if is_own else _pacific(r.get("updated_at")),
            "status": "running this review" if is_own else r.get("status"),
            "conclusion": r.get("conclusion"),
            "url": r.get("html_url"),
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
    "casey_deep_dive_word_pct": (r"Deep dive words Casey: (\d+)%", [int]),
    "citation_alignment": (r"Citation alignment: (\d+)/(\d+) news, (\d+)/(\d+) deep-dive", [int, int, int, int]),
    "anchor": (r"❓ This week's question: (.+)", [str]),
    "debate_question": (r"Debate question: (.+)", [str]),
    "psa": (r"🏘️  Community Spotlight: (.+)", [str]),
    "audio_minutes": (r"Duration: ([\d.]+) minutes", [float]),
    "audio_mb": (r"File size: ([\d.]+) MB", [float]),
    "rss_episodes": (r"Generated RSS feed with (\d+) episodes", [int]),
    "spend": (r"Anthropic input tokens: ([\d,]+) \| API call counts: (.+)", [str, str]),
    "spend_usd": (r"💰 Anthropic est\. \$([\d.]+) at list price", [str]),
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
    # The print carries the payload's keys between the query and the error since
    # 2026-09; without the optional bracket this matched nothing.
    "brave_failures": r"Brave Answers API failed for '([^']+)'(?: \[[^\]]*\])?: (.+)",
}

# One JSON line per pipeline process (`podcast_generator._format_spend_line`).
# Stages are separate processes, so the night's spend is the sum of every line.
_SPEND_LINE = r"💰 Spend by service: (\{.*\})"

_SERVICE_LABELS = {"anthropic": "Anthropic", "brave-search": "Brave Search",
                   "brave-answers": "Brave Answers", "openai-tts": "OpenAI TTS",
                   "kagi-extract": "Kagi Extract", "cohere": "Cohere"}

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


# The runner masks every line of every secret, and the Gemini service-account
# key is multi-line JSON whose first and last lines are a bare `{` and `}`. So
# every brace in the log reads `***`, the spend line never parsed, and the cost
# row went missing from every review from 1835b6f on.
_MASK = re.compile(r"\*\*\*")
_SPEND_LINES = re.compile(r"^.*(?:Spend by service|API call counts).*$", re.MULTILINE)


def _unmask_braces(line: str) -> str:
    """Put a masked `{`/`}` back: an opening one is followed by a key's quote.

    The masker folds a run of closing braces into one `***` (2026-10-21's line
    ended `0.003***` for `0.003}}`), so the line is closed up to balance.
    """
    line = line.replace("******", "{}")
    line = _MASK.sub(lambda m: "{" if line[m.end():m.end() + 1] in ("\"", "'") else "}", line)
    return line + "}" * max(0, line.count("{") - line.count("}"))


def _sum_spend(clean: str, facts: dict[str, Any]) -> None:
    """Add up every stage's spend line; overrides the single-snapshot `spend` facts."""
    tokens, calls, usd = 0, {}, {}
    seen = False
    for raw in re.findall(_SPEND_LINE, clean):
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        seen = True
        tokens += int(line.get("input_tokens", 0))
        for key, n in (line.get("calls") or {}).items():
            calls[key] = calls.get(key, 0) + n
        for key, v in (line.get("usd") or {}).items():
            usd[key] = usd.get(key, 0.0) + v
    if not seen:
        return
    facts["spend"] = [f"{tokens:,}", str(calls)]
    facts["spend_usd"] = f"{usd.get('anthropic', 0.0):.2f}"
    facts["spend_by_service"] = {k: round(v, 4) for k, v in usd.items()}


def scan_log(text: str) -> dict[str, Any]:
    """Pull structured facts out of the job log.

    Every extraction is optional. The log is the pipeline's own stdout, which
    moves whenever a print statement does; a missing fact is a gap in one
    paragraph, an exception is no review at all.
    """
    facts: dict[str, Any] = {}
    clean = _SPEND_LINES.sub(lambda m: _unmask_braces(m.group()), _strip_command_echo(text))

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

    _sum_spend(clean, facts)

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
    "short_script": ("the first draft came in under target and was sent back for up to two "
                     "expand passes. quality.script_words is what shipped."),
    # Raised as a lost-articles problem on five nights before this was here.
    "roundup_dropped": ("stories cut from the roundup pool by the airtime budget "
                        "(NEWS_ROUNDUP_COUNT). By design: the pool is larger than one segment "
                        "can air, and a cut story can resurface on a better-matched day."),
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
            thinking=thinking_off(REVIEW_MODEL),
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
            f"<tr><td>{escape(r['trigger'])}</td><td>{escape(r['created'])}</td>"
            f"<td>{late}</td><td>{escape(r.get('conclusion') or r.get('status') or '—')}</td></tr>")
    return ("<h3>The day's triggers</h3>\n<table>\n"
            "<thead><tr><th>Trigger</th><th>Created</th><th>Drift</th><th>Outcome</th></tr></thead>\n"
            f"<tbody>\n{chr(10).join(rows)}\n</tbody>\n</table>")


def _format_calls(raw: str) -> str:
    """`{'brave-search': 48, 'claude': 11}` (a dict repr from the log) -> `59 total: brave-search 48 · claude 11`."""
    try:
        counts = ast.literal_eval(raw.strip())
        if not isinstance(counts, dict) or not counts:
            return raw
        parts = sorted(counts.items(), key=lambda kv: -kv[1])
        return f"{sum(counts.values())} total: " + " · ".join(f"{k} {v}" for k, v in parts)
    except (ValueError, SyntaxError, TypeError):
        return raw


def _cost_by_service_row(by_service: dict[str, float] | None) -> tuple[str, str] | None:
    if not by_service:
        return None
    parts = [f"{_SERVICE_LABELS.get(k, k)} ${v:.3f}"
             for k, v in sorted(by_service.items(), key=lambda kv: -kv[1])]
    return "Total cost", f"${sum(by_service.values()):.2f} at list price: " + " · ".join(parts)


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
        row("Deep dive split", "casey_deep_dive_word_pct", "Casey {}% of the words"),
        row("Audio", "audio_minutes", "{} min"),
        row("Audio size", "audio_mb", "{} MB"),
        row("Citations matched", "citation_alignment", "{}/{} roundup, {}/{} deep dive"),
        row("Feed", "rss_episodes", "{} episodes"),
        row("Anthropic input tokens", "spend", "{0}"),
        row("API calls", "spend", "{1}"),
        row("Anthropic cost", "spend_usd", "${} at list price (estimate)"),
        _cost_by_service_row(facts.get("spend_by_service")),
    ]
    rows = [(label, _format_calls(value)) if label == "API calls" else (label, value)
            for label, value in filter(None, rows)]
    body = "\n".join(f"<tr><td>{label}</td><td>{escape(str(value))}</td></tr>"
                     for label, value in rows)
    if not body:
        return ""
    return ("<h3>What shipped</h3>\n<table>\n"
            "<thead><tr><th>Measure</th><th>Value</th></tr></thead>\n"
            f"<tbody>\n{body}\n</tbody>\n</table>")


# ---------------------------------------------------------------------------
# Verdict, actions, charts
# ---------------------------------------------------------------------------
#
# The narrative answered "what happened" in eight sections and never "so what
# do I do": the 2026-10-20 review spent its first heading on a word count it
# got wrong and said "the facts do not say" six times. The top of the page is
# now Python — a verdict, the things to act on (one per signal, the same keys
# the roadmap counts), and charts — and the narrative is the detail under it.
# Charts are inline SVG built here: no script, nothing fetched, and every
# number is drawn from the facts rather than restated by a model.

# Signals that are worth a look but never make the night "needs attention".
_MINOR_SIGNALS = {"ai-tells-shipped", "voice-ratio"}

# What to do about each signal: where to look, in one line. A degrade() row
# gets the generic line with its segment named.
_ACTIONS: dict[str, str] = {
    "run-failed": "Open the failed run's log. A later rung shipping does not make it a non-event: "
                  "it spent a retry window.",
    "short-script": "Read the expand passes in the script stage log. Target and floor are "
                    "<code>TARGET_SCRIPT_WORDS</code> / <code>MIN_SCRIPT_WORDS</code> in "
                    "<code>podcast_generator.py</code>.",
    "citations:roundup": "Open the day's citations JSON and find the roundup stories the script "
                         "paraphrased past matching, or never aired.",
    "citations:deep-dive": "Open the day's citations JSON and check which deep-dive sources the "
                           "script failed to use.",
    "ai-tells-shipped": "Find the phrase in the script. If it recurs, add it to "
                        "<code>config/ai_tells.json</code>.",
    "voice-ratio": "One host carried the episode. Check the turn split in the script.",
}

_STATUS = {  # level → (icon, label); colour comes from the CSS class
    "good": ("✓", "Shipped clean"),
    "minor": ("●", "Shipped, minor notes"),
    "warn": ("▲", "Shipped, needs a look"),
    "bad": ("✕", "No episode"),
}


def verdict(facts: dict[str, Any], signals: dict[str, str]) -> tuple[str, str]:
    """(level, one-line summary) for the banner."""
    runs = facts.get("runs") or []
    shipped = "audio_minutes" in facts or any(r.get("conclusion") == "success" for r in runs)
    if not shipped:
        return "bad", "No run produced an episode today."
    major = [k for k in signals if k not in _MINOR_SIGNALS]
    if major:
        n = len(major)
        return "warn", f"{n} thing{'s' if n != 1 else ''} to look at before tomorrow's run."
    if signals:
        return "minor", "Nothing broke. A couple of quality notes below."
    return "good", "Nothing broke and nothing fell back."


def _ledger_note(signal: str, ledger: dict | None) -> str:
    for item in (ledger or {}).get("items", []):
        if item.get("signal") == signal and item.get("status") in ("open", "pending"):
            n = len(item.get("seen") or [])
            where = "on the roadmap" if item["status"] == "open" else "pending for the roadmap"
            return f" <span class=\"recur\">Recurring: {where}, seen {n} night{'s' if n != 1 else ''}.</span>"
    return ""


def render_actions(signals: dict[str, str], ledger: dict | None = None) -> str:
    if not signals:
        return "<h2>What to do</h2>\n<p>Nothing. The run needs no follow-up.</p>"
    order = sorted(signals, key=lambda k: (k in _MINOR_SIGNALS, k))
    items = []
    for key in order:
        if key.startswith("degraded:"):
            seg = key.split(":", 1)[1]
            todo = (f"<code>{escape(seg)}</code> fell back. Search the run log for "
                    f"<code>Degraded '{escape(seg)}'</code> and decide whether the fallback is "
                    "acceptable or the cause needs fixing.")
        else:
            todo = _ACTIONS.get(key, "Investigate.")
        items.append(f"<li><strong>{todo}</strong><br><span class=\"evidence\">"
                     f"{escape(signals[key])}</span>{_ledger_note(key, ledger)}</li>")
    return "<h2>What to do</h2>\n<ol class=\"actions\">\n" + "\n".join(items) + "\n</ol>"


def _attr(text: str) -> str:
    return escape(text, {'"': "&quot;"})


def _hbar(rows: list[tuple[str, float, str]], *, floor: tuple[float, str] | None = None,
          max_value: float | None = None) -> str:
    """Horizontal bars: (label, value, value text). One hue; the value printed at the bar end.

    HTML rather than SVG: a scaled SVG shrinks its text with it, and on a phone
    the labels came out at 6px.
    """
    rows = [r for r in rows if r[1] is not None and r[1] >= 0]
    if not rows:
        return ""
    top = max_value or max(v for _, v, _ in rows) or 1
    # The longest bar takes 75% of the track; the rest holds its value text.
    scale = 75 / top
    ref = (f'<span class="ref" style="left:{scale * floor[0]:.1f}%"></span>' if floor else "")
    out = [f'<div class="row" title="{_attr(label)}: {_attr(text)}"><span class="lbl">{escape(label)}</span>'
           f'<span class="track">{ref}<span class="bar" style="width:{max(1.0, scale * min(v, top)):.1f}%">'
           f'</span><span class="val">{escape(text)}</span></span></div>'
           for label, v, text in rows]
    if floor:
        out.append(f'<div class="row"><span></span><span class="track"><span class="note" '
                   f'style="margin-left:{scale * floor[0]:.1f}%">┆ {escape(floor[1])}</span></span></div>')
    return '<div class="bars">' + "".join(out) + "</div>"


def _length_chart(facts: dict[str, Any]) -> str:
    """Shipped words against the target, with the first draft marked when it came in short."""
    quality = facts.get("quality")
    if not (isinstance(quality, list) and len(quality) == 3):
        return ""
    shipped = quality[2]
    short = facts.get("short_script")
    draft, target = (short if isinstance(short, list) and len(short) == 2 else (None, None))
    top = max(v for v in (shipped, draft, target) if v) * 1.1
    pct = lambda v: f"{100 * v / top:.1f}%"  # noqa: E731
    marks = ""
    if target:
        marks += (f'<span class="tick" style="left:{pct(target)}" title="Target: {target:,} words"></span>'
                  f'<span class="tag" style="left:{pct(target)}">target {target:,}</span>')
    if draft:
        marks += (f'<span class="dot" style="left:{pct(draft)}" title="First draft: {draft:,} words"></span>'
                  f'<span class="tag up" style="left:{pct(draft)}">first draft {draft:,}</span>')
    return (f'<div class="length"><div class="lbl">Shipped {shipped:,} words</div>'
            f'<div class="track"><span class="bar" style="width:{pct(shipped)}" '
            f'title="Shipped: {shipped:,} words"></span>{marks}</div></div>')


def render_charts(facts: dict[str, Any]) -> str:
    figures: list[tuple[str, str, str]] = []  # (title, caption, svg)

    figures.append(("Script length", "Words the listener heard, against the target. The dot is "
                    "the first draft when it came in short and was sent back to expand.",
                    _length_chart(facts)))

    cites = facts.get("citation_alignment")
    if isinstance(cites, list) and len(cites) == 4:
        rows = [(name, 100 * m / t, f"{m}/{t} ({100 * m / t:.0f}%)")
                for name, m, t in (("Roundup", cites[0], cites[1]), ("Deep dive", cites[2], cites[3])) if t]
        figures.append(("Citations that match the script",
                        "Below the line, the roadmap starts counting it.",
                        _hbar(rows, max_value=100, floor=(100 * CITATION_FLOOR, f"{CITATION_FLOOR:.0%} floor"))))

    funnel = [("Loaded from feed", facts.get("articles_loaded")),
              ("Kept after dedup", facts.get("dedup_filtered")),
              ("Aired in roundup", (facts.get("roundup_pool") or [None])[0]),
              ("Cut for airtime", facts.get("roundup_dropped")),
              ("Deep dive sources", facts.get("deep_dive_count"))]
    figures.append(("Where the stories went", "Cuts for airtime are by design: the pool is "
                    "larger than one segment.",
                    _hbar([(l, v, f"{v}") for l, v in funnel if isinstance(v, int)])))

    steps = facts.get("slow_steps") or []
    figures.append(("Where the time went", "Steps over a minute, in the run that shipped.",
                    _hbar([(s["name"][:28], s["seconds"] / 60, f"{s['seconds'] / 60:.1f} min")
                           for s in steps if s.get("seconds")])))

    by_service = facts.get("spend_by_service") or {}
    if by_service:
        figures.append(("Spend by service", f"${sum(by_service.values()):.2f} at list price.",
                        _hbar([(_SERVICE_LABELS.get(k, k), v, f"${v:.3f}")
                               for k, v in sorted(by_service.items(), key=lambda kv: -kv[1])])))

    blocks = [f'<figure><figcaption><strong>{escape(t)}</strong> {escape(c)}</figcaption>{svg}</figure>'
              for t, c, svg in figures if svg]
    return ("<h2>The night in charts</h2>\n<div class=\"grid\">\n" + "\n".join(blocks) + "\n</div>"
            if blocks else "")


def review_metrics(facts: dict[str, Any]) -> dict[str, float]:
    """The handful of numbers worth a trend line; saved with each index entry."""
    m: dict[str, float] = {}
    if isinstance(facts.get("audio_minutes"), float):
        m["audio_minutes"] = facts["audio_minutes"]
    quality = facts.get("quality")
    if isinstance(quality, list) and len(quality) == 3:
        m["script_words"], m["tell_hits"] = quality[2], quality[0]
    cites = facts.get("citation_alignment")
    if isinstance(cites, list) and len(cites) == 4:
        if cites[1]:
            m["roundup_cite_pct"] = round(100 * cites[0] / cites[1], 1)
        if cites[3]:
            m["deep_dive_cite_pct"] = round(100 * cites[2] / cites[3], 1)
    if facts.get("spend_by_service"):
        m["cost_usd"] = round(sum(facts["spend_by_service"].values()), 3)
    return m


_TRENDS = [("audio_minutes", "Episode length", "{:.1f} min"),
           ("script_words", "Script words", "{:,.0f}"),
           ("roundup_cite_pct", "Roundup citations matched", "{:.0f}%"),
           ("deep_dive_cite_pct", "Deep-dive citations matched", "{:.0f}%"),
           ("tell_hits", "AI-tell hits shipped", "{:.0f}"),
           ("cost_usd", "Cost (list price)", "${:.2f}")]


def _sparkline(points: list[tuple[str, float]], fmt: str) -> str:
    width, height, pad = 260, 56, 6
    values = [v for _, v in points]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    step = (width - 2 * pad) / (len(points) - 1)
    xy = [(pad + i * step, height - pad - (height - 2 * pad) * (v - lo) / span) for i, (_, v) in enumerate(points)]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(xy))
    hits = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" class="hit"><title>{escape(d)}: '
                   f'{escape(fmt.format(v))}</title></circle>' for (x, y), (d, v) in zip(xy, points))
    lx, ly = xy[-1]
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" role="img" aria-label="'
            f'{len(points)} nights, {_attr(fmt.format(lo))} to {_attr(fmt.format(hi))}">'
            f'<path d="{path}" class="line"/><circle cx="{lx:.1f}" cy="{ly:.1f}" r="4" class="today"/>'
            f'{hits}</svg>')


def render_trends(history: list[dict], today: dict[str, float], date: str) -> str:
    """Small multiples over the reviews in the index, today last."""
    nights = sorted(((e["date"], e.get("metrics") or {}) for e in history if e.get("date") != date))
    nights.append((date, today))
    tiles = []
    for key, label, fmt in _TRENDS:
        points = [(d, m[key]) for d, m in nights if isinstance(m.get(key), (int, float))]
        if len(points) < 3:
            continue
        tiles.append(f'<div class="trend"><div class="trend-head"><span>{escape(label)}</span>'
                     f'<strong>{escape(fmt.format(points[-1][1]))}</strong></div>'
                     f'{_sparkline(points, fmt)}<div class="note">{escape(points[0][0][5:])} → '
                     f'{escape(points[-1][0][5:])} · range {escape(fmt.format(min(v for _, v in points)))}'
                     f'–{escape(fmt.format(max(v for _, v in points)))}</div></div>')
    if not tiles:
        return ""
    return ("<h2>Last 30 nights</h2>\n<p class=\"note\">Today is the dot at the right end. "
            "Hover a point for its night.</p>\n<div class=\"trends\">\n" + "\n".join(tiles) + "\n</div>")


def render_kpis(facts: dict[str, Any], runs: list[dict]) -> str:
    tiles: list[tuple[str, str, str]] = []
    if facts.get("audio_minutes") is not None:
        tiles.append(("Episode", f"{facts['audio_minutes']} min", "audio runtime"))
    quality = facts.get("quality")
    if isinstance(quality, list) and len(quality) == 3:
        tiles.append(("Script", f"{quality[2]:,}", "words shipped"))
    cites = facts.get("citation_alignment")
    if isinstance(cites, list) and len(cites) == 4:
        tiles.append(("Citations", f"{cites[0]}/{cites[1]} · {cites[2]}/{cites[3]}", "roundup · deep dive"))
    if facts.get("spend_by_service"):
        tiles.append(("Cost", f"${sum(facts['spend_by_service'].values()):.2f}", "list price, all services"))
    shipped = next((r for r in runs if r.get("conclusion") == "success"), None)
    if shipped and shipped.get("finished"):
        tiles.append(("Done at", shipped["finished"].replace(" Pacific", ""),
                      shipped["trigger"].split(" (")[0].split(" —")[0] + " rung"))
    if not tiles:
        return ""
    return "<div class=\"kpis\">" + "".join(
        f'<div class="kpi"><span>{escape(k)}</span><strong>{escape(v)}</strong><small>{escape(s)}</small></div>'
        for k, v, s in tiles) + "</div>"


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    :root {{ --bg: #fcfcfb; --ink: #0b0b0b; --muted: #52514e; --rule: #e1e0d9; --axis: #c3c2b7;
             --panel: #f4f3ef; --series: #2a78d6; --link: #1c5cab;
             --good: #0ca30c; --minor: #2a78d6; --warn: #fab219; --bad: #d03b3b; color-scheme: light; }}
    @media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
             --bg: #1a1a19; --ink: #f0efec; --muted: #c3c2b7; --rule: #2c2c2a; --axis: #383835;
             --panel: #242423; --series: #3987e5; --link: #86b6ef; color-scheme: dark; }} }}
    :root[data-theme="dark"] {{ --bg: #1a1a19; --ink: #f0efec; --muted: #c3c2b7; --rule: #2c2c2a;
             --axis: #383835; --panel: #242423; --series: #3987e5; --link: #86b6ef; color-scheme: dark; }}
    body {{ font-family: system-ui, sans-serif; max-width: 880px; margin: 2rem auto; padding: 0 16px;
            line-height: 1.55; color: var(--ink); background: var(--bg); }}
    h1 {{ font-size: 1.5rem; margin-bottom: 0.25rem; }}
    h2 {{ font-size: 1.15rem; margin-top: 2.25rem; border-bottom: 1px solid var(--rule); padding-bottom: 0.3rem; }}
    h3 {{ margin-top: 1.4rem; font-size: 1.02rem; }}
    a {{ color: var(--link); }}
    code {{ background: var(--panel); padding: 0.05rem 0.3rem; border-radius: 3px; font-size: 0.9em; }}
    .meta, .note, .evidence {{ color: var(--muted); font-size: 0.88rem; }}
    .verdict {{ display: flex; gap: 0.8rem; align-items: center; padding: 0.8rem 1rem; margin: 1.2rem 0;
                border-radius: 8px; background: var(--panel); border-left: 6px solid var(--lvl); }}
    .verdict .icon {{ font-size: 1.4rem; color: var(--lvl); width: 1.4rem; text-align: center; }}
    .verdict strong {{ display: block; }}
    .lvl-good {{ --lvl: var(--good); }} .lvl-minor {{ --lvl: var(--minor); }}
    .lvl-warn {{ --lvl: var(--warn); }} .lvl-bad {{ --lvl: var(--bad); }}
    .kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; }}
    .kpi {{ background: var(--panel); border-radius: 8px; padding: 0.6rem 0.8rem; }}
    .kpi span, .kpi small {{ display: block; color: var(--muted); font-size: 0.8rem; }}
    .kpi strong {{ font-size: 1.3rem; font-variant-numeric: tabular-nums; }}
    .actions li {{ margin-bottom: 0.7rem; }} .actions strong {{ font-weight: 600; }}
    .recur {{ color: var(--bad); font-size: 0.85rem; }}
    .grid {{ display: grid; gap: 1.2rem; }}
    figure {{ margin: 0; }} figcaption {{ font-size: 0.9rem; color: var(--muted); margin-bottom: 0.3rem; }}
    figcaption strong {{ color: var(--ink); }}
    .bars {{ display: grid; gap: 6px; }}
    .row {{ display: grid; grid-template-columns: minmax(90px, 30%) 1fr; gap: 10px; align-items: center;
            font-size: 0.85rem; }}
    .row .lbl {{ text-align: right; }}
    .track {{ position: relative; display: flex; align-items: center; gap: 6px; min-height: 18px; }}
    .track .bar {{ display: block; height: 16px; background: var(--series); border-radius: 0 4px 4px 0; flex: none; }}
    .track .val {{ font-variant-numeric: tabular-nums; white-space: nowrap; }}
    .track .ref {{ position: absolute; top: -3px; bottom: -3px; border-left: 1px dashed var(--axis); }}
    .length {{ font-size: 0.85rem; }} .length .track {{ margin: 22px 0 22px; }}
    .length .bar {{ height: 18px; }}
    .length .tick {{ position: absolute; top: -4px; bottom: -4px; border-left: 2px solid var(--ink); }}
    .length .dot {{ position: absolute; width: 10px; height: 10px; margin-left: -7px; border-radius: 50%;
                    background: var(--bg); border: 2px solid var(--ink); }}
    .length .tag {{ position: absolute; top: 22px; transform: translateX(-50%); white-space: nowrap;
                    color: var(--muted); font-size: 0.78rem; }}
    .length .tag.up {{ top: -20px; }}
    .trends {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }}
    .trend {{ background: var(--panel); border-radius: 8px; padding: 0.6rem 0.8rem; }}
    .trend-head {{ display: flex; justify-content: space-between; font-size: 0.85rem; color: var(--muted); }}
    .trend-head strong {{ color: var(--ink); font-variant-numeric: tabular-nums; }}
    svg.spark {{ width: 100%; height: 56px; display: block; margin: 0.3rem 0; }}
    svg .line {{ fill: none; stroke: var(--series); stroke-width: 2; stroke-linejoin: round; }}
    svg .today {{ fill: var(--series); stroke: var(--panel); stroke-width: 2; }}
    svg .hit {{ fill: transparent; }} svg .hit:hover {{ fill: var(--series); fill-opacity: 0.25; }}
    table {{ border-collapse: collapse; width: 100%; margin: 0.5rem 0; font-size: 0.92rem; }}
    th, td {{ border-bottom: 1px solid var(--rule); padding: 0.35rem 0.6rem; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 600; }}
    .table-wrap {{ overflow-x: auto; }}
    details {{ margin-top: 2rem; }} summary {{ cursor: pointer; font-weight: 600; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p class="meta">Published {published} &middot; <a href="{site}">Cariboo Signals</a></p>
  {content}
</body>
</html>
"""


def build_review(date: str, runs: list[dict], facts: dict[str, Any], narrative: str,
                 history: list[dict] | None = None,
                 ledger: dict | None = None) -> tuple[str, str, str]:
    """Return (title, content_html, page_html).

    content_html goes into the RSS feed, which super-rss-feed reads as text, so
    it carries the verdict, the actions and the prose but no charts.
    """
    theme = facts.get("theme_feed") or facts.get("theme_configured") or ""
    # The day's theme makes each title distinct: a templated "Cariboo Signals,
    # <date>" title shares its term set with yesterday's, and super-rss-feed's
    # story dedup read every review after the first as a repeat.
    title = f"Episode Review — {theme or 'Cariboo Signals'}, {_human_date(date)}"
    generated = next((r for r in runs if r.get("conclusion") == "success"), None)

    parts = []
    if theme:
        parts.append(f"Theme: {escape(theme)}.")
    if generated and generated.get("url"):
        parts.append(f'Run <a href="{escape(generated["url"])}">{generated["run_id"]}</a>.')
    lede = f"<p class=\"meta\">{' '.join(parts)}</p>" if parts else ""

    signals = run_signals({**facts, "runs": runs})
    level, summary = verdict({**facts, "runs": runs}, signals)
    icon, label = _STATUS[level]
    banner = (f'<div class="verdict lvl-{level}"><span class="icon" aria-hidden="true">{icon}</span>'
              f'<div><strong>{label}</strong>{escape(summary)}</div></div>')
    actions = render_actions(signals, ledger)
    detail = f"<h2>What happened</h2>\n{narrative}" if narrative else ""
    tables = "\n\n".join(t for t in [render_run_table(runs), render_numbers_table(facts)] if t)

    content = "\n\n".join(p for p in [lede, banner, actions, detail, tables] if p)
    page_body = "\n\n".join(p for p in [
        lede, banner, render_kpis(facts, runs), actions, render_charts(facts),
        render_trends(history or [], review_metrics(facts), date), detail,
        f"<details><summary>Raw numbers</summary>\n{tables}\n</details>" if tables else ""] if p)
    page = PAGE.format(title=escape(title), published=_human_date(date),
                       site=SITE_URL, content=page_body)
    return title, content, page


def _human_date(date: str) -> str:
    return datetime.strptime(date, "%Y-%m-%d").strftime("%B %-d, %Y")


# ---------------------------------------------------------------------------
# Index + RSS
# ---------------------------------------------------------------------------

def load_index() -> list[dict]:
    if not INDEX_FILE.exists():
        return []
    try:
        return json.loads(INDEX_FILE.read_text("utf-8"))
    except json.JSONDecodeError:
        print("  ⚠️  reviews index unreadable — rebuilding from this review alone")
        return []


def update_index(date: str, title: str, content: str,
                 metrics: dict[str, float] | None = None) -> list[dict]:
    """Record this review in the index, replacing any entry for the same date."""
    index = [e for e in load_index() if e.get("date") != date]
    index.append({
        "date": date,
        "title": title,
        "url": f"{SITE_URL}/podcasts/reviews/episode-review-{date}.html",
        "content_html": content,
        "published": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # The trend charts read these; the index is the only history the review keeps.
        "metrics": metrics or {},
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
# Roadmap distillation
# ---------------------------------------------------------------------------
#
# The reviews were being distilled into ROADMAP.md by hand (7d3fd10, four
# reviews in), which is the part that stops happening. What made that
# distillation worth reading was not any one night's narrative — it was that
# the same items came back. So the model's job here is narrow: read one day's
# facts and name what would need changing. The recurrence, the dedup and the
# rendering are local, because they are arithmetic and a model that can restate
# a number can also restate it wrong.
#
# An item reaches ROADMAP.md on its ROADMAP_MIN_OCCURRENCES'th sighting, not
# its first: one bad night is an incident, the same bad night twice is a
# roadmap item. Checking a box in the file closes it, and the ledger remembers
# that so tomorrow's review cannot re-open it by mentioning it again.
#
# Each finding also names the *signal* it is about — a degrade() row or a
# metric past a line, read off the facts by `run_signals`. The model's ids and
# titles drift ("dropped 48 articles", "dropped 55 articles"), and by
# 2026-09-23 the ledger held three items for the Brave body budget, five for
# deep-dive citations and seven for script expansion. A signal does not drift, so it is the dedup key; and
# because it comes from the facts, an item closes once its signal stops
# appearing rather than once the model stops mentioning it.

ROADMAP_FILE = Path("ROADMAP.md")
LEDGER_FILE = Path("podcasts/roadmap_ledger.json")

SECTION_BEGIN = "<!-- reviews:begin -->"
SECTION_END = "<!-- reviews:end -->"

ROADMAP_MODEL = os.getenv("CLAUDE_ROADMAP_MODEL", REVIEW_MODEL)
ROADMAP_MAX_TOKENS = 1600
# Sightings before an item is written down, and days of silence before one the
# reviews stopped raising is retired. Seeded and hand-written items never
# retire — a human wrote them, only a human closes them.
ROADMAP_MIN_OCCURRENCES = int(os.getenv("ROADMAP_MIN_OCCURRENCES", "2"))
ROADMAP_RETIRE_DAYS = int(os.getenv("ROADMAP_RETIRE_DAYS", "14"))
ROADMAP_MAX_FINDINGS = 4
ROADMAP_WRAP = 96
_SEEN_KEPT = 10
# Days a signal must be absent from the facts before its item closes. Three,
# not one: a problem that skips a night has not been fixed.
SIGNAL_QUIET_DAYS = int(os.getenv("ROADMAP_SIGNAL_QUIET_DAYS", "3"))
# Where a metric becomes a signal. Below half the articles reaching the script
# is a matching problem. A host imbalance is Casey's share of the deep dive's
# words outside this band (one host near twice the other), never the voice
# ratio: that compares average turn length, and the persona gives Casey the
# shorter turns. The ratio band (0.8, 1.25) fired 11 nights in 60, every one
# with Casey on 39-54% of the words.
CITATION_FLOOR = 0.5
VOICE_SHARE_BAND = (35, 65)
# Shipped tell hits that are a signal: more than the count that sends the polish
# to Opus (podcast_generator.OPUS_QUALITY_HIT_THRESHOLD; a test pins the two
# together). One or two hits are the scan counting idiom, 28 of 40 scripts
# 2026-09-01..10-11 had at least one, so the item could not stay closed.
AI_TELL_SIGNAL_ABOVE = 3
_CLOSED_SHOWN = 25

_FINDING_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "description": ("Findings from this run that imply a change to the code, config "
                            "or workflow. Empty on a run with nothing to act on."),
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string",
                           "description": ("Lowercase hyphenated slug naming the underlying "
                                           "problem, not the day. Reuse an existing id exactly "
                                           "when this is the same finding.")},
                    "title": {"type": "string",
                              "description": ("One sentence stating the problem flatly, ending "
                                              "in a period. No date, no hedging.")},
                    "detail": {"type": "string",
                               "description": ("2-5 sentences: the evidence with its date and "
                                               "numbers, the mechanism, and what would close it. "
                                               "Plain prose, no markdown headings or bullets.")},
                    "signal": {"type": "string",
                               "description": ("The signal from TODAY'S SIGNALS this finding is "
                                               "about, or `other` when none fits.")},
                },
                "required": ["id", "title", "detail", "signal"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}


def _slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")[:60]


def _finding_schema(signals: dict[str, str]) -> dict:
    """The schema with `signal` pinned to today's signals plus `other`."""
    import copy

    schema = copy.deepcopy(_FINDING_SCHEMA)
    item = schema["properties"]["findings"]["items"]
    item["properties"]["signal"]["enum"] = sorted(signals) + ["other"]
    return schema


def run_signals(facts: dict[str, Any]) -> dict[str, str]:
    """Stable key → one line of evidence, for each thing the facts show going wrong.

    Only what a fact can show without judgement: a degrade() row, a first
    draft sent back as short, a metric past its line, a run that failed. The
    unknown unknowns in `other_warnings` have no key until they earn one.
    """
    signals: dict[str, str] = {}
    for entry in facts.get("degradations", []):
        if isinstance(entry, list) and len(entry) == 2:
            signals.setdefault(f"degraded:{entry[0]}", f"degrade() row: {entry[1][:160]}")
    # A short first draft that the expand pass brought to target is the
    # pipeline working (2026-09-28: 2,877 → 3,548 shipped). The listener hears
    # what shipped, so that is what keeps the item open.
    short = facts.get("short_script")
    quality = facts.get("quality")
    shipped = quality[2] if isinstance(quality, list) and len(quality) == 3 else None
    if isinstance(short, list) and len(short) == 2 and (shipped is None or shipped < short[1]):
        signals["short-script"] = (f"shipped {shipped if shipped is not None else '?'} words "
                                   f"against a {short[1]}-word target (first draft {short[0]})")
    cites = facts.get("citation_alignment")
    if isinstance(cites, list) and len(cites) == 4:
        for name, matched, total in (("roundup", cites[0], cites[1]),
                                     ("deep-dive", cites[2], cites[3])):
            if total and matched / total < CITATION_FLOOR:
                signals[f"citations:{name}"] = f"{matched}/{total} {name} citations matched the script"
    quality = facts.get("quality")
    if isinstance(quality, list) and len(quality) == 3:
        hits = quality[0]
        if hits > AI_TELL_SIGNAL_ABOVE:
            signals["ai-tells-shipped"] = f"{hits} AI-tell pattern hit(s) in the shipped script"
    share = facts.get("casey_deep_dive_word_pct")
    if isinstance(share, int) and not VOICE_SHARE_BAND[0] <= share <= VOICE_SHARE_BAND[1]:
        signals["voice-ratio"] = f"Casey spoke {share}% of the deep dive's words"
    failed = [r["trigger"] for r in facts.get("runs", []) if r.get("conclusion") == "failure"]
    if failed:
        signals["run-failed"] = "failed: " + ", ".join(failed)
    return signals


def load_ledger() -> dict:
    """The roadmap ledger, or an empty one. A corrupt ledger is not fatal.

    Losing the ledger costs recurrence history — items start counting again and
    take an extra day to reach the file. Refusing to publish a review over it
    would cost the day's review, which is worth more.
    """
    if not LEDGER_FILE.exists():
        return {"items": []}
    try:
        data = json.loads(LEDGER_FILE.read_text("utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  ⚠️  Roadmap ledger unreadable ({exc}) — starting a fresh one")
        return {"items": []}
    return data if isinstance(data, dict) and isinstance(data.get("items"), list) else {"items": []}


# ---------------------------------------------------------------------------
# The managed section: parse and render
# ---------------------------------------------------------------------------

_ITEM_BOX = re.compile(r"^- \[[ xX]\] ")
_ITEM_START = re.compile(r"^- \[( |x|X)\] \*\*(.+?)\*\*[ ]?(.*)$")


def parse_section(text: str) -> list[dict]:
    """Read the managed section back into items.

    Inverse of `render_section`, and the reason no id ever has to be written
    into the markdown: an item is matched back by its title. It runs on the
    hand-written section too, which is how the existing roadmap items seed the
    ledger instead of being competed with by a second list.

    An item is its box line plus the indented lines under it, rejoined before
    the title is matched. `_render_item` wraps at ROADMAP_WRAP, so a long
    title's closing `**` lands on a continuation line; matched line by line,
    four of the six open items on 2026-10-09 never parsed, and checking their
    boxes closed nothing.
    """
    body = _section_body(text)
    if body is None:
        return []
    paragraphs: list[str] = []
    for line in body.splitlines():
        if _ITEM_BOX.match(line):
            paragraphs.append(line.strip())
        elif paragraphs and line.startswith("  ") and line.strip():
            paragraphs[-1] += " " + line.strip()
    items: list[dict] = []
    for paragraph in paragraphs:
        match = _ITEM_START.match(paragraph)
        if match:
            checked, title, rest = match.groups()
            items.append({"title": title.strip(), "detail": rest.strip(),
                          "done": checked.lower() == "x"})
    return items


def _title_key(title: str) -> str:
    """A title as the file can give it back. The render's wrap folds a double
    space, tab or newline at the break into the one space parse_section rejoins
    with, so an exact match would seed a wrapped title a second time, as a
    manual item that never retires."""
    return " ".join(title.split())


def _section_body(text: str) -> str | None:
    """The text between the markers, or None when the section is absent."""
    start = text.find(SECTION_BEGIN)
    end = text.find(SECTION_END)
    if start == -1 or end == -1 or end < start:
        return None
    return text[start + len(SECTION_BEGIN):end]


def _render_item(item: dict) -> str:
    seen = item.get("seen", [])
    signal = f"signal `{item['signal']}`; " if item.get("signal") else ""
    provenance = (f"({signal}seen in {item['occurrences']} reviews, latest {item['last_seen']})"
                  if item.get("source") == "review" and seen else "")
    box = "[x]" if item.get("status") == "done" else "[ ]"
    line = " ".join(filter(None, [f"- {box} **{item['title']}**",
                                  item.get("detail", "").strip(), provenance]))
    # One flowed paragraph, as the hand-written items were: the detail carries
    # on from the title line when it fits. Long words are never split, so an
    # inline `path/to/thing` or a URL survives the wrap intact.
    return "\n".join(textwrap.wrap(line, width=ROADMAP_WRAP, subsequent_indent="      ",
                                   break_long_words=False, break_on_hyphens=False))


def render_section(items: list[dict]) -> str:
    """The managed block, markers included.

    Ledger order, which is discovery order — seeded items in the order a human
    wrote them, then each new item after the last. Deliberately not sorted by
    recurrence: those counts move every night, and re-sorting on them would
    rewrite the whole block daily for no reader.

    Nothing here reads the run's date, for the same reason. The window is the
    span of reviews that produced the items on show, so it moves when one of
    them does and not otherwise — dating the block by the run instead put a
    one-line diff on ROADMAP.md every single night, which is how a generated
    file teaches its reader to stop looking at it.
    """
    shown = [i for i in items if i.get("status") == "open"]
    dates = sorted({d for i in shown for d in i.get("seen", [])})
    window = ""
    if dates:
        window = f" ({dates[0]}..{dates[-1]})" if dates[0] != dates[-1] else f" ({dates[-1]})"
    stat = "\n".join(textwrap.wrap(
        f"_Distilled from the daily reviews by `episode_review.py`{window} — "
        f"{len(shown)} open. An item with a signal closes itself once the signal has been "
        f"absent for {SIGNAL_QUIET_DAYS} days. Check a box to close one; it comes back only if "
        f"the reviews raise it {ROADMAP_MIN_OCCURRENCES} more times._", width=ROADMAP_WRAP))
    body = "\n".join(_render_item(i) for i in shown) or "_Nothing recurring in the window._"
    return f"{SECTION_BEGIN}\n\n{stat}\n\n{body}\n\n{SECTION_END}"


def apply_section(text: str, section: str) -> str:
    """Swap the managed block into ROADMAP.md, leaving everything else alone."""
    start = text.find(SECTION_BEGIN)
    end = text.find(SECTION_END)
    if start == -1 or end == -1 or end < start:
        return text
    return text[:start] + section + text[end + len(SECTION_END):]


# ---------------------------------------------------------------------------
# Ledger merge
# ---------------------------------------------------------------------------

def seed_ledger(ledger: dict, text: str, date: str) -> dict:
    """Adopt whatever is already in the section as ledger items, once.

    The section was written by hand before this existed. Reproducing those
    items rather than starting empty is what makes the first automated run an
    edit instead of a replacement. They are marked `source: "manual"`, which
    exempts them from retirement.
    """
    known = {_title_key(i["title"]) for i in ledger["items"]}
    for parsed in parse_section(text):
        if _title_key(parsed["title"]) in known:
            continue
        ledger["items"].append({
            "id": _slug(parsed["title"]),
            "title": parsed["title"],
            "detail": parsed["detail"],
            "source": "manual",
            "status": "done" if parsed["done"] else "open",
            "occurrences": ROADMAP_MIN_OCCURRENCES,
            "first_seen": date,
            "last_seen": date,
            "seen": [],
        })
    return ledger


def harvest_checked(ledger: dict, text: str) -> list[str]:
    """Close every item whose box is checked in the file.

    The file is where a human answers, so it is read before it is written.
    Closing resets the counter rather than blocklisting the id: a finding that
    is genuinely still happening earns its way back after
    ROADMAP_MIN_OCCURRENCES more sightings, and one that was actually fixed
    never does.
    """
    checked = {_title_key(i["title"]) for i in parse_section(text) if i["done"]}
    closed = []
    for item in ledger["items"]:
        if _title_key(item["title"]) in checked and item.get("status") != "done":
            item["status"] = "done"
            item["occurrences"] = 0
            closed.append(item["title"])
    return closed


def _finding_signal(finding: dict) -> str:
    signal = str(finding.get("signal") or "")
    return "" if signal == "other" else signal


def _match(ledger: dict, finding: dict) -> dict | None:
    """Find the ledger item a finding is about — by signal, then id, then title.

    The signal is read off the facts, so it does not drift. The id and title
    are the model's, and do: the same problem came back as
    `credit-balance-preflight` and `credit-balance-not-usage-limit` in testing,
    and as five titles that differed only in their numbers in production. A
    finding with a signal falls back to id and title only among items that
    have none yet — a close title under another signal is a different problem
    worded alike.
    """
    from difflib import SequenceMatcher

    signal = _finding_signal(finding)
    if signal:
        for item in ledger["items"]:
            if item.get("signal") == signal:
                return item
    candidates = [i for i in ledger["items"] if not (signal and i.get("signal"))]
    wanted = _slug(finding["id"])
    for item in candidates:
        if item["id"] == wanted:
            return item
    best, score = None, 0.0
    for item in candidates:
        ratio = SequenceMatcher(None, item["title"].lower(), finding["title"].lower()).ratio()
        if ratio > score:
            best, score = item, ratio
    return best if score >= 0.72 else None


def merge_findings(ledger: dict, findings: list[dict], date: str) -> dict:
    """Fold one day's findings in. Recurrence is counted here, not asked for."""
    for finding in findings[:ROADMAP_MAX_FINDINGS]:
        signal = _finding_signal(finding)
        item = _match(ledger, finding)
        if item is None:
            item = {
                "id": _slug(finding["id"]) or _slug(finding["title"]),
                "title": finding["title"].strip(),
                # First sighting's wording wins for the life of the item. A
                # detail rewritten nightly is a daily diff on a file nobody
                # asked to change, and the reader gains nothing.
                "detail": finding["detail"].strip(),
                "source": "review",
                "status": "pending",
                "occurrences": 0,
                "first_seen": date,
                "seen": [],
            }
            ledger["items"].append(item)
        if signal and not item.get("signal"):
            item["signal"] = signal  # an item from before signals adopts one
        if signal:
            item["signal_seen"] = max(item.get("signal_seen", ""), date)
        if date in item.get("seen", []):
            continue  # idempotent: a re-run of the same date is not a recurrence
        item["occurrences"] = item.get("occurrences", 0) + 1
        item["seen"] = (item.get("seen", []) + [date])[-_SEEN_KEPT:]
        item["last_seen"] = date
        if (item.get("status") in ("pending", "done", "retired")
                and item["occurrences"] >= ROADMAP_MIN_OCCURRENCES):
            item["status"] = "open"
            print(f"  📌 Roadmap item promoted: {item['title']}")
    return ledger


def retire_stale(ledger: dict, date: str, signals: dict[str, str] | None = None) -> list[str]:
    """Close tool-written items whose problem has stopped showing.

    An item with a signal closes once the signal has been absent from the
    facts for SIGNAL_QUIET_DAYS: whether the problem is still happening is a
    better answer than whether the model still mentions it, and a present
    signal keeps its item open on nights the model says nothing. `signals` is
    None when the day's log could not be read — no facts is not a fix, so
    nothing closes on a signal that day.

    An item without one falls back to silence: ROADMAP_RETIRE_DAYS without a
    sighting — a quiet week is not a fix — pending ones included, so a single
    sighting no longer sits in the prompt for ever.

    Only `source: "review"` items; a human wrote the rest, only a human closes
    them. The record stays in the ledger with its count restarted, and
    ROADMAP_MIN_OCCURRENCES new sightings put it back.
    """
    today = datetime.strptime(date, "%Y-%m-%d")
    retired = []
    for item in ledger["items"]:
        if item.get("status") not in ("open", "pending") or item.get("source") != "review":
            continue
        signal = item.get("signal")
        if signal:
            if signals is None:
                continue
            if signal in signals:
                item["signal_seen"] = max(item.get("signal_seen", ""), date)
                continue
            last, quiet = item.get("signal_seen") or item.get("last_seen"), SIGNAL_QUIET_DAYS
        else:
            last, quiet = item.get("last_seen"), ROADMAP_RETIRE_DAYS + 1
        if last and (today - datetime.strptime(last, "%Y-%m-%d")).days >= quiet:
            item["status"] = "retired"
            item["occurrences"] = 0
            retired.append(item["title"])
    return retired


# ---------------------------------------------------------------------------
# The call, and the orchestrator
# ---------------------------------------------------------------------------

def propose_findings(facts: dict[str, Any], narrative: str, ledger: dict,
                     roadmap: str, signals: dict[str, str] | None = None) -> list[dict]:
    """One Claude call: today's run in, candidate roadmap items out.

    The open ledger and the rest of ROADMAP.md both go in so the model reuses
    an id instead of coining a synonym, and does not propose work that is
    already written down. Returns [] on any failure — a day without a
    distillation is a day the roadmap does not move, which is the correct cost.
    """
    import anthropic

    template = load_prompts_config().get("roadmap_distill", {}).get("template", "")
    if not template:
        print("  ⚠️  No roadmap_distill prompt configured — skipping distillation")
        return []

    signals = signals or {}

    def line(i: dict) -> str:
        return f"- {i['id']}" + (f" [{i['signal']}]" if i.get("signal") else "") + f": {i['title']}"

    open_items = "\n".join(
        line(i) for i in ledger["items"] if i.get("status") in ("open", "pending")) or "(none)"
    # The most recent only: the closed list grows every week and the model
    # needs it to avoid re-raising what was just fixed, not the whole archive.
    recent = sorted((i for i in ledger["items"] if i.get("status") in ("done", "retired")),
                    key=lambda i: i.get("last_seen", ""), reverse=True)[:_CLOSED_SHOWN]
    closed = "\n".join(line(i) for i in recent) or "(none)"
    signal_lines = "\n".join(f"- {k}: {v}" for k, v in sorted(signals.items())) or "(none)"
    planned = "\n".join(
        line.strip()[6:].strip() for line in roadmap.splitlines()
        if line.strip().startswith("- [ ] "))[:4000]

    prompt = template.format(
        facts_json=json.dumps(label_facts(facts), indent=2, ensure_ascii=False),
        narrative=re.sub(r"<[^>]+>", " ", narrative)[:4000],
        open_items=open_items,
        closed_items=closed,
        planned_items=planned,
        signals=signal_lines,
        max_findings=ROADMAP_MAX_FINDINGS,
        min_occurrences=ROADMAP_MIN_OCCURRENCES,
        tell_block=format_static_tell_block(),
    )
    try:
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        resp = client.messages.create(
            model=ROADMAP_MODEL,
            thinking=thinking_off(ROADMAP_MODEL),
            max_tokens=ROADMAP_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
            output_config=json_output_config(_finding_schema(signals)),
        )
    except Exception as exc:
        print(f"  ⚠️  Roadmap distillation failed ({exc}) — the roadmap is unchanged")
        return []

    usage = getattr(resp, "usage", None)
    if usage:
        print(f"  [api] service=claude model={ROADMAP_MODEL} "
              f"input_tokens={usage.input_tokens} output_tokens={usage.output_tokens}")
    try:
        findings = json.loads(message_text(resp)).get("findings", [])
    except (json.JSONDecodeError, AttributeError) as exc:
        print(f"  ⚠️  Roadmap reply unparseable ({exc}) — the roadmap is unchanged")
        return []
    return [f for f in findings
            if isinstance(f, dict) and f.get("id") and f.get("title") and f.get("detail")]


def distill_roadmap(date: str, facts: dict[str, Any], narrative: str,
                    log_read: bool = False) -> bool:
    """Fold today's review into ROADMAP.md. True when the file changed.

    `log_read` says the facts came from a job log. Without one, today's
    signals are unknown rather than absent, and nothing closes on a signal.

    Never raises: this is a surface on top of the review, and a review that
    published is worth more than a roadmap that updated.
    """
    if not ROADMAP_FILE.exists():
        print(f"  ⚠️  {ROADMAP_FILE} not found — skipping distillation")
        return False
    text = ROADMAP_FILE.read_text("utf-8")
    if _section_body(text) is None:
        print(f"  ⚠️  No {SECTION_BEGIN} … {SECTION_END} block in {ROADMAP_FILE} — "
              "add one where the distilled items should go")
        return False

    ledger = seed_ledger(load_ledger(), text, date)
    for title in harvest_checked(ledger, text):
        print(f"  ☑️  Closed by hand: {title}")

    signals = run_signals(facts)
    if signals:
        print(f"  📡 Signals: {', '.join(sorted(signals))}")
    findings = propose_findings(facts, narrative, ledger, text, signals)
    print(f"  🧭 {len(findings)} finding(s) from the {date} review")
    merge_findings(ledger, findings, date)
    for title in retire_stale(ledger, date, signals if log_read else None):
        print(f"  🗑️  Retired, no longer showing: {title}")

    ledger["updated"] = date
    atomic_write_json(LEDGER_FILE, ledger, ensure_ascii=False)

    updated = apply_section(text, render_section(ledger["items"]))
    if updated == text:
        print(f"  ↔️  {ROADMAP_FILE} unchanged")
        return False
    atomic_write_text(ROADMAP_FILE, updated)
    print(f"✅ {ROADMAP_FILE}")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Episode date (YYYY-MM-DD). Defaults to today, Pacific.")
    parser.add_argument("--log", help="Read the job log from a file instead of the API.")
    parser.add_argument("--no-llm", action="store_true", help="Skip the narrative call.")
    parser.add_argument("--no-roadmap", action="store_true",
                        help="Publish the review without distilling it into ROADMAP.md.")
    parser.add_argument("--dry-run", action="store_true", help="Print the facts, write nothing.")
    parser.add_argument("--skip-if-reviewed", action="store_true",
                        help="Exit if the date already has a review (the backstop after rung 3).")
    args = parser.parse_args()

    date = args.date or datetime.now(PACIFIC).strftime("%Y-%m-%d")
    # Both rung 3 and the backstop carry the review job, for the nights the
    # Worker is down. On every other night the backstop's review repeated
    # rung 3's — two narratives, two distillations — and described its own
    # 20-second no-op as the day's unfinished run.
    if args.skip_if_reviewed and (REVIEWS_DIR / f"episode-review-{date}.html").exists():
        print(f"📝 {date} already reviewed — nothing to do")
        return 0
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
            # The run that made the episode; failing that, the newest one that
            # failed, because a day with no success is the day most worth a
            # review and its log says why (2026-08-23 published a bare table).
            generated = (next((r for r in runs if r.get("conclusion") == "success"), None)
                         or next((r for r in reversed(runs)
                                  if r.get("conclusion") == "failure"), None))
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
    title, content, page = build_review(date, runs, facts, narrative,
                                        history=load_index(), ledger=load_ledger())

    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    page_path = REVIEWS_DIR / f"episode-review-{date}.html"
    atomic_write_text(page_path, page)
    index = update_index(date, title, content, review_metrics(facts))
    atomic_write_text(FEED_FILE, build_feed(index))

    print(f"✅ {page_path}")
    print(f"✅ {FEED_FILE} ({len(index)} review(s))")

    # After the review is on disk, never before: the distillation is a surface
    # on top of it and must not be able to cost the day's review.
    if not (args.no_roadmap or args.no_llm):
        try:
            distill_roadmap(date, facts, narrative, log_read=bool(log))
        except Exception as exc:
            print(f"  ⚠️  Roadmap distillation failed ({exc}) — the review still published")
    return 0


if __name__ == "__main__":
    sys.exit(main())
