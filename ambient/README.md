# Themed Ambient Chimes

Each file here provides the intermission chime for one of the seven weekly
themes.  When a file is present it replaces the generic `cariboo-signals-interval.mp3`
for **all** in-episode transitions (Welcome→News, News→Spotlight,
Spotlight→Deep Dive).  When a file is absent those transitions fall back to
the generic chime automatically.

## Required files

| Theme | Filename | Character |
|-------|----------|-----------|
| Arts, Culture & Digital Storytelling | `ambient-arts.mp3` | Soft acoustic guitar or gentle piano — creative, warm |
| Working Lands & Industry | `ambient-industry.mp3` | Distant machinery hum, wind across open fields |
| Community Tech & Governance | `ambient-civic.mp3` | Quiet office ambience, soft keyboard clicks, civic tone |
| Indigenous Lands & Innovation | `ambient-indigenous.mp3` | Gentle wind through pines, subtle water sounds — respectful and grounded |
| Wild Spaces & Outdoor Life | `ambient-wilderness.mp3` | Birdsong, gentle creek, forest ambience |
| Cariboo Voices & Local News | `ambient-community.mp3` | Gentle small-town morning — distant traffic, birds, coffee shop warmth |
| Resilient Rural Futures | `ambient-futures.mp3` | Soft electronic hum with natural undertones — tech meets nature |

## Generation tips

- Target length: **5–8 seconds** (the code trims to 4 s with 500 ms fade-in /
  800 ms fade-out, so anything longer is fine).
- Target level: normalize to around **-20 dBFS** before saving; the pipeline
  will duck it further to -28 dBFS automatically.
- Suggested tools: [Ludo.ai](https://ludo.ai), ElevenLabs SFX,
  [Freesound.org](https://freesound.org) (CC0 licence), Suno, or any DAW.
- Export as **MP3, 44.1 kHz, stereo**.
