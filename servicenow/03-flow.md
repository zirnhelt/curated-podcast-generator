# Part 3: Flow Designer Flow — `GeneratePodcastEpisode`

Triggered on schedule (daily via `config.schedule_time`) or manually from a Script record. Uses explicit error transitions at each stage — a failed Claude/TTS call sets `script.state = error` with a logged message rather than silently failing.

---

## Flow Inputs

| Input | Type | Notes |
|---|---|---|
| `episode_date` | Date | Defaults to today |
| `theme_sys_id` | Reference | Overrides auto-selection |
| `trigger_source` | String | `schedule / manual / api` |
| `target_user_sys_id` | Reference (optional) | Required when `theme.access_mode = individual` |

---

## Stage 1 — Load Config & Select Theme/Hosts

1. Query `x_snc_podcast_config` for the singleton (`name = Default`). Halt with error if missing.
2. Resolve theme: use `theme_sys_id` input → else auto-select by `day_of_week` if `config.auto_theme_selection` → else use `config.default_theme`.
3. **Validate access mode:** if `theme.access_mode = individual` and no `target_user_sys_id` provided, halt with error `"individual access mode requires target_user_sys_id"`.
4. Resolve cost center: `theme.default_cost_center` (flow input override if provided).
5. Select 2 active hosts from `x_snc_podcast_host`. For debate formats, prefer one `optimist` + one `skeptic`. Randomise which opens the episode.
6. Create the Script record with `state = generating`, `access_mode`, `target_user`, `cost_center` copied from theme/inputs.

---

## Stage 2 — Build Segment Plan & Fetch Content

Script step → `new PodcastTaskFetcher().fetchAllSegments(themeSysId, episodeDate, accessContext)`

`accessContext` shape: `{mode, serviceAccountSysId, accessFilterJson, targetUserSysId}` — built from the Script record.

Loads the theme's active `episode_segment` records in sequence order. For each segment, fetches content by type:

- **`news_roundup` / `deep_dive`** — reads records from the segment's `saved_query` (`sys_report_source`). The source's filter and ORDER BY define what appears and in what priority. Limits by `segment.max_items`.
- **`kpi_snapshot`** — reads current values for each KPI signal in `segment.kpi_signal_list` (see Stage 2b).
- **`psa`** — queries the PSA source (see Stage 2c).
- **`welcome` / `closing`** — no external fetch; Claude generates from context.

Returns `{segments: [{type, label, items, kpis, psa, prompt_addendum}, ...]}`.

---

## Stage 2a — Access Filter Injection

`_executeReportSource()` always uses `GlideRecordSecure` (never `GlideRecord`) and applies access filtering based on `accessContext.mode`:

| Mode | Behaviour |
|---|---|
| `general` | Queries run as `accessContext.serviceAccountSysId`. Account holds the minimum role needed for the domain (e.g. `itil` for ITSM, `sn_hr_core.case_reader` for HRSD). |
| `role_based` | Same as `general` plus appends `theme.access_filter_json` encoded query fragment to every segment query. |
| `individual` | Queries run as `accessContext.targetUserSysId`. Also appends `assignment_group.sys_idIN{userGroups}^ORassigned_to={userId}` so only records the user is directly connected to appear. |

---

## Stage 2b — KPI Snapshot

`segment.kpi_signal_list` is a JSON array of `sys_id` values pointing to platform KPI signal records (the same records used by Process Behavior Charts). `PodcastTaskFetcher._readKPISignals()` queries the signal store for `current_value`, `trend`, `label`, `unit` — no custom aggregate queries needed.

Admins configure KPIs once in the KPI/Process Behavior Charts area; the podcast reads them automatically as signal definitions evolve.

---

## Stage 2c — PSA Content

`psa_type` is a **framing hint to Claude only** — it controls how Claude presents the content, not where it comes from.

| `psa_type` | How Claude frames it |
|---|---|
| `team_spotlight` | Celebrate a team, group, or person behind a win |
| `success_story` | A recently resolved/closed record worth highlighting |
| `knowledge_share` | A KB article, policy, or doc the audience should know |
| `announcement` | A news item, bulletin, or upcoming event |
| `custom` | Admin writes the copy in `prompt_addendum` — no record fetched |

`_fetchPSAContent(segmentRecord, accessContext)`:
- `custom` → returns `{freetext: segmentRecord.prompt_addendum}`
- all others → executes `segmentRecord.saved_query`, returns top record (any table: `kb_knowledge`, `sys_news`, `sn_hr_case`, etc.)

---

## Stage 3 — Deduplicate Against Memory

Script step → `new PodcastMemoryManager().deduplicateTasks(tasksJson, episodeDate)`

Checks `x_snc_podcast_memory` (type `episode`, not yet expired). Exact matches are dropped; same record in a different state is flagged `_is_evolving = true` for "follow-up" framing.

---

## Stage 4 — Build Memory Context & Prompt

Script step → `new PodcastMemoryManager().buildMemoryContext(themeSysId, episodeDate)`

Returns a formatted string covering: recent episodes (21d), debate history for this theme (90d — forces a different central question), CTA history (365d), host personality evolution.

`PodcastScriptGenerator.buildUserPrompt()` iterates the ordered segment plan and emits a section instruction block per segment:

```
EPISODE PLAN (in order):
1. {segment.label} [welcome]       — hosts, theme, optional KPI intro
2. {segment.label} [news_roundup]  — {formattedItems}
3. {segment.label} [psa]           — {psaContent}
4. {segment.label} [deep_dive]     — {formattedItems}
5. {segment.label} [closing]       — sign-off, feedback CTA
```

Each segment's `prompt_addendum` is appended to its section instructions. The closing instruction tone is derived from `theme.persona_description`.

---

## Stage 5 — Generate Script via Claude REST

1. Build system prompt (static, cacheable): host bios, debate stances, format rules, segment structure.
2. Build user prompt (dynamic): theme + tasks + memory context + episode date.
3. REST step → `x_snc_podcast_claude_alias`, POST `/messages`, model from `x_snc_podcast.claude_script_model`.
4. Retry up to `config.max_retries` on 429/503 (exponential backoff: 2s, 4s, 8s).
5. Validate response contains host speaker tags (`**RILEY:**` / `**CASEY:**`). On failure: `script.state = error`.
6. Update Script: `generated_script = raw_script`, `state = polishing`.
7. Accumulate `response.usage.input_tokens` + `output_tokens` onto `script.claude_input_tokens` / `claude_output_tokens`.

---

## Stage 6 — Polish + Fact-Check + Debate Summary

1. REST → Claude (`claude_polish_model`): combined polish + fact-check in one call. Verified sources = task `short_description` values.
2. REST → Claude (`claude_summary_model`, Haiku): extract structured debate summary JSON — `central_question`, host positions + evidence, `topics_covered`, `calls_to_action`.
3. Update Script: `polished_script`, `debate_summary_json`, `central_question`, `state = tts_pending`.

---

## Stage 7 — Parse Script into Script Lines

Script step → `new PodcastScriptParser().parseAndPersist(scriptSysId, polishedScript)`

Creates one `x_snc_podcast_script_line` per spoken turn: `sequence`, `section`, `speaker` (resolved to Host record), `line_text`, `pacing_tag`, `gap_ms`, `word_count`, `source_task` (when text references a known task number).

---

## Stage 8 — Generate TTS Audio

1. Resolve TTS provider: `script.tts_provider` → fallback `config.default_tts_provider`.
2. Update `script.state = tts_processing`.
3. Script step → `new PodcastTTSIntegration().generateAudio(scriptSysId, ttsProviderRecord)`.
   - Dispatches by `provider_key`: `openai / azure / elevenlabs`.
   - Reads voice IDs from `host.voice_openai` / `host.voice_azure` / `host.voice_elevenlabs`.
   - Chunks by `tts_provider.max_chars_per_request`; stitches chunks.
   - Audio stored as SN Attachment or external URL per `config.audio_storage_attachment`.
   - All REST calls via `sn_cc.SNHttpClient` with the provider's `connection_alias` — no credentials in code.

---

## Stage 9 — Finalise, Write Memory & Chargeback

1. Update Script: `audio_url`, `state = published`.
2. Write episode memory record (`memory_type = episode`).
3. Write debate memory record (`memory_type = debate`).
4. Write CTA memory record (`memory_type = cta`).
5. Update `host.personality_clues_json` / `core_memories_json` for both hosts.
6. Script step → `new PodcastUsageTracker().writeChargebackRecord(scriptSysId)` — writes one `x_snc_podcast_usage` record attributed to `script.cost_center`.
