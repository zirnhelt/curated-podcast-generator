# Part 7: Implementation Order

Build in this sequence — each step depends only on what came before it.

1. **Tables** (all 9) with seed data. Everything else depends on these existing.
2. **System properties** — lightweight, no dependencies.
3. **Connection Aliases** — required before any Script Include makes a REST call. Add credentials in the Connection & Credential Stores after alias creation.
4. **`PodcastMemoryManager`** — pure GlideRecord logic, no REST. Standalone unit-testable.
5. **`PodcastUsageTracker`** — pure GlideRecord logic, no REST. Can be tested against a Script record with dummy token counts.
6. **`PodcastTaskFetcher`** — pure GlideRecord logic. Test with `new PodcastTaskFetcher().fetchAllSegments(themeSysId, new GlideDate(), accessContext)` in a background script against real seed data.
7. **`PodcastScriptParser`** — pure text parsing + GlideRecord inserts. Test by passing a sample Claude-formatted script string.
8. **`PodcastScriptGenerator`** — first REST-dependent component. Needs Claude alias + credentials live.
9. **`PodcastTTSIntegration`** — test one provider at a time (start with OpenAI as it is the simplest).
10. **Flow Designer Flow** — wire up all Script steps. Test by manually triggering on an existing Script record with `state = draft`.
11. **Scheduled Trigger** — set `config.schedule_enabled = true` and `schedule_time` only after the flow passes the full verification checklist in `08-verification.md`.

---

## Anticipated Challenges

**Large text fields**
Script text can exceed 30 000 characters. Use Long Text (journal-type) fields. Be careful with GlideRecord `setLimit()` behaviour on journal fields — test early.

**Audio attachment size**
A 20-minute episode at 128 kbps ≈ 18 MB. Fits the default 50 MB SN attachment limit. If storing externally, configure `audio.external_base_url` before testing TTS.

**`sn_cc.SNHttpClient` plugin**
Requires `com.glide.sn_connect_spoke.scope`. Verify it is active on the target instance before writing any Script Include that calls a REST API.

**Line endings**
`PodcastScriptParser` must handle both `\n` and `\r\n` line endings from Claude responses. Split on `/\r?\n/`.

**KPI signal sys_ids in seed data**
`06-seed-data.md` uses placeholder references for KPI signal sys_ids. Replace them with actual sys_ids from the target instance's KPI signal store before publishing seed records.

**Access mode — impersonation**
`GlideRecordSecure` respects the current session's ACLs. For `general` and `role_based` modes the Script step should set the session user to `accessContext.serviceAccountSysId` using `GlideSession.setCurrentApplicationId()` or the appropriate scoped equivalent — do not use `gs.getUser().impersonate()` in production without security review.
