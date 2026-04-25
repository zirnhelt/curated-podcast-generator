# Part 6: Seed Data

---

## Hosts — 2 records (from `config/hosts.json`)

**Riley**
- `host_key: riley`, `debate_stance: optimist`, `pronouns: she/her`
- `voice_openai: nova`, `voice_azure: en-US-Ava:DragonHDLatestNeural`
- `azure_style: cheerful`, `azure_style_degree: 1.3`

**Casey**
- `host_key: casey`, `debate_stance: skeptic`, `pronouns: they/them`
- `voice_openai: echo`, `voice_azure: en-US-Andrew:DragonHDLatestNeural`
- `azure_style: newscast-casual`, `azure_style_degree: 1.1`

Copy `short_bio`, `full_bio`, `debate_style`, `recurring_questions`, `consistent_interests` verbatim from `config/hosts.json`.

---

## Themes — fully seeded

Two themes are fully seeded with matching report sources. All others are starter templates requiring only a `sys_report_source` to activate.

### Theme 1: "Daily Ops Briefing" (ITSM)
- `persona_description: "IT operations briefing — crisp, operational, on-call team audience"`
- `access_mode: general`
- Segments:

| seq | type | label |
|---|---|---|
| 10 | `welcome` | Welcome |
| 20 | `kpi_snapshot` | Ops KPIs |
| 30 | `news_roundup` | Incident Roundup |
| 40 | `psa` | Team Spotlight |
| 50 | `closing` | Closing |

- Seeded `sys_report_source` for seq 30: table `incident`, filter `active=true^priority<=2`, order `priority ASC^sys_updated_on DESC`
- `kpi_signal_list` for seq 20: `[sys_id of MTTR signal, sys_id of SLA breach rate signal, sys_id of open P1 count signal, sys_id of change success rate signal]` — replace with actual signal sys_ids from the target instance

### Theme 2: "Weekly Problem Review" (ITSM — deep dive)
- `persona_description: "Post-incident systemic analysis — thoughtful, investigative, engineering audience"`
- `access_mode: general`
- Segments:

| seq | type | label |
|---|---|---|
| 10 | `welcome` | Welcome |
| 20 | `news_roundup` | Recent Incidents |
| 30 | `psa` | Knowledge Share |
| 40 | `deep_dive` | Problem Deep Dive |
| 50 | `closing` | Closing |

- Seeded `sys_report_source` for seq 20: table `incident`, filter `state=6^resolved_at>=javascript:gs.daysAgo(7)`, order `sys_updated_on DESC`
- Seeded `sys_report_source` for seq 40: table `problem`, filter `state!=4`, order `opened_at ASC`

---

## Starter Templates

Theme + segment records seeded; admin creates a matching report source to activate. Zero code changes required.

| Theme | Domain | Roundup table | Deep dive table | PSA type |
|---|---|---|---|---|
| HR Case Digest | HRSD | `sn_hr_case` (open, by priority) | `sn_hr_case` (oldest open) | `knowledge_share` |
| Vulnerability Briefing | SecOps | `sn_si_incident` (open, critical) | `sn_si_vulnerability` (unmitigated) | `announcement` |
| Asset Lifecycle Review | ITAM | `alm_asset` (nearing end-of-life) | `alm_asset` (end-of-life overdue) | `success_story` |
| Pipeline Health Digest | DevOps | `rm_story` (failed/blocked) | `rm_release` (at-risk) | `team_spotlight` |
| Customer Case Roundup | CRM | `sn_customerservice_case` (open, high) | `sn_customerservice_case` (oldest) | `success_story` |
| Change Advisory Briefing | ITSM | `change_request` (upcoming this week) | `change_request` (risk-scored high) | `announcement` |

---

## TTS Providers — 1 default record

```
name: OpenAI TTS
provider_key: openai
model: tts-1
max_chars_per_request: 4096
is_default: true
audio_format: mp3
request_template_json: {"model":"tts-1","input":"{{text}}","voice":"{{voice}}"}
connection_alias: x_snc_podcast_openai_tts_alias
```

---

## Config — 1 singleton record

```
name: Default
auto_theme_selection: true
schedule_enabled: false        ← enable only after full end-to-end test passes
audio_storage_attachment: true
max_retries: 3
```
