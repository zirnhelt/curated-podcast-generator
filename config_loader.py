#!/usr/bin/env python3
"""
Configuration loader — loads all show content from config/ directory.
Single-file swap point for a future DB-backed or per-tenant config layer.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

CONFIG_DIR = Path(__file__).parent / "config"

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

def get_voice_instructions_for_host(host_key):
    """Get OpenAI TTS delivery/emotion instructions for a host."""
    return load_hosts_config()[host_key]["voice_instructions"]

def get_speed_for_host(host_key):
    """Get OpenAI TTS speed multiplier for a host (defaults to 1.0)."""
    return load_hosts_config()[host_key].get("speed", 1.0)

@lru_cache(maxsize=1)
def _stage_direction_pattern():
    """Compiled pattern matching whitelisted (cue) stage directions, or None."""
    cues = (load_prompts_config().get("gemini_tts", {})
            .get("stage_directions", {}).get("whitelist", []))
    if not cues:
        return None
    alternatives = "|".join(re.escape(c) for c in cues)
    return re.compile(r"\s*\((?:" + alternatives + r")\)", re.IGNORECASE)

def strip_stage_directions(text):
    """Remove whitelisted (cue) delivery hints from *text*.

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
        'blocklist': load_blocklist()
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
