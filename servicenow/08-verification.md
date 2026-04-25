# Part 8: Verification Checklist

Run these in order. Each step builds on the previous one.

---

1. **Tables only**
   Create seed records in the dev instance. Confirm:
   - All fields appear on forms with correct types
   - Host `voice_openai` / `voice_azure` / `voice_elevenlabs` fields are directly editable (not a JSON blob)
   - `episode_segment` records for a theme are orderable by `sequence` and individually togglable via `active`

2. **Record fetching**
   Run from a background script:
   ```js
   var ctx = {mode:'general', serviceAccountSysId:'<itil_user_sys_id>', accessFilterJson:'', targetUserSysId:''};
   var result = new PodcastTaskFetcher().fetchAllSegments('<daily_ops_theme_sys_id>', new GlideDate(), ctx);
   gs.info(JSON.stringify(result));
   ```
   Confirm it returns records from the seeded `sys_report_source` with correctly populated `sys_id`, `display_value`, `state`, `priority`, `age_hours`, `summary`.

3. **Access control — general mode**
   Set `access_service_account` to a user with only the `itil` role. Run the fetcher. Confirm records outside that account's ACL scope (e.g. HR cases, restricted incidents) are absent from the result.

4. **Access control — individual mode**
   Trigger the flow with `target_user_sys_id` of a test user who belongs to one assignment group. Confirm the episode roundup contains only incidents/problems assigned to that group or directly to that user.

5. **Memory dedup**
   Generate a first episode. Run again with the same date range. Confirm:
   - Records already covered are excluded from `unique_tasks`
   - Records whose `state` changed appear in `evolving_stories` with `_is_evolving = true`

6. **Script generation**
   Trigger the flow manually; inspect `script.generated_script`. Confirm:
   - Contains `**RILEY:**` and `**CASEY:**` speaker turns
   - Section markers match the segment labels from the theme's episode_segment records
   - Task/record numbers from the seed data are referenced in the text

7. **Script line parsing**
   Query `x_snc_podcast_script_line` filtered to the Script record. Confirm:
   - `sequence` is contiguous and ordered
   - `section` values correspond to the correct segment types
   - `pacing_tag` and `gap_ms` are populated where expected
   - `word_count` > 0 on all lines

8. **TTS — OpenAI**
   Complete the flow with the OpenAI provider. Confirm:
   - An attachment appears on the Script record (or `audio_url` is populated if using external storage)
   - `audio_duration_seconds` is populated

9. **TTS provider switch**
   Set `is_default = true` on the Azure TTS provider record (and `false` on OpenAI). Re-run. Confirm the Azure SSML path executes without any code change. Verify the attachment is a valid audio file.

10. **Memory persistence**
    After a published episode query `x_snc_podcast_memory`. Confirm:
    - One `episode` record, one `debate` record, one `cta` record created for this Script
    - `expires_on` dates match configured retention days
    - Backdate a record's `expires_on` to yesterday; run `new PodcastMemoryManager().pruneExpiredMemory()` in a background script; confirm the backdated record is deleted

11. **Cost tracking**
    After a published episode confirm:
    - `script.claude_input_tokens`, `script.claude_output_tokens`, `script.tts_characters` are all > 0
    - One `x_snc_podcast_usage` record exists for this episode with `estimated_cost_usd` > 0 and `cost_center` matches `theme.default_cost_center`

12. **Scheduled run**
    Set `config.schedule_enabled = true` and `config.schedule_time` to 2 minutes from now. Wait. Confirm:
    - Flow triggers automatically
    - Episode completes with `state = published`
    - No errors in `script.generation_log`
