#!/usr/bin/env python3
"""
Weekly anchor question — the third rotation layer.

The show already rotates a daily theme (7, by weekday) and a super-cycle focus
within each theme. Both narrow *what* a deep dive is about. Neither gives a week
a spine: a single open question the seven deep dives all circle from their own
theme's angle.

This module picks that question. One per ISO week, named on air (unlike the
focus, which is deliberately silent), affecting framing only — article selection
is untouched.

Idempotency: the focus gets it for free by being calendar-derived. An anchor
cannot be, because the pool is eventually LLM-generated, so it is bought with
state instead — the week's choice is pinned in podcasts/weekly_anchor_state.json
on first use and every later run that week returns it unchanged. A Thursday
re-render sees exactly what Monday chose.

No repetition works on two levels: an `id` is never reused at all, and a
`dimension` — the thing the question makes you uncertain about — has a
MIN_WEEKS_BETWEEN_DIMENSIONS cooldown. Keying the guard on dimension rather than
wording is what stops a generated question coming back as a paraphrase.
"""

import json
import os
import re
from datetime import date, timedelta
from pathlib import Path

from config_loader import (
    atomic_write_json,
    load_prompts_config,
    load_themes_config,
    load_weekly_anchors_config,
    message_text,
)

# ponytail: MEMORY_DIR mirrors psa_selector — a future multi-tenant deployment
# points each show at its own state directory without changing any other code.
_MEMORY_BASE = Path(os.environ.get("MEMORY_DIR", Path(__file__).parent))
PODCASTS_DIR = _MEMORY_BASE / "podcasts"
ANCHOR_STATE_FILE = PODCASTS_DIR / "weekly_anchor_state.json"

# A dimension may not come back for this many weeks. Six months is long enough
# that a listener who noticed the first one will not feel the second as a repeat.
MIN_WEEKS_BETWEEN_DIMENSIONS = 26

# Refill the pool before it empties, so a top-up failure costs a warning rather
# than the week's anchor.
MIN_POOL_REMAINING = 4
TOP_UP_COUNT = 6

# Retention. `assigned` only needs to outlive a re-render; `used` is the
# no-repeat ledger and is a few hundred bytes a year.
ASSIGNED_RETENTION_WEEKS = 8
USED_RETENTION_WEEKS = 104

ANCHOR_MODEL = os.getenv("CLAUDE_ANCHOR_MODEL", "claude-sonnet-5")

# This module cannot import degrade() from podcast_generator without a circular
# import — same constraint gemini_tts lives under, same solution.
_degradations: list[str] = []


def drain_degradations() -> list[str]:
    """Return and clear the degradations recorded since the last drain."""
    global _degradations
    drained, _degradations = _degradations, []
    return drained


def iso_week_key(d: date) -> str:
    """ISO week identifier, e.g. '2026-W34'. Monday starts the week."""
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def _week_start(week_key: str) -> date:
    """Monday of an ISO week key, for arithmetic on the retention windows."""
    year, _, week = week_key.partition("-W")
    return date.fromisocalendar(int(year), int(week), 1)


def _weeks_between(a: str, b: str) -> int:
    """Whole weeks from ISO week *a* to ISO week *b*."""
    return (_week_start(b) - _week_start(a)).days // 7


def load_anchor_state() -> dict:
    """Load anchor state from disk, defaulting to an empty ledger.

    A truncated file would deserialize as an exception here rather than silently
    as {} — every write goes through atomic_write_json, so a partial file should
    be impossible, and swallowing one would quietly discard the no-repeat ledger.
    """
    if ANCHOR_STATE_FILE.exists():
        with open(ANCHOR_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("assigned", {})
        data.setdefault("used", [])
        data.setdefault("generated", [])
        return data
    return {"assigned": {}, "used": [], "generated": []}


def save_anchor_state(state: dict) -> None:
    """Persist anchor state atomically.

    # ponytail: no mkdir here — atomic_write_text already creates the parent
    # with parents=True, which also handles a MEMORY_DIR that does not exist yet.
    """
    atomic_write_json(ANCHOR_STATE_FILE, state)


def _prune(state: dict, now_week: str) -> dict:
    """Drop assigned weeks and used entries past their retention windows."""
    state["assigned"] = {
        wk: rec for wk, rec in state.get("assigned", {}).items()
        if _weeks_between(wk, now_week) <= ASSIGNED_RETENTION_WEEKS
    }
    state["used"] = [
        u for u in state.get("used", [])
        if _weeks_between(u.get("week", now_week), now_week) <= USED_RETENTION_WEEKS
    ]
    return state


def _pool(state: dict) -> list:
    """Every candidate anchor: the seeded config pool, then generated top-ups."""
    seeded = load_weekly_anchors_config().get("pool", [])
    return list(seeded) + list(state.get("generated", []))


def _eligible(candidates: list, used: list, now_week: str) -> list:
    """Candidates that break neither no-repetition rule, in pool order.

    An id is spent forever. A dimension is spent for MIN_WEEKS_BETWEEN_DIMENSIONS
    weeks — this is the rule that matters, because it is what a paraphrase cannot
    get around.
    """
    used_ids = {u.get("id") for u in used}
    cooling = {
        u.get("dimension") for u in used
        if u.get("dimension")
        and _weeks_between(u.get("week", now_week), now_week) < MIN_WEEKS_BETWEEN_DIMENSIONS
    }
    return [
        c for c in candidates
        if c.get("id") not in used_ids and c.get("dimension") not in cooling
    ]


def _pick(candidates: list, used: list, week: str):
    """Choose this week's anchor: a week-pinned entry first, else pool order."""
    eligible = _eligible(candidates, used, week)
    for c in eligible:
        if c.get("pin_week") == week:
            return c
    # A pin for a *future* week must not be taken early — that is the whole
    # point of pinning it. A pin for a week already past is overdue rather than
    # void (the feature shipped late, a run was skipped), so it stays in the
    # queue and runs at its pool position.
    available = [
        c for c in eligible
        if not c.get("pin_week") or _weeks_between(c["pin_week"], week) >= 0
    ]
    return available[0] if available else None


def _parse_json_response(text: str):
    """Parse a JSON object/array out of a model response, tolerating a fence."""
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    return json.loads(text)


def _call_claude(client, prompt: str, max_tokens: int, log_usage=None):
    """One Anthropic call, returning parsed JSON, or None on any failure.

    log_usage is the caller's token logger (podcast_generator._log_api_call),
    injected rather than imported to keep this module free of a circular import.
    """
    if not client:
        return None
    try:
        response = client.messages.create(
            model=ANCHOR_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        if log_usage:
            usage = getattr(response, "usage", None)
            log_usage("claude", "input_tokens", getattr(usage, "input_tokens", 0))
            log_usage("claude", "output_tokens", getattr(usage, "output_tokens", 0))
        return _parse_json_response(message_text(response))
    except Exception as exc:
        print(f"⚠️  Weekly anchor: Claude call failed ({exc})")
        return None


def _themes_block() -> str:
    """The seven themes, numbered by weekday, for the framing prompt."""
    themes = load_themes_config()
    return "\n".join(
        f"{wd} ({['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'][int(wd)]}): "
        f"{info['name']} — {info.get('description', '')}"
        for wd, info in sorted(themes.items(), key=lambda kv: int(kv[0]))
    )


def generate_framings(anchor: dict, client, log_usage=None) -> dict:
    """The seven per-weekday angles on this week's question. One call per week.

    Returns {} on any failure — an anchor with no framings still works, the
    question just reaches each day with less shaping.
    """
    cfg = load_prompts_config().get("weekly_anchor", {}).get("framings", {})
    template = cfg.get("template")
    if not template:
        return {}

    parsed = _call_claude(
        client,
        template.format(
            question=anchor.get("question", ""),
            premise=anchor.get("premise", ""),
            themes_block=_themes_block(),
        ),
        max_tokens=1500,
        log_usage=log_usage,
    )
    if not isinstance(parsed, dict):
        _degradations.append(
            f"no per-weekday framings for {anchor.get('question', '?')!r} — "
            "each day frames the question unaided"
        )
        return {}
    return {str(k): str(v) for k, v in parsed.items() if str(k) in "0123456"}


def top_up_pool(client, used: list, count: int = TOP_UP_COUNT, log_usage=None) -> list:
    """Generate fresh anchor candidates conditioned on everything already used.

    Roughly one call every eleven weeks. The prompt is handed every spent
    question *and* its dimension, and told to open a dimension none of them
    touch — the same rule _eligible() then enforces locally.
    """
    cfg = load_prompts_config().get("weekly_anchor", {}).get("pool_top_up", {})
    template = cfg.get("template")
    if not template:
        return []

    used_block = "\n".join(
        f"- {u.get('question', '?')}  [dimension: {u.get('dimension', '?')}]" for u in used
    ) or "- (none yet)"

    parsed = _call_claude(
        client,
        template.format(used_block=used_block, count=count),
        max_tokens=2500,
        log_usage=log_usage,
    )
    if not isinstance(parsed, list):
        _degradations.append("anchor pool top-up produced nothing — pool not refilled")
        return []

    fresh = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        if entry.get("id") and entry.get("question") and entry.get("dimension"):
            fresh.append({
                "id": str(entry["id"]),
                "question": str(entry["question"]),
                "dimension": str(entry["dimension"]),
                "premise": str(entry.get("premise", "")),
                "origin": "generated",
            })
    return fresh


def select_anchor(d: date, client=None, log_usage=None) -> dict | None:
    """This week's anchor question, assigning one if the week has none yet.

    Idempotent within an ISO week: the first call of the week picks and pins,
    every later call returns that same record. Returns None when no anchor is
    available — a configured-empty pool, or a top-up that failed — which the
    caller treats as "run the show exactly as it ran before this feature".
    """
    week = iso_week_key(d)
    state = load_anchor_state()

    existing = state.get("assigned", {}).get(week)
    if existing:
        return existing

    candidates = _pool(state)
    chosen = _pick(candidates, state.get("used", []), week)

    # Pool running dry — refill and retry once. Checked on the miss rather than
    # on a count, so a week whose only eligible entries are cooling down still
    # triggers a refill.
    if chosen is None or len(_eligible(candidates, state.get("used", []), week)) < MIN_POOL_REMAINING:
        fresh = top_up_pool(client, state.get("used", []), log_usage=log_usage)
        if fresh:
            state.setdefault("generated", []).extend(fresh)
            candidates = _pool(state)
            chosen = chosen or _pick(candidates, state.get("used", []), week)

    if chosen is None:
        _degradations.append(
            f"no eligible anchor question for {week} — episode runs without one"
        )
        save_anchor_state(_prune(state, week))
        return None

    record = {
        "id": chosen["id"],
        "question": chosen["question"],
        "dimension": chosen.get("dimension", ""),
        "premise": chosen.get("premise", ""),
        "reading": chosen.get("reading"),
        "framings": generate_framings(chosen, client, log_usage=log_usage),
        "assigned_at": d.isoformat(),
        "origin": chosen.get("origin", "seeded"),
    }

    state.setdefault("assigned", {})[week] = record
    state.setdefault("used", []).append({
        "id": record["id"],
        "dimension": record["dimension"],
        "question": record["question"],
        "week": week,
    })
    save_anchor_state(_prune(state, week))
    return record


def format_anchor_for_prompt(anchor: dict | None, weekday: int, theme_name: str) -> str:
    """Render the on-air anchor block for the script prompt, or '' if no anchor.

    The block is bracketed by blank lines so the surrounding template reads the
    same whether or not an anchor is present.
    """
    if not anchor or not anchor.get("question"):
        return ""
    template = load_prompts_config().get("weekly_anchor", {}).get("on_air_block")
    if not template:
        return ""

    premise = (anchor.get("premise") or "").strip()
    framing = (anchor.get("framings") or {}).get(str(weekday), "").strip()

    return "\n" + template.format(
        question=anchor["question"],
        theme_name=theme_name,
        premise_line=f"WHY IT'S LIVE: {premise}\n" if premise else "",
        framing_line=f"TODAY'S ANGLE: {framing}\n" if framing else "",
    ) + "\n"


def preview(start: date, weeks: int = 12) -> list:
    """Dry-run the next *weeks* assignments without calling Claude or writing state.

    Answers "what is the show asking in October?" — the reason the pool is
    seeded and ordered rather than generated fresh every week.
    """
    state = load_anchor_state()
    used = list(state.get("used", []))
    rows = []
    for i in range(weeks):
        monday = start + timedelta(weeks=i)
        week = iso_week_key(monday)
        assigned = state.get("assigned", {}).get(week)
        if assigned:
            rows.append((week, monday, assigned["question"], assigned.get("dimension", ""), "assigned"))
            continue
        chosen = _pick(_pool(state), used, week)
        if chosen is None:
            rows.append((week, monday, "(pool exhausted — top-up would run)", "", "generate"))
            continue
        rows.append((week, monday, chosen["question"], chosen.get("dimension", ""), "pending"))
        used.append({"id": chosen["id"], "dimension": chosen.get("dimension", ""), "week": week})
    return rows


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Preview upcoming weekly anchor questions")
    parser.add_argument("--preview", type=int, default=12, metavar="WEEKS",
                        help="how many weeks to show (default 12)")
    parser.add_argument("--from", dest="start", metavar="YYYY-MM-DD",
                        help="first Monday to show (default: this week's Monday)")
    args = parser.parse_args()

    today = date.fromisoformat(args.start) if args.start else date.today()
    monday = today - timedelta(days=today.weekday())

    print(f"Weekly anchor questions from {monday} ({iso_week_key(monday)}):\n")
    for week, start_date, question, dimension, status in preview(monday, args.preview):
        flag = {"assigned": "●", "pending": "○", "generate": "+"}[status]
        print(f" {flag} {week}  {start_date}  {question}")
        if dimension:
            print(f"              dimension: {dimension}")
    print("\n ● already assigned   ○ next in pool   + pool exhausted, top-up runs")
