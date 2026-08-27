#!/usr/bin/env python3
"""
Configuration loader — loads all show content from config/ directory.
Single-file swap point for a future DB-backed or per-tenant config layer.
"""

import json
import os
import re
import tempfile
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).parent / "config"


# Lives here for the same reason atomic_write_text does: generate_bespoke and
# weekly_anchor both parse JSON out of Claude and neither may import the
# pipeline.
def json_output_config(schema: dict) -> dict:
    """An `output_config` that constrains the reply to `schema`.

    Replaces the strip-the-fences-and-hope pattern these calls used to carry:
    each one asked in prose for "ONLY JSON", then hand-peeled ``` fences before
    json.loads and kept a fallback for the days the model added a preamble
    anyway. Constraining the format server-side removes that failure mode. The
    fallbacks stay — a constrained response is still a response that can fail
    to arrive.

    Deliberately carries no `effort` key. Most callers are Haiku calls made
    through client.messages.create rather than create_message, and Haiku 4.5
    rejects effort; a caller that wants both passes one merged dict.

    Every object node is stamped `additionalProperties: false` on the way
    through, because the API rejects the schema outright without it and the
    rejection is invisible until it fires in production: `_FINDING_SCHEMA`
    shipped one level short on 2026-08-24 and the roadmap distiller 400'd
    every night from then, returning zero findings while the run stayed green.
    The nine schemas that already say so are unchanged by this — it is a floor,
    not an override — and this is the one choke point every caller passes.
    """
    return {"format": {"type": "json_schema", "schema": _closed(schema)}}


def _closed(node: Any) -> Any:
    """Copy `node`, setting `additionalProperties: false` on every object."""
    if isinstance(node, list):
        return [_closed(v) for v in node]
    if not isinstance(node, dict):
        return node
    out = {k: _closed(v) for k, v in node.items()}
    if out.get("type") == "object":
        out["additionalProperties"] = False
    return out


# Lives here rather than in podcast_generator so psa_selector can use it too
# without a circular import — this is the module every other one already loads.
def atomic_write_text(path, text: str, encoding: str = "utf-8") -> None:
    """Write text to path via a sibling temp file + os.replace.

    Every state file in this project was previously opened 'w' and truncated in
    place. A crash or OOM mid-write left truncated JSON, which the loaders
    swallow as {} — silently discarding a 35- or 90-day history. os.replace is
    atomic within a filesystem, so a reader sees either the old file or the new.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        # Leaving .tmp files behind would accumulate in podcasts/ over months.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path, data, **dump_kwargs) -> None:
    """json.dump to path atomically. Defaults match the call sites it replaces."""
    dump_kwargs.setdefault("indent", 2)
    atomic_write_text(path, json.dumps(data, **dump_kwargs))

@lru_cache(maxsize=1)
def load_podcast_config():
    """Load main podcast configuration (cached)."""
    with open(CONFIG_DIR / "podcast.json", 'r') as f:
        return json.load(f)

@lru_cache(maxsize=1)
def load_hosts_config():
    """Load host personalities and settings (cached)."""
    with open(CONFIG_DIR / "hosts.json", 'r') as f:
        return json.load(f)

@lru_cache(maxsize=1)
def load_themes_config():
    """Load daily themes (cached)."""
    with open(CONFIG_DIR / "themes.json", 'r') as f:
        return json.load(f)

@lru_cache(maxsize=1)
def load_credits_config():
    """Load credits information (cached)."""
    with open(CONFIG_DIR / "credits.json", 'r') as f:
        return json.load(f)

@lru_cache(maxsize=1)
def load_interests():
    """Load Claude scoring interests as plain text (cached)."""
    with open(CONFIG_DIR / "interests.txt", 'r') as f:
        return f.read()

@lru_cache(maxsize=1)
def load_prompts_config():
    """Load Claude API prompts (cached)."""
    with open(CONFIG_DIR / "prompts.json", 'r') as f:
        return json.load(f)

@lru_cache(maxsize=1)
def load_psa_organizations():
    """Load PSA organizations roster (cached)."""
    with open(CONFIG_DIR / "psa_organizations.json", 'r') as f:
        return json.load(f)["organizations"]

@lru_cache(maxsize=1)
def load_psa_events():
    """Load PSA events calendar (cached)."""
    with open(CONFIG_DIR / "psa_events.json", 'r') as f:
        return json.load(f)["events"]

@lru_cache(maxsize=1)
def load_blocklist():
    """Load content blocklist (cached)."""
    blocklist_path = CONFIG_DIR / "blocklist.json"
    if blocklist_path.exists():
        with open(blocklist_path, 'r') as f:
            return json.load(f)
    return {"title_keywords": []}

@lru_cache(maxsize=1)
def load_disciplines_config():
    """Load science/topic discipline hierarchy for news roundup grouping (cached)."""
    disciplines_path = CONFIG_DIR / "disciplines.json"
    if disciplines_path.exists():
        with open(disciplines_path, 'r') as f:
            return json.load(f)
    return {"groups": {}}

@lru_cache(maxsize=1)
def load_bespoke_hosts():
    """Load bespoke (long-form) host personalities (cached)."""
    with open(CONFIG_DIR / "bespoke_hosts.json", 'r') as f:
        return json.load(f)["default_bespoke"]

@lru_cache(maxsize=1)
def load_bespoke_config():
    """Load bespoke episode generation config (cached). Returns {} if file absent."""
    path = CONFIG_DIR / "bespoke_config.json"
    if not path.exists():
        return {}
    with open(path, 'r') as f:
        return json.load(f)

@lru_cache(maxsize=1)
def load_super_cycles_config():
    """Load multi-week focus cycles within daily themes (cached). Returns {} if file absent."""
    path = CONFIG_DIR / "super_cycles.json"
    if not path.exists():
        return {}
    with open(path, 'r') as f:
        return json.load(f)

@lru_cache(maxsize=1)
def load_weekly_anchors_config():
    """Load the weekly anchor-question pool (cached). Returns {} if file absent."""
    path = CONFIG_DIR / "weekly_anchors.json"
    if not path.exists():
        return {}
    with open(path, 'r') as f:
        return json.load(f)

@lru_cache(maxsize=1)
def load_ai_tells_config():
    """Load the AI speech-tell corpus (cached). Returns {} if file absent.

    Optional by design: score_script falls back to its built-in pattern dict and
    the ledger no-ops, so a missing or malformed file costs style enforcement,
    never a run.
    """
    path = CONFIG_DIR / "ai_tells.json"
    if not path.exists():
        return {}
    with open(path, 'r') as f:
        return json.load(f)

def format_static_tell_block():
    """The config-only half of the burned-phrase block: hard bans plus the rhythm
    budget. Lives here so generate_bespoke.py can use it without importing the
    whole pipeline — same reason atomic_write_text does.

    podcast_generator composes this with the dynamic ledger half; bespoke uses it
    alone, since the ledger's rates are derived from daily scripts.
    """
    tells = load_ai_tells_config()
    hard = [p for p in tells.get('hard_banned', []) if p.strip()]
    rhythm = tells.get('rhythm', {})
    if not hard and not rhythm:
        return ""

    lines = []
    if hard:
        lines.append("BURNED PHRASES — do not use any of these, in any form:")
        lines.append("  " + ", ".join(f'"{p}"' for p in hard))
        lines.append(
            "  Do not substitute a synonym either — swapping one intensifier for "
            "another is the same tic wearing a hat. Delete it, or replace it with "
            "the specific detail that made you want to emphasise."
        )
    if rhythm:
        lines.append(
            f"RHYTHM BUDGET: at least {rhythm.get('min_short_turns', 8)} turns under "
            f"{rhythm.get('short_turn_max_words', 15)} words; fewer than "
            f"{rhythm.get('max_em_dashes_per_1k_words', 8)} em dashes per thousand words; "
            f"the \"not X, it's Y\" flip at most "
            f"{rhythm.get('max_antithesis_per_script', 2)} times. Uniform turn length and "
            "a dash before every elaboration is what machine prose sounds like."
        )
    return "\n".join(lines)


@lru_cache(maxsize=1)
def load_notable_dates():
    """Load notable dates calendar for theme-aligned secondary mentions (cached)."""
    path = CONFIG_DIR / "notable_dates.json"
    if path.exists():
        with open(path, 'r') as f:
            return json.load(f)["dates"]
    return []

def get_voice_for_host(host_key):
    """Get TTS voice for a host."""
    return load_hosts_config()[host_key]["voice"]

def get_azure_voice_for_host(host_key):
    """Get Azure Neural TTS voice name for a host."""
    return load_hosts_config()[host_key]["azure_voice"]

def get_gemini_voice_for_host(host_key):
    """Get Gemini TTS prebuilt voice name for a host."""
    return load_hosts_config()[host_key]["gemini_voice"]

def get_gemini_audio_profile_for_host(host_key):
    """One-line vocal profile for the Gemini prompt's AUDIO PROFILE section.

    Short by design: it names who the voice is, not how the show sounds. The
    show-level direction is prompts.json's gemini_tts.style_prompt.
    """
    return load_hosts_config()[host_key].get("gemini_audio_profile", "")

def get_voice_instructions_for_host(host_key):
    """Get OpenAI TTS delivery/emotion instructions for a host."""
    return load_hosts_config()[host_key]["voice_instructions"]

def get_speed_for_host(host_key):
    """Get OpenAI TTS speed multiplier for a host (defaults to 1.0)."""
    return load_hosts_config()[host_key].get("speed", 1.0)

@lru_cache(maxsize=1)
def _stage_direction_pattern():
    """Compiled pattern matching whitelisted [tag] / (cue) directions, or None.

    Both delimiters, and both whitelists. Cues are written `[thoughtfully]` now
    that the Gemini prompt is scaffolded the way the model documents, but every
    script already on disk carries the older `(wry)` parentheticals and a
    re-render of one still has to strip them for the OpenAI and Azure paths.
    """
    directions = (load_prompts_config().get("gemini_tts", {})
                  .get("stage_directions", {}))
    cues = list(directions.get("whitelist", [])) + list(directions.get("legacy_whitelist", []))
    if not cues:
        return None
    alternatives = "|".join(re.escape(c) for c in dict.fromkeys(cues))
    return re.compile(
        r"\s*(?:\((?:" + alternatives + r")\)|\[(?:" + alternatives + r")\])",
        re.IGNORECASE,
    )

def strip_stage_directions(text):
    """Remove whitelisted delivery cues from *text*.

    Gemini TTS performs these cues; every other provider would read them
    aloud, so their synthesis paths strip them first. Whitelist-driven so
    genuine parenthetical dialog is never touched.
    """
    pattern = _stage_direction_pattern()
    return pattern.sub("", text) if pattern else text

def render_credits_text(tts_credit):
    """Plain-text credits block with the TTS provider line filled in."""
    return load_credits_config()["text"].replace("{tts_credit}", tts_credit)

def get_theme_for_day(weekday):
    """Get theme for specific day of week (0=Monday, 6=Sunday)."""
    return load_themes_config()[str(weekday)]["name"]

def get_focus_for_day(weekday: int, d: date):
    """Return the super-cycle focus dict for *weekday* on date *d*, or None.

    Cycle position is calendar-derived — (toordinal // 7) % cycle length — so it
    is stateless, idempotent across re-runs, and predictable weeks ahead. Days
    without a configured cycle (e.g. Saturday) return None and run the plain
    daily theme.
    """
    cycle = load_super_cycles_config().get(str(weekday), {}).get("cycle", [])
    if not cycle:
        return None
    index = (d.toordinal() // 7) % len(cycle)
    focus = dict(cycle[index])
    focus["index"] = index
    focus["cycle_length"] = len(cycle)
    return focus

def get_upcoming_day_slots(d: date, horizon_days: int = 14) -> list:
    """Enumerate (date, weekday, theme_name, focus|None) for each day after *d*.

    Used by the article-holding router to find the soonest day an off-theme
    article belongs to. Excludes *d* itself.

    Every day in the horizon gets a slot, including days with no super-cycle
    (Saturday). This used to emit focus-bearing days only, and match on focus
    keywords alone: a forestry story on a Monday found no home because Tuesday's
    focus was Agriculture & Ranching that week, even though forestry is a
    Tuesday *theme* keyword outright. The theme is the day's standing identity
    and the focus only narrows it, so the router needs both.
    """
    slots = []
    for offset in range(1, horizon_days + 1):
        day = d + timedelta(days=offset)
        slots.append((
            day,
            day.weekday(),
            get_theme_for_day(day.weekday()),
            get_focus_for_day(day.weekday(), day),
        ))
    return slots

def message_text(response) -> str:
    """Concatenate all text blocks from an Anthropic message response.

    Reasoning-capable models (e.g. claude-sonnet-5) may lead with a
    ThinkingBlock, so response.content[0] is not guaranteed to be a text block.
    Join every text block instead of indexing, which crashes on non-text leads.
    """
    return "".join(block.text for block in response.content if block.type == "text")

def get_all_config():
    """Load all configuration at once."""
    return {
        'podcast': load_podcast_config(),
        'hosts': load_hosts_config(),
        'themes': load_themes_config(),
        'credits': load_credits_config(),
        'interests': load_interests(),
        'prompts': load_prompts_config(),
        'psa_organizations': load_psa_organizations(),
        'psa_events': load_psa_events(),
        'blocklist': load_blocklist(),
        'super_cycles': load_super_cycles_config(),
        'weekly_anchors': load_weekly_anchors_config(),
        'ai_tells': load_ai_tells_config()
    }

if __name__ == "__main__":
    # Test the config loader
    print("Testing configuration loader...")
    
    config = get_all_config()
    
    print(f"\n📻 Podcast: {config['podcast']['title']}")
    print(f"🎙️  Hosts: {', '.join(config['hosts'].keys())}")
    print(f"📅 Themes: {len(config['themes'])} daily themes")
    print(f"✅ Credits loaded: {len(config['credits']['structured'])} items")
    print(f"📝 Interests: {len(config['interests'])} characters")
    print(f"🤖 Prompts: {len(config['prompts'])} prompt templates")
    
    print("\n✅ All configs loaded successfully!")
