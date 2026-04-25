# Part 4: Script Includes

---

## `PodcastTaskFetcher`

Contains **no domain knowledge** — no table names, no field names, no filter or sort logic. All of that lives in `sys_report_source` records and the platform KPI signal store.

```js
fetchAllSegments(themeSysId, episodeDate, accessContext)
  // Iterates active episode_segment records for this theme in sequence order
  // Passes accessContext to every _executeReportSource call
  // Returns {segments: [{type, label, items, kpis, psa, prompt_addendum}, ...]}

_fetchSegmentItems(segmentRecord, accessContext)
  // news_roundup / deep_dive:
  //   Reads segmentRecord.saved_query → sys_report_source
  //   Executes via _executeReportSource() with access filtering applied
  //   Limits by segmentRecord.max_items

_executeReportSource(reportSourceSysId, maxItems, accessContext)
  // Reads table + encoded_query + order_by from sys_report_source
  // Always uses GlideRecordSecure (never GlideRecord)
  // general/role_based: runs as accessContext.serviceAccountSysId
  //   + appends accessContext.accessFilterJson if present
  // individual: runs as accessContext.targetUserSysId
  //   + appends _buildIndividualFilter() result
  // Returns array via _serializeRecord()

_buildIndividualFilter(targetUserSysId)
  // Queries sys_user_grmember for the user's groups
  // Returns: "assignment_group.sys_idIN{groupList}^ORassigned_to={userId}"

_serializeRecord(gr, tableName)
  // Domain-agnostic; reads common cross-table fields with null-safety
  // → {sys_id, display_value, table, label, state, priority, age_hours, summary, body}

_readKPISignals(kpiSignalListJson)
  // Queries sn_kpi_signal (or equivalent) for current_value, trend, label, unit
  // No access filter — KPI signals are not ACL-gated
  // Returns [{label, value, unit, trend}]

_fetchPSAContent(segmentRecord, accessContext)
  // custom → {freetext: segmentRecord.prompt_addendum}
  // others → _executeReportSource(segmentRecord.saved_query, 1, accessContext)
```

Adding a new domain (HRSD, SecOps, ITAM, etc.) requires only new `sys_report_source` + theme + episode_segment records. Zero code changes.

---

## `PodcastScriptGenerator`

```js
buildSystemPrompt(host1Rec, host2Rec, themeRec)
  // Static + cacheable
  // Ports config/prompts.json → script_generation_system
  // Replaces Cariboo geography/land acknowledgement with theme.persona_description
  // Injects host full_bio, debate_stance, debate_style, recurring_questions

buildUserPrompt({themeRec, segmentPlan, memoryContext, welcomeHostRec, episodeDate})
  // Dynamic; iterates ordered segment plan
  // Per segment: emits label + type instruction + items/kpis/psa + prompt_addendum
  // Closing instruction tone derived from theme.persona_description
  // _formatNewsItem(item)     → "- [INC0001] Short desc [CRITICAL] (4h old)"
  // _formatDeepDiveItem(item) → full description + age context

buildPolishPrompt(script, themeName, sourceRecordsJson)
  // Ports polish_and_factcheck prompt
  // Verified sources = item display_value + summary fields

buildDebateSummaryPrompt(deepDiveSection, themeName)

callClaude(systemPrompt, userPrompt, model, maxTokens, scriptSysId)
  // sn_cc.SNHttpClient with x_snc_podcast_claude_alias
  // Retry on 429/503 up to config.max_retries (backoff: 2s, 4s, 8s)
  // On success: increments script.claude_input_tokens / claude_output_tokens
  //   from response.usage.input_tokens / output_tokens

_validateScriptResponse(text, host1Key, host2Key)
  // Confirms both host speaker tags present in response
```

Segment type → Claude prompt structure:
- `welcome` — hosts introduce themselves, theme, optional KPI intro
- `news_roundup` — rapid-fire coverage (one host per item, one-sentence reaction)
- `kpi_snapshot` — hosts read and briefly react to KPI values (30–60 seconds)
- `psa` — warm spotlight using `_fetchPSAContent()` result
- `deep_dive` — long-form analytical discussion with counterpoints
- `closing` — sign-off, feedback CTA, persona-appropriate farewell

---

## `PodcastUsageTracker`

```js
recordClaudeUsage(scriptSysId, inputTokens, outputTokens)
  // Increments script.claude_input_tokens / claude_output_tokens

recordTTSUsage(scriptSysId, characterCount)
  // Increments script.tts_characters

writeChargebackRecord(scriptSysId)
  // Reads script.cost_center + accumulated counts
  // Reads per-unit rates from system properties:
  //   x_snc_podcast.cost.claude_input_per_1k_tokens  (default 0.003)
  //   x_snc_podcast.cost.claude_output_per_1k_tokens (default 0.015)
  //   x_snc_podcast.cost.tts_per_1k_chars            (default 0.015)
  // Creates one x_snc_podcast_usage record; read-only after creation
```

---

## `PodcastTTSIntegration`

```js
generateAudio(scriptSysId, ttsProviderRecord)
  // Dispatches by provider_key → returns {audio_url, duration_seconds}

_loadScriptLines(scriptSysId)
_chunkByProvider(lines, maxChars)
_getVoiceForHost(hostRecord, providerKey)
  // Reads voice_openai / voice_azure / voice_elevenlabs field by providerKey
_buildRequestBody(text, voiceId, providerRecord)
  // Fills {{text}} / {{voice}} tokens in provider.request_template_json
_saveAudioAttachment(scriptSysId, audioBytes, chunkIndex)
_callTTSAPI(providerRecord, requestBody)
  // sn_cc.SNHttpClient with provider.connection_alias

_generateOpenAI(scriptSysId, providerRecord)
  // Per-line MP3; calls PodcastUsageTracker.recordTTSUsage()
_generateAzure(scriptSysId, providerRecord)
  // SSML multi-talker per section; calls recordTTSUsage()
_generateElevenLabs(scriptSysId, providerRecord)
  // Per-line calls; calls recordTTSUsage()
```

Azure SSML format (mirrors `azure_tts.py`):

```xml
<speak version="1.0" xmlns:mstts="...">
  <mstts:backgroundaudio .../>
  <voice name="en-US-MultiTalker-Ava-Andrew:DragonHDLatestNeural">
    <mstts:turn speaker="Ava">
      <mstts:express-as style="cheerful" styledegree="1.3">{line_text}</mstts:express-as>
    </mstts:turn>
    <mstts:turn speaker="Andrew">...</mstts:turn>
  </voice>
</speak>
```

---

## `PodcastMemoryManager`

```js
buildMemoryContext(themeSysId, episodeDate)
  // Reads x_snc_podcast_memory where expires_on >= today
  // Returns formatted string: episode history + debate history + CTA log + host personality

deduplicateTasks(tasksJson, episodeDate)
  // → {unique_tasks: [...], evolving_stories: [...]}
  // _is_evolving = true when same sys_id appears with a changed state

writeEpisodeMemory(scriptSysId, topics, themeSysId, sourceTaskIds)
writeDebateMemory(scriptSysId, debateSummaryJson, themeSysId)
writeCTAMemory(scriptSysId, callsToAction, themeSysId)

updateHostPersonality(scriptSysId, personalityCluesJson)
  // Increments occurrence count per signal
  // Promotes signals at threshold to core_memories_json

pruneExpiredMemory()
  // Called from Scheduled Script Execution
  // Deletes x_snc_podcast_memory where expires_on < today
```

---

## `PodcastScriptParser`

```js
parseAndPersist(scriptSysId, scriptText)  → lineCount

_parseScript(scriptText)
  // State machine over lines
  // Detects section markers (Claude opens each section with the segment label from the prompt)
  // Detects speaker tags: **RILEY:** / **CASEY:**
  // Extracts [pause:N] / [overlap:-N] pacing tags from line start
  // Multi-line continuation: lines not starting with ** belong to current speaker
  // Handles both \n and \r\n line endings

_extractPacingTag(text)        → {gap_ms, cleanText}
_resolveHostSysId(speakerName) // Cached lookup by host.name
_heuristicGapMs(text, prevSpeaker, curSpeaker, section)
  // Mirrors podcast_generator.py:heuristic_gap_ms()
  // Same speaker continuing in news_roundup: 700ms
  // Short interjection: 50–150ms
  // Speaker change: 150–600ms

_persistLines(scriptSysId, lines)
  // Bulk GlideRecord inserts ordered by sequence
```
