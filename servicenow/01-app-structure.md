# Part 1: Application Structure

```
servicenow/
├── now_app_manifest.xml
├── tables/
│   ├── x_snc_podcast_script.xml            # Episode record
│   ├── x_snc_podcast_script_line.xml       # One per spoken line
│   ├── x_snc_podcast_host.xml              # Configurable host personas
│   ├── x_snc_podcast_theme.xml             # Themes
│   ├── x_snc_podcast_episode_segment.xml   # Segment recipe per theme (ordered)
│   ├── x_snc_podcast_tts_provider.xml      # Admin-selectable TTS backends
│   ├── x_snc_podcast_memory.xml            # Anti-repetition memory store
│   ├── x_snc_podcast_usage.xml             # API usage + chargeback records
│   └── x_snc_podcast_config.xml            # Singleton admin config
├── script_includes/
│   ├── PodcastTaskFetcher.js               # Fetches records via report sources + access filtering
│   ├── PodcastScriptGenerator.js           # Builds Claude prompts + calls API
│   ├── PodcastTTSIntegration.js            # Dispatches to selected TTS provider
│   ├── PodcastMemoryManager.js             # Reads/writes memory records
│   ├── PodcastScriptParser.js              # Parses script text → Script Line records
│   └── PodcastUsageTracker.js              # Accumulates token/char counts + writes chargeback
├── flows/
│   └── GeneratePodcastEpisode.xml          # Main Flow Designer flow
├── connection_aliases/
│   ├── x_snc_podcast_claude_alias.xml
│   ├── x_snc_podcast_openai_tts_alias.xml
│   ├── x_snc_podcast_azure_tts_alias.xml
│   └── x_snc_podcast_elevenlabs_alias.xml
├── sys_properties/
│   └── x_snc_podcast_properties.xml
└── data/
    ├── seed_hosts.xml                      # Riley + Casey seed records
    ├── seed_themes.xml                     # Default themes + episode_segment records
    ├── seed_report_sources.xml             # sys_report_source records for seeded segments
    ├── seed_tts_providers.xml              # OpenAI TTS default record
    └── seed_config.xml                     # Singleton config record
```
