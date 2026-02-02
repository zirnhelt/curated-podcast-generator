#!/usr/bin/env python3
"""
Configuration loader for Cariboo Tech Progress podcast
Loads all text content from config/ directory
"""

import json
from pathlib import Path

CONFIG_DIR = Path(__file__).parent / "config"

def load_podcast_config():
    """Load main podcast configuration."""
    with open(CONFIG_DIR / "podcast.json", 'r') as f:
        return json.load(f)

def load_hosts_config():
    """Load host personalities and settings."""
    with open(CONFIG_DIR / "hosts.json", 'r') as f:
        return json.load(f)

def load_themes_config():
    """Load daily themes."""
    with open(CONFIG_DIR / "themes.json", 'r') as f:
        return json.load(f)

def load_credits_config():
    """Load credits information."""
    with open(CONFIG_DIR / "credits.json", 'r') as f:
        return json.load(f)

def load_interests():
    """Load Claude scoring interests as plain text."""
    with open(CONFIG_DIR / "interests.txt", 'r') as f:
        return f.read()

def load_prompts_config():
    """Load Claude API prompts."""
    with open(CONFIG_DIR / "prompts.json", 'r') as f:
        return json.load(f)

def get_voice_for_host(host_key):
    """Get TTS voice for a host."""
    hosts = load_hosts_config()
    return hosts[host_key]["voice"]

def get_theme_for_day(weekday):
    """Get theme for specific day of week (0=Monday, 6=Sunday)."""
    themes = load_themes_config()
    return themes[str(weekday)]["name"]

def get_all_config():
    """Load all configuration at once."""
    return {
        'podcast': load_podcast_config(),
        'hosts': load_hosts_config(),
        'themes': load_themes_config(),
        'credits': load_credits_config(),
        'interests': load_interests(),
        'prompts': load_prompts_config()
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
