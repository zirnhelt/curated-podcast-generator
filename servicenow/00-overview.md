# ServiceNow Port: Curated Podcast Generator

## Context

The `curated-podcast-generator` is a Python pipeline that generates a daily AI-hosted podcast from RSS news feeds. It uses Claude to write a two-host dialogue script, OpenAI/Azure for TTS audio, and GitHub Actions for scheduling. This port moves that pipeline to ServiceNow so that:

- ServiceNow **records from any application domain** (ITSM, HRSD, CRM, SecOps, ITAM, DevOps, etc.) replace the RSS news feed as content source
- The deterministic orchestration moves into a Flow Designer flow
- All configuration (hosts, themes, TTS providers, episode segments) lives in ServiceNow tables
- Episode format is a composable **recipe of segments** (welcome, roundup, KPI snapshot, spotlight, deep dive, closing) configured per theme — not a binary format choice
- TTS provider is admin-selectable from a configured set

The design is deliberately domain-agnostic: all domain knowledge (which table, which fields, what order) lives in platform-standard `sys_report_source` records and KPI signal configurations — not in application code. A team can create an HRSD case review podcast, a SecOps threat briefing, or a DevOps pipeline summary by configuring themes and report sources, with no code changes.

---

## Application Identity

- **Scope:** `x_snc_podcast`
- **Source layout:** `servicenow/` directory in this repo
- **Delivery:** ServiceNow scoped application, source-controlled via Studio

---

## Source Files to Port From

| Python file | Maps to |
|---|---|
| `podcast_generator.py` | All Script Include logic (the entire pipeline lives here) |
| `config/prompts.json` | Prompt templates in `PodcastScriptGenerator` — adapt for task data instead of RSS articles |
| `config/hosts.json` | Seed data for `x_snc_podcast_host` (Riley + Casey) |
| `config/themes.json` | Informs `x_snc_podcast_theme` field design |
| `azure_tts.py` | SSML multi-talker format in `PodcastTTSIntegration._generateAzure()` |

---

## Document Index

| File | Contents |
|---|---|
| `01-app-structure.md` | Directory layout and file list |
| `02-table-schemas.md` | All 9 table definitions with field-level notes |
| `03-flow.md` | Flow Designer flow stages 1–9 |
| `04-script-includes.md` | All 6 Script Include signatures and logic notes |
| `05-properties-and-aliases.md` | System properties + Connection Alias definitions |
| `06-seed-data.md` | Hosts, themes, starter templates, TTS provider, config |
| `07-implementation-order.md` | Build sequence + anticipated challenges |
| `08-verification.md` | 12-step end-to-end test checklist |
