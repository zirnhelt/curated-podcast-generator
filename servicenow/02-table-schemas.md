# Part 2: Table Schemas

## `x_snc_podcast_episode_segment` — Segment Recipe per Theme

An episode is assembled from an **ordered list of segment records** rather than a fixed structure. Each theme has its own recipe. Segments can be individually enabled/disabled. This replaces the hard-coded `welcome → news → spotlight → deep_dive` sequence from the Python version.

| Field | Type | Notes |
|---|---|---|
| `theme` | Reference → theme | Parent theme |
| `segment_type` | Choice | `welcome / news_roundup / kpi_snapshot / psa / deep_dive / closing` |
| `sequence` | Integer | Order in the episode |
| `active` | Boolean | Toggle without deleting |
| `label` | String | Display label, e.g. "Incident Roundup", "Team Spotlight", "Weekly KPIs" |
| `saved_query` | Reference → `sys_report_source` | Report data source for `news_roundup`, `deep_dive`, and non-custom `psa` segments; the source's own sort order is the natural prioritisation |
| `max_items` | Integer | Record limit for this segment |
| `age_threshold_hours` / `min_age_hours` | Integer | Age filters injected into query |
| `prompt_addendum` | Long text | Optional extra instructions appended to Claude's section prompt |
| `psa_type` | Choice | `team_spotlight / success_story / knowledge_share / announcement / custom` — framing hint for Claude only; see `03-flow.md` Stage 2c |
| `kpi_signal_list` | Long text | JSON array of `sys_id` refs to platform KPI signal records — used by `kpi_snapshot` segments |

A theme with all segments active produces a full episode. A theme with only `welcome + news_roundup + closing` produces a short briefing.

---

## `x_snc_podcast_script` — Episode Record

| Field | Type | Notes |
|---|---|---|
| `number` | Auto (PCAST0001…) | Episode identifier |
| `episode_date` | Date | Date this episode covers |
| `theme` | Reference → theme | Selected theme |
| `state` | Choice | `draft → generating → polishing → tts_pending → tts_processing → published → error` |
| `access_mode` | Choice | Copied from theme at creation: `general / role_based / individual` |
| `target_user` | Reference → `sys_user` | Set only when `access_mode = individual` |
| `cost_center` | Reference → `cmn_cost_center` | Inherited from theme, overridable per episode |
| `generated_script` | Long text | Raw Claude output |
| `polished_script` | Long text | Post-polish/fact-check version |
| `audio_url` | URL | Final audio file |
| `audio_duration_seconds` | Integer | |
| `tts_provider` | Reference → tts_provider | Provider used |
| `host_1` / `host_2` | Reference → host | The two hosts for this episode |
| `debate_summary_json` | Long text | Structured JSON from Claude summary extraction |
| `central_question` | String(500) | Parsed from `debate_summary_json` |
| `source_record_list` | Long text | JSON array of sourced record sys_ids |
| `claude_input_tokens` | Integer | Accumulated across all Claude calls in this episode |
| `claude_output_tokens` | Integer | Accumulated across all Claude calls in this episode |
| `tts_characters` | Integer | Total characters sent to TTS |
| `generation_log` | Journal | Execution log |
| `error_message` | Long text | Set when `state = error` |

---

## `x_snc_podcast_script_line` — One Per Spoken Line

| Field | Type | Notes |
|---|---|---|
| `script` | Reference → script | Parent episode |
| `sequence` | Integer | Ordering |
| `speaker` | Reference → host | Which host speaks |
| `section` | Choice | Mirrors `segment_type`: `welcome / news_roundup / kpi_snapshot / psa / deep_dive / closing` |
| `line_text` | Long text | Spoken text |
| `pacing_tag` | String(50) | Raw tag, e.g. `[pause:400]` or `[overlap:-100]` |
| `gap_ms` | Integer | Parsed gap (positive = silence, negative = overlap) |
| `source_task` | Reference → task | OOTB task record this line references |
| `word_count` | Integer | Computed on insert |
| `audio_url` | URL | Per-line audio segment (if line-level TTS used) |

---

## `x_snc_podcast_host` — Configurable Personas

| Field | Type | Notes |
|---|---|---|
| `name` / `host_key` | String | Display name + unique internal key |
| `pronouns` | String | e.g. `she/her` |
| `short_bio` / `full_bio` | String / Long text | Injected into Claude system prompt |
| `debate_stance` | Choice | `optimist / skeptic / neutral / challenger` |
| `debate_style` | Long text | How this host debates (injected into prompt) |
| `recurring_questions` | Long text | JSON array |
| `consistent_interests` | Long text | JSON array |
| `default_tts_provider` | Reference → tts_provider | Per-host provider override |
| `voice_openai` | String | e.g. `nova` |
| `voice_azure` | String | e.g. `en-US-Ava:DragonHDLatestNeural` |
| `voice_elevenlabs` | String | ElevenLabs voice ID |
| `azure_style` | String | e.g. `cheerful` |
| `azure_style_degree` | Decimal | e.g. `1.3` |
| `personality_clues_json` | Long text | Rolling evolution buffer (mirrors `host_personality_memory.json`) |
| `core_memories_json` | Long text | Promoted high-frequency signals |
| `active` | Boolean | |

Voice fields are explicit per provider (not a single JSON blob) so non-developers can edit them from the standard form.

---

## `x_snc_podcast_theme` — Selectable Themes

| Field | Type | Notes |
|---|---|---|
| `name` / `theme_key` | String | Display name + unique key |
| `description` | Long text | Injected into Claude prompt |
| `persona_description` | Long text | Context framing — drives tone and sign-off style |
| `keywords` | Long text | JSON array for relevance scoring |
| `day_of_week` | Choice (0–6, blank=manual) | Optional pin to a weekday |
| `active` | Boolean | |
| **Access control** | | |
| `access_mode` | Choice | `general / role_based / individual` |
| `access_service_account` | Reference → `sys_user` | Service account for `general` and `role_based` modes — holds minimum required role for the domain |
| `access_role` | Reference → `sys_user_role` | For `role_based` — the role whose visibility this episode reflects |
| `access_filter_json` | Long text | Optional extra encoded query fragment injected into every segment query |
| **Cost** | | |
| `default_cost_center` | Reference → `cmn_cost_center` | Charged for API usage by default; overridable per episode |

`format`, task queries, segment labels, and item counts are not theme fields — they live on the theme's `episode_segment` records.

---

## `x_snc_podcast_tts_provider` — Admin-Selectable TTS Backends

| Field | Type | Notes |
|---|---|---|
| `name` / `provider_key` | String | Display + unique key (`openai`, `azure`, `elevenlabs`) |
| `connection_alias` | Reference → sys_connection_alias | Credential store reference |
| `endpoint_url` | URL | Base endpoint |
| `model` | String | e.g. `tts-1` |
| `request_template_json` | Long text | JSON body template with `{{text}}` and `{{voice}}` tokens |
| `audio_format` | Choice | `mp3 / wav / ogg` |
| `max_chars_per_request` | Integer | Chunking limit |
| `is_default` | Boolean | Exactly one true |
| `active` | Boolean | |

---

## `x_snc_podcast_memory` — Anti-Repetition Store

| Field | Type | Notes |
|---|---|---|
| `memory_type` | Choice | `episode / debate / cta / host_personality` |
| `episode_date` | Date | When created |
| `theme` | Reference → theme | |
| `retention_days` | Integer | 21 (episode), 90 (debate), 365 (cta) |
| `expires_on` | Date (computed) | `episode_date + retention_days` |
| `topic_summary` | String(1000) | |
| `topics_json` / `debate_summary_json` / `calls_to_action_json` | Long text | Type-specific payload |
| `source_task_ids` | Long text | JSON array of task sys_ids sourced |
| `script` | Reference → script | Back-reference |

Expired records are purged by `PodcastMemoryManager.pruneExpiredMemory()` via Scheduled Script Execution.

---

## `x_snc_podcast_usage` — API Usage + Chargeback

One record per published episode. Read-only after creation; no financial posting — informational only.

| Field | Notes |
|---|---|
| `script` | Reference → episode |
| `cost_center` | Reference → `cmn_cost_center` |
| `episode_date` | Date |
| `theme` | Reference → theme |
| `access_mode` | Choice (copied from episode) |
| `target_user` | Reference → `sys_user` (individual mode only) |
| `claude_input_tokens` / `claude_output_tokens` | Integer |
| `tts_characters` | Integer |
| `estimated_cost_usd` | Decimal |

---

## `x_snc_podcast_config` — Singleton Admin Config

| Field | Notes |
|---|---|
| `name` | Always "Default" |
| `default_tts_provider` | Reference → tts_provider |
| `auto_theme_selection` | Boolean — pick theme by day-of-week if true |
| `anthropic_connection_alias` | Reference → sys_connection_alias |
| `schedule_enabled` / `schedule_time` | Boolean / Time — daily run config |
| `audio_storage_attachment` | Boolean — store audio as SN attachment vs. external URL |
| `audio_base_url` | URL — external storage base |
| `max_retries` | Integer — API retry attempts (default 3) |
