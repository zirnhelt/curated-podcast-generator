"""Theme-aware ambient transition sounds for Cariboo Signals.

Loads per-theme ambient MP3 clips from the ambient/ directory and returns
pydub AudioSegment objects trimmed and faded for use as segment transitions.

Falls back to the standard interval music when a theme's ambient file
is not available.
"""

import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
AMBIENT_DIR = SCRIPT_DIR / "ambient"
AMBIENT_CONFIG = SCRIPT_DIR / "config" / "ambient.json"


def load_ambient_config():
    """Load ambient sound configuration."""
    try:
        with open(AMBIENT_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"  Ambient config not found or invalid: {e}")
        return None


def get_ambient_transition(theme_name, fallback_segment=None):
    """Return a pydub AudioSegment for the given theme's ambient transition.

    Args:
        theme_name: The current episode theme (e.g., "Wild Spaces & Outdoor Life")
        fallback_segment: A pydub AudioSegment to use if no ambient file exists
                          (typically the standard interval music, already trimmed)

    Returns:
        pydub.AudioSegment — the ambient transition, or fallback_segment
    """
    from pydub import AudioSegment

    config = load_ambient_config()
    if not config:
        return fallback_segment

    theme_config = config.get("themes", {}).get(theme_name)
    if not theme_config:
        return fallback_segment

    ambient_file = AMBIENT_DIR / theme_config["file"]
    if not ambient_file.exists():
        return fallback_segment

    duration_ms = config.get("duration_ms", 4000)
    fade_in_ms = config.get("fade_in_ms", 500)
    fade_out_ms = config.get("fade_out_ms", 800)

    try:
        clip = AudioSegment.from_mp3(str(ambient_file))

        # Trim to configured duration
        clip = clip[:duration_ms]

        # Apply fades
        clip = clip.fade_in(fade_in_ms).fade_out(fade_out_ms)

        # Normalize to a quiet level (beneath speech, similar to music)
        target_dbfs = -28.0
        if clip.dBFS != float("-inf"):
            change = target_dbfs - clip.dBFS
            clip = clip.apply_gain(change)

        print(f"  🎵 Using ambient transition: {theme_config['file']} ({duration_ms}ms)")
        return clip

    except Exception as e:
        print(f"  Ambient load failed for {ambient_file}: {e}")
        return fallback_segment
