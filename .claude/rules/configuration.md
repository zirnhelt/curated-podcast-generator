---
paths:
  - "config/**"
  - "config_loader.py"
  - "tests/test_config_loader.py"
  - "podcasts/**"
---
<!-- Moved verbatim from CLAUDE.md on 2026-11-02. Claude Code loads this file when a
     matching path is read or edited. Keep each rule to one or two lines; the story goes
     in docs/decisions/. -->

### Configuration (`config_loader.py`)

All content lives in `config/` JSON files, loaded through LRU-cached loaders. No hard-coded strings.

| File | Purpose |
|------|---------|
| `podcast.json` | Title, RSS metadata, `local_places`, TRACE accountability scores, `all_week_events` |
| `hosts.json` | Riley & Casey — bios, voices, personalities, debate stances |
| `themes.json` | 7 daily themes, keywords, lenses, `event_focus` (the election) |
| `super_cycles.json` | Multi-week focus rotations within each daily theme |
| `weekly_anchors.json` | Seeded pool of weekly anchor questions |
| `ai_tells.json` | Hard-banned phrases, `score_script` pattern families, phrase-ledger tuning, rhythm budget |
| `prompts.json` | All Claude prompt templates |
| `pronunciations.json` | Name → spoken alias, applied in order before synthesis; a longer name must precede any name it contains |
| `standing_notes.txt` | **The producer's** standing rules and facts, one per line, in the cached script system prompt and both fact-check prompts. Edited by hand or by merging a weekly proposal PR |
| `interests.txt` | Article relevance rubric |
| `blocklist.json` | Excluded domains and keywords; `email_producer_senders` |
| `credits.json` | Spoken and written credits |
| `psa_organizations.json` | Community org roster + weekday assignments |
| `disciplines.json` | Topic taxonomy for roundup grouping |
| `indigenous_nations.json` | Nation names + aliases the territory check recognises (recognition only) |

**Memory state** (`podcasts/`, committed daily by CI): `episode_memory.json` (35 days), `host_personality_memory.json`, `debate_memory.json` (90 days), `psa_rotation_state.json`, `article_holding.json`, `weekly_anchor_state.json`, `phrase_ledger.json`, `roadmap_ledger.json`, `native_land_cache.json`, `email_queue.json`, `standing_notes_ledger.json`, `youtube_uploads.json`, `ballot_research_cache.json`.
