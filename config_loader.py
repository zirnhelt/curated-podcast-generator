#!/usr/bin/env python3
"""
Configuration loader for Cariboo Tech Progress podcast
Loads all text content from config/ directory
"""

import json
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

def get_voice_for_host(host_key):
    """Get TTS voice for a host."""
    return load_hosts_config()[host_key]["voice"]

def get_theme_for_day(weekday):
    """Get theme for specific day of week (0=Monday, 6=Sunday)."""
    return load_themes_config()[str(weekday)]["name"]

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
