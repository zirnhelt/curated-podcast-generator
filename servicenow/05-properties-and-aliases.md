# Part 5: System Properties & Connection Aliases

---

## System Properties

All prefixed `x_snc_podcast.` in the app's property category.

```
claude_script_model                  string   claude-sonnet-4-6
claude_polish_model                  string   claude-sonnet-4-6
claude_summary_model                 string   claude-haiku-4-5-20251001
claude_max_tokens                    integer  8000

news_anchor.max_items                integer  12
news_anchor.age_threshold_hours      integer  24
deep_dive.max_items                  integer  5
deep_dive.min_age_hours              integer  72

memory.episode_retention_days        integer  21
memory.debate_retention_days         integer  90
memory.cta_retention_days            integer  365

tts.default_provider_key             string   openai
tts.azure_region                     string   eastus  (substituted into Azure base URL at call time)

audio.storage_type                   choice   attachment | external_url
audio.external_base_url              string   (empty)

access.default_mode                  choice   general

log_level                            choice   info | debug | warn | error

cost.claude_input_per_1k_tokens      decimal  0.003
cost.claude_output_per_1k_tokens     decimal  0.015
cost.tts_per_1k_chars                decimal  0.015
```

KPI signal definitions live in the platform KPI signal store, not here.
Report source definitions live in `sys_report_source`, not here.

---

## Connection Aliases

| Alias name | Base URL | Auth type | Credential name |
|---|---|---|---|
| `x_snc_podcast_claude_alias` | `https://api.anthropic.com/v1` | API Key Header (`x-api-key`) | `AnthropicApiKey` |
| `x_snc_podcast_openai_tts_alias` | `https://api.openai.com/v1` | Bearer Token | `OpenAIApiKey` |
| `x_snc_podcast_azure_tts_alias` | `https://{region}.tts.speech.microsoft.com` | API Key Header (`Ocp-Apim-Subscription-Key`) | `AzureSpeechKey` |
| `x_snc_podcast_elevenlabs_alias` | `https://api.elevenlabs.io/v1` | API Key Header (`xi-api-key`) | `ElevenLabsApiKey` |

Azure region is substituted at call time from `x_snc_podcast.tts.azure_region`.

All REST calls use `sn_cc.SNHttpClient` with the relevant alias — credentials never appear in code.

**Prerequisite:** The `com.glide.sn_connect_spoke.scope` plugin must be active on the target instance. Verify before building.
