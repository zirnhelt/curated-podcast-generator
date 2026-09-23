# Gemini TTS

*Moved verbatim from CLAUDE.md on 2026-09-23. CLAUDE.md keeps the standing rules; this file keeps the incidents and the reasoning behind them. Read it before changing the code it describes.*

## Status: parked (2026-09-23)

OpenAI `tts-1` is the nightly provider. The Gemini code, its tests and the TTS Eval
workflow stay, and a `tts_provider=gemini` dispatch still works. While parked,
nothing about Gemini is tuned, refitted or extended.

**Why.** Two rewrites of the delivery direction and a voice recast did not fix the
sing-song read (2026-09-15/16, below). Roughly a third of the podcast's commits from
July to September went into keeping Gemini on the air.

**Exit criterion: unpark only when both hold.**

1. **It sounds better, blind.** Render the same deep-dive section on both providers
   (TTS Eval workflow, `providers: all`, or `python evaluate_tts.py --section deep_dive`).
   Listen without knowing which is which. Gemini is preferred, on two separate days.
2. **It answers.** `--probe-models` on the backend you would use comes back 6/6. The
   estimated full-episode render also fits inside `GEMINI_RENDER_DEADLINE_S`.

Until then, the rest of this file is history and reference.

### Per-chunk checksums (Gemini)

A Gemini section is 1–3 independent chunks joined into one clip, and every guard around it
used to run on the wrong quantity or in the wrong place. On 2026-09-06 that shipped both
halves of the same defect in one episode: a welcome section that rendered **8 s of speech for
113 words** and a news-roundup chunk that came back at **67% of its expected length**. Both
passed everything.

- **The ratio measures speech, not wall length** (`_duration_ratio` → `_trim_pcm_silence`).
  The welcome's response was long enough to clear both thresholds and was mostly dead air,
  which the assembler then trimmed off — so the check saw a healthy clip and the listener got
  eight seconds. Measured on the trimmed span it reads 0.18 and `SEVERE_TRUNCATION_RATIO`
  retries it.
- **Dead air inside a chunk is its own defect** (`MAX_INTERNAL_SILENCE_MS`, 4 s). It is the
  one the assembler cannot repair: `trim_tts_silence` touches a section's head and tail, and a
  chunk's own trailing silence lands in the middle of the section. Raised rather than spliced
  out — a hole that long means the words are gone too, and a fresh sampling draw beats a hard
  cut. The script's `[pause:N]` tags never reach Gemini (`_extract_pacing_tag` parses them into
  inter-turn gaps first), so nothing legitimate approaches the threshold.
- **Each chunk is trimmed before it joins its neighbours**, which is what keeps a chunk
  boundary a boundary instead of a hole.
- **A wholly silent chunk trims to `b""`** and therefore reads as a 0 ratio. `_is_silent_take`
  only ever sees the assembled section, so one dead chunk of three could always pass it.
- **The soft 0.80 check still does not retry, and no longer only prints.** Brisk banter
  genuinely renders faster than the flat 400 ms/word estimate, so it is expected to trip
  sometimes — but a `print` reaches neither the run report nor the roadmap ledger, and the
  only record of the 2026-09-06 omission was the audio itself. It appends to `_degradations`,
  drained as `render/gemini-take` (renamed from `render/gemini-retry`: the rows are about what
  the take was, not only about how many rungs it took).

**Gemini multi-speaker TTS (optional, `USE_GEMINI_TTS=1`):** `gemini_tts.py` renders each section's whole two-host conversation in one `generateContent` call (NotebookLM-style prosody) via REST — needs `GEMINI_API_KEY`; `GEMINI_TTS_MODEL` overrides the default (`gemini-3.1-flash-tts-preview`). A style prompt plus whitelisted `[tag]` stage directions live in `config/prompts.json` under `gemini_tts`; the polish pass only adds tags when Gemini is active, and the OpenAI/Azure paths (and both published transcripts) strip them. Credits on every surface resolve through `_compose_tts_credit()` — **every** provider that actually rendered audio is named, in render order, so a mid-episode fallback reads as "Gemini TTS and OpenAI TTS" rather than picking one. `get_active_tts_provider()` is the *routing* answer (what renders next), which is a different question and must not be used for a credit. Compare providers with `python evaluate_tts.py`.

### The prompt says where the speech starts

The request is scaffolded rather than prose-led — `### AUDIO PROFILE` (one line per
speaker, from `hosts.json`'s `gemini_audio_profile`), `### PERFORMANCE NOTES` (the
config's `style_prompt` plus the speaker and tag rules this call generates), then
`#### TRANSCRIPT` and nothing but speech below it. Every failure this endpoint has cost
the show is a boundary failure: the cold open read aloud twice (see `CONTINUATION_NOTE`),
a stage direction spoken as dialogue. The marker is the boundary, so the model never has
to infer one from a colon at the end of a sentence.

**That collision happened on 2026-09-14 and the profile lines are gone.** The episode read
the hosts' own personality descriptions out on air more than once, and the render log rules
out every other explanation: twelve cloud calls on `gemini-2.5-flash-tts`, all answered on
the first attempt, no rung climbed, no model swapped, no degradation recorded. Nothing
failed — the request asked for it. `Riley: Host. Earnest and intense…` is a transcript turn
on studio and a flattened `multiSpeakerMarkup` turn on cloud, and the cloud backend is the
worse place for it: there is no transcript in the prompt at all, so the profile lines were
the only thing in the request shaped like speech.

**A second pointer aimed the model straight at them.** The speaker sentence read "alternating
exactly as the speaker labels *below* set it out" — written for studio, where labels do sit
below. On cloud the turns ride structured in `multiSpeakerMarkup`, so there was nothing
below, and the only labels anywhere in the request were the profile lines above. Direction
copy that describes the studio layout is a live hazard on the other backend; keep it
backend-neutral.

Direction lines are now `- Delivery for Riley — …`, which keeps the binding to the pinned
voice and cannot parse as a turn, and the header says so (`### AUDIO PROFILE — direction,
never spoken`). **No line in either backend's direction may begin with a speaker name** —
there is a test sweeping both.

**Describe the register, never the register to avoid.** The same episode was sing-song
against a script that is anything but. The direction spent eight negations on it — "Never
peppy, bubbly, perky or bright", "Never laid-back, breezy or slangy", "No morning-show DJ
energy: no hype, no exaggerated laughter, no radio-announcer voice" — and the words a TTS
model actually receives are *peppy, bubbly, perky, bright, breezy, hype, laughter,
announcer*, which is a fair description of what shipped. This is the prompt-teaching-the-tic
failure from the AI-tells section, on the audio side: a ban list written in the register it
bans. `style_prompt` and both `gemini_audio_profile`s now say what the delivery *is*, and
name the intonation positively ("pitch falls at the end of a statement and stays in a narrow
range"). A test asserts the burned adjectives stay out of the request.

**That rewrite did not work, and what it rules out is worth more than what it fixed.**
2026-09-15 and 2026-09-16 — the first two episodes rendered after it merged — both came back
sing-song, bubbly and uptalked. The render logs say the direction was never the variable:
twelve `gemini-2.5-flash-tts` cloud calls an episode plus the canary, every chunk at **rung
0** with the style block and audio profile intact and byte-identical, **zero retries on
09-15** and one context-dropping retry on 09-16, no model swap, no degradation touching the
prompt. The script is not asking for it either — across the sixteen scripts of September
2026, **1 122 of 1 168 host turns end in a period** and 36 in a question mark, and not one
turn in the month carries an exclamation mark. So: flat copy, correct terminal punctuation,
a direction that asks in plain words for a falling terminal and a narrow range, delivered
unshed on every single request — and the model sings anyway. **On this surface the style
prompt is a nudge, not a lever**, and a third rewrite of the adjectives is the move that has
now failed twice. The lever left is the prebuilt voice, then the provider.

- **The cue whitelist was the half of the direction that *was* asking for it.** `warmly`,
  `curiosity` and `soft laugh` are performed per turn, inside the speech stream, which makes
  them the closest and most obeyed direction in the whole request — and every one of them
  asks for lift the style prompt above then has to argue against. `[warmly]` was also the
  `tag_instruction`'s worked example, so it rode in the prompt of *every* request whether or
  not a script used it, the same way `[short pause]` taught itself on 2026-09-13. All three
  are retired to `legacy_whitelist` (strip-only — 200+ scripts on disk carry them, and an
  unexplained cue gets read aloud), and the live list keeps only cues that do not brighten:
  `thoughtfully`, `slow`, `fast`, `sighs`.
- **The broadcast frame is a burned word too.** `style_prompt` opened "Two longtime co-hosts
  on **community radio**" — the announcer prior the eight retired adjectives only *described*.
  It is "two people talking to each other … at the volume of a kitchen table" now, with the
  intonation rule promoted to the first bullet, and `radio` / `co-host` added to the test's
  burned list along with the retired cues. The sweep also runs on the **studio** prompt and
  on the live whitelist now; it covered the cloud prompt alone, which is why `[warmly]` sat
  in every request of both post-fix episodes with a test watching.
- **Pick the next voice by ear, not by adjective** — `evaluate_tts.py --probe-voices`
  (TTS Eval workflow, `probe_voices`, with an optional `voice_pairs` override) renders the
  section's first chunk through each candidate pair and uploads the WAVs. `set_voice_override`
  exists for it and nothing else. Google's one-word voice labels (Kore *Firm*, Iapetus
  *Clear*, Schedar *Even*, Charon *Informative*, Gacrux *Mature*) are why a pair is on the
  candidate list and are not evidence about it — the same rule as a model preview: it is not
  measured here until it is measured here. Listen for the terminal of a declarative: it
  should fall and stay fallen. The winner is pinned in `hosts.json` `gemini_voice`.
- **Changing the voice changes who the hosts sound like**, which the ordering rule elsewhere
  in this section calls the last thing to degrade. That ordering was written for *automatic*
  fallbacks inside a render. It does not bind a deliberate, auditioned recast — and after two
  prompt rewrites it is the cheapest remaining option that does not mean leaving Gemini.

**Speaker order is canonical, not first-to-speak** (`_ordered_speakers`). It was
`dict.fromkeys(seg["speaker"] …)`, so the chunk's opener led — and 2026-09-14 alternated
`Riley=Kore, Casey=Iapetus` with `Casey=Iapetus, Riley=Kore` across its twelve calls,
reordering the profile lines, the voice-config array and the "between X and Y" sentence with
it. The voices were never wrong (they bind by name) and the personalities still drifted
segment to segment, which is the complaint it answers. Cloud pins no `seed` and no
`temperature`, so **byte-identical direction on every chunk is the only consistency lever
the backend leaves** — anything that varies the prompt per chunk is varying the performance.

The direction block is 1 179 of the 1 200 bytes `CLOUD_PROMPT_BYTE_RESERVE` holds back, so
there is ~20 bytes of room. Growing it is not free in the obvious direction either: raising
the reserve shrinks every chunk, which buys *more* independent sampling draws, which is the
drift above. Trim before you raise.

Cues are inline `[thoughtfully]` tags now, from the documented vocabulary
(`whitelist`), not invented ones — custom tags read flatter. Hype tags
(`cheerfully`, `enthusiasm`, `gasp`) are deliberately not in it: the show has no
morning-DJ register to reach for. `legacy_whitelist` is strip-only, so the
`(wry)`-style parentheticals in every script already on disk still get cleaned on a
re-render.

**A duration cue is not a manner cue, and the model reads it out.** `short pause` and
`long pause` were in the whitelist, the `tag_instruction` used `[short pause]` as its
worked example, and on 2026-09-13 the episode aired several spoken "short pause"es. Every
other cue names *how* to say the next words; these two name an absence, so there is nothing
to perform and the text is all that is left. They are retired to `legacy_whitelist` rather
than reworded, because **pacing is already the assembler's job**: `_extract_pacing_tag`
parses the script's own `[pause:N]` tags into real inter-turn gaps before TTS sees them, so
the word forms were a second channel for something the pipeline already did exactly.
`strip_retired_stage_directions` drops them on the cue-*keeping* rungs too — the whitelist
is what the prompt explains, so any cue outside it is unexplained text no matter which rung
is running, and 200+ scripts on disk carry them. **The never-speak-a-tag rule is not in `style_prompt`** — it rides with the
tags (`tag_instruction`), so the rung that sheds the style cannot ship tags with nothing
saying they are direction.

**OpenAI is the nightly default again (2026-09-17).** Gemini's multi-speaker delivery
still reads as too sing-song after two prompt rewrites and a voice recast (see above), so
the daily workflow's `tts_provider` input default flipped back from `gemini` to `openai`
and `USE_GEMINI_TTS` no longer treats an unset input as `gemini` — a scheduled run (the
cron backstop) passes no input at all, and only an explicit `tts_provider=gemini` dispatch
enables Gemini now. Re-audition Gemini's voices later with `evaluate_tts.py` before
flipping the default back.

**The code default is `gemini-3.1-flash-tts-preview`, and it has never answered here.**
It was made the default without the probe this section asks for, and every run from
2026-09-01 spent two 45 s canary read timeouts on it (1082 chars, 45.1 s, unanswered,
twice each night, identical on both dates) before pinning
`GEMINI_TTS_FALLBACK_MODEL` for the show. **Production overrides it via the
`GEMINI_TTS_MODEL` repository variable, set to `gemini-2.5-flash-preview-tts`, and that
works**: on 2026-09-03 the canary passed on the first candidate in 3.4 s.

**Setting that variable then collapsed the model ladder, which is the trap to know
about.** `gemini-2.5-flash-preview-tts` was also the hard-coded default of
`GEMINI_TTS_FALLBACK_MODEL`, so primary and fallback named one model, the canary printed
one candidate, and `_other_model()` returned `None` on every attempt — the same hole the
`_model_override` pin opens, reached from the configuration side and just as silent. All
four attempts on that day's cold open re-asked the same model unchanged and the episode
went to OpenAI. `GEMINI_TTS_FALLBACK_MODEL` is therefore resolved against the primary
(`_default_fallback_model`, preference order `2.5-flash` then `2.5-pro`) rather than
hard-coded, and a candidate list of one now `degrade()`s from `canary()` whether or not
the render goes on to succeed.

**Price is not a reason to keep the ladder one model short.** Pro TTS was demoted out of
the fallback slot for costing more than flash; measured 2026-09-03 that holds only for
*input* tokens ($1.25 vs $0.50 per MTok) while **audio output is $10 per MTok on both** —
and audio output is essentially the whole bill. An episode sends ~2.5k input tokens, so
choosing pro over flash as the second model costs a fraction of a cent. `3.1` is the one
that is genuinely more expensive ($1.00 in / **$20** out) and it is also the one that has
never answered, which is why it is not in the preference order.

Before making any new preview the primary, **run `python evaluate_tts.py --probe-models`
(TTS Eval workflow, `probe_models` input) against the 8/15 baseline** and record the
numbers here — the reason 3.1 ran unexamined for weeks is that nobody had a measurement
to argue with. Note the probe only ever measures the two configured models: it cannot
discover a better one, so widening the field is a decision made here, not by the tool.
Check the pinned `speechConfig` voices (`Kore`/`Iapetus` in `hosts.json`) carry over —
voice identity is the last thing to degrade — and refit `READ_TIMEOUT_MS_PER_CHAR` off
the probe's slowest column while the data is in hand.

### The reliability lever is the endpoint — the `GEMINI_TTS_BACKEND` switch

The persistent-failure story above is a property of the *surface*, not the prompt: all
three Gemini **API** TTS models (`2.5-flash-preview-tts`, `2.5-pro-preview-tts`,
`3.1-flash-tts-preview`) are preview on `generativelanguage.googleapis.com` — no SLA,
tighter rate limits, two weeks' notice before withdrawal, and the 500s and read timeouts
this whole section is about. Cloud Text-to-Speech serves Gemini-TTS as **GA** on
`texttospeech.googleapis.com`: the same prebuilt voices (`Kore`/`Iapetus`), a separate
quota pool with requestable limits, a real SLA. That is the only change that alters the
*reliability* rather than re-rolling the same dice, so it is wired as a backend switch, not
another model.

- **`studio`** (default) — `…:generateContent`, API-key auth (`GEMINI_API_KEY`). Unchanged;
  every failure mechanism above lives here.
- **`cloud`** — `v1beta1 text:synthesize`, **service-account** auth
  (`GOOGLE_APPLICATION_CREDENTIALS`). Multi-speaker on Cloud TTS is served *only* by this
  Google-Cloud backend — the API-key path does not offer it. The transcript rides structured
  (`input.multiSpeakerMarkup.turns`) with the direction in `input.prompt`, so nothing in the
  prompt is speakable: the boundary the studio path has to draw with a `TRANSCRIPT_MARKER` is
  drawn by the schema. Shape verified against the v1beta1 proto and Google's multi-speaker
  sample; not run here (no service-account key in the sandbox).

**Only the transport changes.** The canary, retry ladder, model alternation, per-chunk
checksums and degradation plumbing are shared — `_attempt` dispatches to `_studio_synthesize`
/ `_cloud_synthesize` and runs the trim + truncation + silence checks on either — so the
cloud backend inherits the entire safety net. Two differences to know: there is **no
`seed`/`temperature`** (chunk-to-chunk prosody is not pinned the way studio pins it — the
voices still are, by `speechConfig`), and **no `finishReason: OTHER`** (text:synthesize
returns audio or an HTTP error), so only the HTTP/transport rungs of the ladder can fire —
the prompt-shedding rungs are dead weight there, harmlessly.

**Cloud enforces a hard 4 000-**byte** limit on the synthesis input, and that is the one
thing about it that is not a fit.** `400 INVALID_ARGUMENT "Either `input.text` or
`input.prompt` is longer than the limit of 4000 bytes."` Studio has no equivalent, so
`TRANSCRIPT_CHAR_LIMIT` (3 000) is a *latency* number free to move when the endpoint is
remeasured, while `CLOUD_INPUT_BYTE_LIMIT` is a wall no rung, retry or model change gets
past. They are two constants for that reason — a future studio refit must not be able to
reach across and break cloud's ceiling — and `_segment_cost` / `_chunk_limit` pick the
active backend's.

- **Bytes, not chars.** Secwépemc, Tŝilhqot'in, em dashes and curly quotes are 2–3 bytes
  each, so a char budget over-states what fits by up to 7% on a short turn. The chunker
  measures the encoded turns off the real payload rather than applying a fudge factor.
- **The prompt is reserved, which assumes the stricter of two readings.** The message names
  `input.text` and `input.prompt`, and the payload that met it had no `input.text` at all
  while its `input.prompt` was 1 014 bytes — so the validator measures something it does not
  name (most likely the markup flattened to text) and one 400 cannot say whether the 4 000
  is per field or over the input as a whole. `CLOUD_PROMPT_BYTE_RESERVE` (1 200, covering the
  worst prompt the ladder sends at 1 160) assumes the sum. Measured over ten episodes that
  costs 10.1 requests an episode against 8.1 — two extra sampling draws, 0.4 more than studio
  already makes. The other way round, every chunk 400s and the episode goes to OpenAI whole.
- **The probe was asking a question production never asks.** `_probe_gemini_models` and
  `_probe_gemini_rungs` sent the whole *unchunked* section, which on cloud is past the wall
  before the model is ever reached: on 2026-09-12 `news` read **0/6 on both models** and
  looked exactly like a dead endpoint. Both probes now send the largest chunk
  `_balanced_chunks` would make, and print which it is.
- There is a test sweeping every rung, both `continuing` values, turn lengths down to 30
  chars and the real news/deep-dive size range against the wall — the invariant that would
  have caught this before a probe did.

**Cloud leads with pro (Option B), flash second** — and the reason given for it was wrong.
"Audio output is $10/MTok on both, so pro buys the better dialog for ~free" is the studio
price list; the [Cloud TTS page](https://cloud.google.com/text-to-speech/pricing) charges
**Gemini 2.5 Pro TTS $1.00 in / $20.00 out per MTok against flash's $0.50 / $10.00**, and
audio output is essentially the whole bill. On this surface pro is a **2x** decision, not a
free one.

**Measured on the first all-Gemini episode (2026-09-13, run 34746805021).** Audio tokens
bill at 25/second, so a 20-minute episode is ~30k output tokens including the canary and the
one pro chunk that timed out after being synthesized:

| | pro | flash | OpenAI `tts-1` |
|---|---|---|---|
| audio out | ~30.6k tok @ $20/MTok = **$0.61** | @ $10/MTok = **$0.31** | 18.2k chars @ $15/1M = **$0.27** |
| text in | ~9k tok @ $1/MTok = $0.01 | @ $0.50 = $0.005 | — |
| ~30 days | **~$18.60** | **~$9.20** | ~$8.20 |

**The render clock is the sharper cost.** That episode spent **832 s of the 1 500 s
`GEMINI_RENDER_DEADLINE_S`** on pro — 55%, matching the ~830 s this section predicted — and
one deep-dive chunk burned its full 142 s leash on pro before flash served the same request
in 48.9 s. So pro costs 2x the money *and* half the headroom that keeps a bad night on
Gemini's voices at all. Set the `GEMINI_TTS_CLOUD_MODEL` repository variable to
`gemini-2.5-flash-tts` unless the dialog difference is audible enough to be worth both; the
ladder already treats the slower model as the thing to fall past.

**Default stays `studio` until the probe clears the 8/15 baseline — GA is not "measured
here" until it is measured here.** Probe the cloud surface exactly like a new model, with a
real key: `GEMINI_TTS_BACKEND=cloud python evaluate_tts.py --probe-models --section
deep_dive` (TTS Eval workflow, `backend: cloud`).

**The code default is still `studio`; production is not.** The repository variables now read
`GEMINI_TTS_BACKEND=cloud`, `GEMINI_TTS_MODEL=gemini-2.5-flash-tts`,
`GEMINI_TTS_FALLBACK_MODEL=gemini-2.5-pro-tts` — flash-first, as the cost table above argues
for — and the nightly has been rendering whole episodes there since 2026-09-13. **Read the
render log before reasoning about an episode's audio**, because the `studio` default in this
file is not what shipped: the `[api] … service=gemini-cloud-tts model=… latency=` lines name
the backend, the model and every chunk. On 2026-09-14 that is what separated "the endpoint
is flaky" from "the request asked for it" — twelve calls, twelve first-attempt answers, and
the defect was entirely in the prompt.

**`READ_TIMEOUT_MS_PER_CHAR` was refitted against cloud on 2026-09-12 and did not move —
but not for the reason the first cloud probe suggested.** Two probes, 3 calls per model
each, both **6/6**:

| section | request chars | pro median / slowest | flash median / slowest |
|---|---|---|---|
| `welcome` | 1 903 | 39.2 / 50.7 s | 24.9 / 33.3 s |
| `news` (largest chunk) | 3 560 | 103.7 / 110.4 s | 64.1 / 64.4 s |

**ms/char is not flat in request size on pro** — 26.6 at the small request, 31.0 at the
large one — so fitting off the welcome number alone would have claimed a 1.5x tail margin
that is really **1.29x** at the size that matters. Same over-confidence, from the same
source, as the studio fit measured on one take. flash is nearly flat (17.5 → 18.1) at 2.2x.

40 stays anyway, and this is the first evidence for it that is not inherited: **cloud's
latency is predictable where studio's was not.** The three pro calls spread 9.6%
(100.3 / 103.2 / 109.9 s) and the three flash calls 5.4%, against studio's r² = 0.14 and two
similarly-sized calls 5.4x apart. A 1.29x margin on a tight distribution is worth more than
1.55x on a long tail — the case for the GA surface, restated as a number.

**What to watch is the render deadline, not the leash.** Pro at 29.1 ms/char median puts a
full episode (~28 400 request chars) at **~830 s of the 1 500 s `GEMINI_RENDER_DEADLINE_S`**,
against ~510 s on flash. Pro is not timing out — it is spending over half the render's Gemini
budget on a clean night, so one exhausted ladder can push the episode past the deadline. The
lever then is the `GEMINI_TTS_CLOUD_MODEL` repository variable (set it to
`gemini-2.5-flash-tts`), not the leash constant.

The leash's *scale* also differs by backend: cloud's request is the spoken turns plus a
direction-only prompt, so its largest chunk prices ~156 s against studio's 191 s, and
`READ_TIMEOUT_MAX_S` / `SECTION_BUDGET_S` stay non-binding on both — asserted in tests, per
backend.

**Nightly cutover is a variable flip, not a code change** — the plumbing is default-off. Set
repository variable `GEMINI_TTS_BACKEND=cloud`, add secret `GEMINI_TTS_CLOUD_SA_KEY` (a
service-account key with the Text-to-Speech API enabled), and — because cloud model names
have no `-preview` suffix — optionally pin `GEMINI_TTS_CLOUD_MODEL` /
`GEMINI_TTS_CLOUD_FALLBACK_MODEL` (unset uses pro/flash). `daily-podcast.yml` writes the key
to `GOOGLE_APPLICATION_CREDENTIALS` only when the backend variable is `cloud`; a missing
secret warns and falls to OpenAI rather than costing the episode.

Google's Interactions API is now GA with `generateContent` marked legacy for speech, but it
is still on `generativelanguage.googleapis.com` — the same quota pool as `studio`, so it is a
request-shape change, not a reliability one. Port to it only if a probe shows the
`generateContent` shape (not the surface) is holding the model back; the cloud backend is the
surface change.

### Getting Gemini through a whole episode

An episode is 6–9 independent Gemini calls, so per-call reliability compounds — in the week of 2026-08-01, seven of seven episodes fell back to OpenAI at or before the welcome section, and three shipped a Gemini cold open with an OpenAI show. Four mechanisms exist to stop that, in the order they fire:

- **Canary (`gemini_tts.canary()`).** One tiny throwaway synthesis before any audio exists, run from `generate_audio_from_script`. A failed canary pins OpenAI up front, so a provider that is down costs one tiny call rather than a section's whole retry ladder — on 2026-09-02 that ladder spent 290 s of the render learning what a 3 s probe would have said. It probes the fallback model too, and pins it only if the primary is the one that's down.
  **It does not make a mixed-voice episode unrepresentable, and it never did.** It front-runs only the *pre-render* case; the per-section fallback still leaves already-rendered sections in the earlier provider's voice, which is what 2026-09-01 shipped (Gemini cold open and welcome, OpenAI from the news on). **A mixed episode is an accepted outcome** — re-rendering good audio to force one voice spends the render clock and the OpenAI budget on nothing a listener asked for. What is *not* acceptable is a mixed episode that does not say so: `record_tts_render()` tracks every provider that spoke and `_compose_tts_credit()` names all of them, on the spoken credits, the citations sidecar and the episode description alike. Each candidate gets `CANARY_ATTEMPTS` probes, but only against a failure carrying no verdict — the same rule `_carries_no_shape_verdict` applies to the ladder. Every canary failure of the week of 2026-08-17 was a read timeout against an endpoint the 2026-08-13 probe had measured at 8/15 calls answering, so a single attempt was a coin flip that moved whole episodes onto OpenAI's voices; a tokenized rejection is still taken at its word and never re-asked.
  **The probe has to ask the question the render asks.** It was one single-speaker turn at a 30 s leash, vouching for multi-speaker sections that get 75–120 s: on 2026-08-28 it passed and the same model then failed three multi-speaker sections in a row, so the episode was pinned to a provider that could not render it. `CANARY_SEGMENTS` is now two turns, one per speaker (still under the 10 words `_duration_ratio` needs before it will judge a clip), and `CANARY_READ_TIMEOUT` is `READ_TIMEOUT_MIN_S` — **a canary must never be stricter than the render**, or it fails endpoints the render would have waited out. That coupling means raising the floor raises what a *dead* night costs before the render starts: at 75 s, two candidates × `CANARY_ATTEMPTS` is ~310 s against ~190 s at 45. It stays coupled anyway — a probe that gives up sooner than the render is the more expensive mistake, because it spends the whole episode's voices rather than five minutes.
  **It still only vouches for the multi-speaker shape**, and the cold open is usually one turn, which takes the `singleSpeakerVoiceConfig` branch: on 2026-09-02 the probe passed in 3.3 s and the single-speaker cold open that followed was rejected twice. Probing both shapes was considered and left out — a rung-0 rejection is not a dead provider (the same section succeeded one rung later on 2026-09-01), so vetoing Gemini on one would cost more Gemini days than it saves. `_ladder_summary()` names the shape in the degradation instead: the same information, on the day it matters, for no extra call.
- **Retry ladder (`RETRY_LADDER`).** Each rung changes the *shape* of the request, not just the seed — `finishReason: OTHER` returns `promptTokenCount == totalTokenCount`, i.e. a rejection of what was asked, which reseeding cannot fix. Rungs shed the continuation note, then the audio profile and style block, then the tags. Backoff (0/15/45/90/90 s) is sized to outlast the minutes-long capacity windows the old 5 s/10 s ladder always died inside.
- **Model ladder.** `GEMINI_TTS_FALLBACK_MODEL` is tried at rung 3, *before* the primary model with a bare transcript: voices are pinned by `speechConfig` on every rung, so a model change keeps the hosts sounding like themselves while a stripped prompt loses the direction. It resolves to whichever of `2.5-flash` / `2.5-pro` the primary is not, so this rung exists no matter what `GEMINI_TTS_MODEL` names — the 2026-09-03 collapse is the failure that rule exists to prevent, and it cost a whole episode's voices while every log line looked healthy.
- **Failure-shape routing (`_carries_no_shape_verdict`).** The rung order above assumes a *rejection*. Exactly one failure here is one: `finishReason: OTHER`, which returns `promptTokenCount == totalTokenCount` — accepted, tokenized, refused. A read timeout, a dropped connection, a 429 and a 5xx all carry no verdict on the prompt, so on one the ladder keeps the request's shape and **swaps the model** (`_other_model`, alternating for as long as the budget lasts), and when there is no other model (`_model_override` pinned one, or both env vars name the same one) it re-asks the same full-quality request rather than shedding anything.
  **The model choice is a third axis, not a rung.** It was a forward-only search of the rung ladder (`_next_model_rung`), which could change model exactly once: a transport failure at rung 0 jumped to the first fallback-model rung, and a transport failure *there* found no later rung naming a different model, so the chunk re-asked the fallback for the whole rest of its budget with no way back. On 2026-09-06 the deep dive met one HTTP 500 on the primary at rung 0 and then spent four attempts and 352 s on the fallback — each dying at the full read timeout, on a night the primary had already answered four other chunks. `_synthesize_chunk` now carries a `model_pin` alongside `rung_index`: the rungs own the *shape*, the pin owns the *model*, and a transport failure moves only the second. Alternating costs nothing — the same budget, spent asking both models instead of confirming one. A transport-set pin outranks the rung's `fallback_model` flag for the rest of the chunk, which is safe precisely because the alternation is already covering both. Prompt-shedding is not a retry strategy for a request that was never read: the two shedding rungs cost a full read timeout each and pushed the model rungs out of `SECTION_BUDGET_S` entirely — three timeouts spend 120+15+120+45+120 = 420 s, the budget exactly, which is why every August 2026 episode fell back to OpenAI mid-show and no model rung ever ran. The budget still allows three attempts; the change is *what* they ask, not how many there are.
  **A 429 is a rate limit until proven otherwise — unless it names a spend cap.** Gemini answers an ordinary per-minute throttle with the same 429 `RESOURCE_EXHAUSTED` it uses for a spent quota, and taking it as a verdict gave both canary candidates away on one throttled call each on 2026-08-26 (two of three crons). A genuinely spent quota costs one extra tiny probe before it is believed; refusing to re-ask a rate limit costs an episode its voices. Note the asymmetry with `_billing_wall()` on the *script* side, which must match on credit wording and never on the status code — there the cost of reading a throttle as a wall is a skipped day.
  **The one 429 that is a verdict is a spend cap** (`_is_spend_cap`, `SpendCapError`), and like `_billing_wall()` it is matched on the *wording* — `"Your project has exceeded its monthly spending cap"` — never the status. A capped project refuses every model on it until a human raises the cap or the month rolls over, so no rung, no backoff and no model rung reaches past it: `_carries_no_shape_verdict` returns False for it, the ladder hands the section back immediately, and `canary()` skips the remaining candidates instead of asking the same wall twice per model. On 2026-08-29 that wall cost four probes across two models, and would have cost four a night until Sept 1. The degradation names the cap, because "did not answer the pre-flight check" reads like a flaky endpoint and this one needs a person.
  The 2026-08-13 probe (`--probe-gemini`, welcome section, 3 calls/rung) measured 8/15 calls succeeding, spread evenly across all five rungs — flaky endpoint, not a rejected prompt, and not a dead primary model. That is why a timeout is worth re-asking unchanged, and why the canary's verdict on the primary should be read as "slow right now", not "down".
- **Budgets, and the leash that spends them.** `SECTION_BUDGET_S` bounds one chunk's ladder; `set_render_deadline()` (called with `GEMINI_RENDER_DEADLINE_S`) bounds all Gemini work in a render, so a provider that dies *after* the canary passed cannot eat the 40-minute render step one section at a time. `_budget_allows` reserves the attempt's own read timeout as well as its backoff, so a retry that cannot finish inside the budget is never started.
  **`REQUEST_READ_TIMEOUT` was flat and that is what bounded the ladder's reach.** One 120 s leash covered a 354-char cold open and an 8 500-char chunk alike, so at 420 s a section afforded **two attempts** — and against the measured ~53%-per-call endpoint, two attempts is ~78% per section and **~17% across a seven-section episode**. That arithmetic, not any one outage, is why a whole Gemini episode kept not happening. `_read_timeout_for(segments)` now scales the leash by **request** chars, clamped to `[READ_TIMEOUT_MIN_S, READ_TIMEOUT_MAX_S]` = `[75, 210]`. Five attempts per section takes the episode to ~85%.
  **It scaled on *transcript* chars until 2026-09-07, and that alone explains the shape of the failures.** The endpoint spends its time on the whole request, and the prompt scaffolding is not small — 771 chars for a single-speaker turn, 1 060–1 260 for a multi-speaker chunk (audio profile, performance notes, transcript marker, a `Riley: ` label per turn). So 32–36% of every news and deep-dive request went unbudgeted: about **50 seconds of missing leash**. The floor hides it and the scale only bites past ~1 875 transcript chars, which is exactly the line between the sections that worked and the sections that did not — cold open (333–373) and welcome (669–1 012) price under the floor and succeeded; news and deep-dive chunks (2 200–2 775) got the scaled value and failed. Two chunks on 2026-09-06 settle it: a 3 538-char request leashed at 90 s **answered at 89.8 s**, while a 3 446-char request the same night was leashed at 88 s and cut off at 88.1 s, four times. The leash was set below the slowest *successful* call at its own size. **Budget the request, never the transcript** — `_transcript_chars` still governs chunking, which is right, because that is what the model has to speak.
  **The constants are provisional and instrumented for refit.** Every call logs `latency=` and `limit=` alongside `chars=`, success and failure alike — pair those across a few episodes and refit `READ_TIMEOUT_MS_PER_CHAR`, the same way `_SPEECH_RATE_FITS` was fitted from the transcript sidecars. Nothing recorded latency before, which is how a flat 120 s survived unexamined for the life of the integration.
  **Before refitting it again, know that the fit is weak.** Across the 38 calls logged 2026-09-05..07, request size explained **14% of the variance** in latency among answered calls (r² = 0.14), and two calls within 5% of each other in size came back 5.4x apart (16.7 s vs 89.8 s). Latency on this endpoint is dominated by server-side queueing, not payload. So 40 ms/char is a **tail** margin — ~1.55x the slowest answered call (25.7 ms/char, 55.1 s for 2 144 chars) — not a central estimate, and a refit that fits the mean will re-create the under-leashing this replaced. The honest alternative that r² = 0.14 actually argues for is dropping the scaling entirely for a flat leash sized to the tail; that is the change to weigh next time, rather than a fourth refit of the slope.
  **The floor was the stale half of that fit, and it is what a small section actually gets.** The formula wants 15.9 s for a 398-char cold open and the clamp lifts it, so the floor was never "3x observed" — it was 3x the single take the constants were fitted to. The same request measured **27.9 s** on 2026-09-03 and two of that section's four attempts died at exactly 45.0 s and 45.1 s, so the floor is now **75 s**. This is a hypothesis, and the counter-evidence is in the 2026-08-13 probe: 7 of 15 calls failed against a flat 120 s leash, so a longer wait does not convert every timeout into a take. What it buys is that a slow-but-alive call stops being indistinguishable from a dead one at exactly the leash.
  **The chunk was the other half, and it is the half that was actually failing.** At
  `TRANSCRIPT_CHAR_LIMIT` 8 500 the news roundup was one request every night — 6 382, 6 686,
  7 410, 7 453 and 8 521 chars over the five episodes to 2026-09-05 — and it is the section
  Gemini kept dying in. On 2026-09-05 it went out three times at 6 894 chars and came back
  unanswered at **120.2 s, 120.1 s, 120.2 s**: stopped by the clock every time, never by a
  verdict. The formula wanted 276 s for that request and the clamp handed it 120, so on the
  largest chunk of every episode the fit was not loose, it was **inverted** — and the comment
  claiming the clamp "only ever cuts the small ones" was true of the 8 500-char chunk in
  exactly the wrong direction.
  **Both constants moved together.** `TRANSCRIPT_CHAR_LIMIT` is 3 000, sized to what the
  endpoint has been measured to *answer* rather than what the model will accept: the same
  night's successful calls ran 46–62 chars/s (2 226 chars in 48.0 s and 48.2 s), so a
  ~2 400-char chunk is a ~50 s call with 2x headroom, and the roundup becomes three of them.
  `READ_TIMEOUT_MAX_S` is 210, chosen so the ceiling does not clamp any chunk the render can
  make. There is a test asserting that, because raising the chunk limit without raising the
  ceiling restores the clamp silently, which is the failure that cost a month of episodes
  their voices.
  **The invariant is empirical, not "by construction", and it was mis-measured until
  2026-09-07.** The test computed `TRANSCRIPT_CHAR_LIMIT × MS_PER_CHAR` and passed at a 150 s
  ceiling on exactly the nights every large chunk was under-leashed by ~50 s — it was
  guarding the transcript while the leash is spent on the request. It now measures through
  `_read_timeout_for` over a sweep of real chunk shapes, because scaffolding grows with *turn
  count*: the same 3 000 transcript chars price differently as 11 long turns or 100 short
  ones. The worst chunk across the whole back catalogue (223 scripts) wants 172 s; the sweep's
  synthetic 30-char-turn shape wants 191 s. What is **not** bounded is a chunk of arbitrarily
  many one-word turns, which no script has produced — if the ceiling starts clamping, measure
  that before raising it again.
  The price is extra independent sampling draws — the reason 6 000 was raised to 8 500 in the
  first place — which the pinned seed, low temperature and `speechConfig` voices mitigate. A
  chunk that never returns costs the whole episode its voices, which is the larger price.
  **`_balanced_chunks` chooses the chunk COUNT first, then splits evenly.** Greedy packing
  fills to the limit and leaves the remainder in a runt: 6 382 chars packs to 3 000/3 000/382,
  and that tail is a whole extra request plus an extra sampling draw dropping three seconds of
  differently-sampled audio at the end of the segment. It also does **not** borrow
  `_split_segments_by_char_limit`'s +120-chars-per-segment SSML estimate — that is an Azure
  concern, and counting it charged a 27-turn roundup 3 240 phantom chars and bought two
  requests nobody needed. Gemini is sent plain speech with one fixed prompt block per request.

  **`SECTION_BUDGET_S` moved with it (420 → 540 → 660 → 950), because the budget and the leash trade against each other.** At 420 s a 45 s-leash section afforded four attempts (0+45, 15+45, 45+45, 90+45 = 330 s); at 75 s the same budget affords three, so raising the timeout alone would have bought longer waits by silently spending an attempt. 540 s kept four at a 75 s floor; 660 kept four once a full chunk got a 120 s leash. Moving to request-char budgeting took the worst chunk's leash to 191 s, which 660 affords only twice — hence 950. Two bounds bracket it from opposite sides, both asserted in tests: four attempts at the worst *real* leash must fit (150 + 4×191 = 914 ≤ 950), and four at the *ceiling* must not (150 + 4×210 = 990 > 950), so a request clamped at the ceiling still fails fast. `GEMINI_RENDER_DEADLINE_S` deliberately does **not** move — the trade is tighter now (1 500 s affords ~1.7 exhausted sections rather than 2.3), and that is the right side to be on: a night where two full ladders have already failed belongs to OpenAI, and the deadline saying so sooner is the behaviour it exists for. Watch for `render/gemini-*` degradations that start naming the deadline rather than the ladder. **Move the leash, the chunk limit and the budget together or not at all.**

**Ordering rule:** degrade delivery nuance before voice identity. Anything that changes *who the hosts sound like* is the last resort — a model change (which keeps the pinned `speechConfig` voices) always comes before dropping the show onto OpenAI's. That ordering is why the whole-episode decision is made up front where it can be; it is not a promise that every episode is single-provider, which the per-section fallback has never been able to keep. When the episode does end up mixed, the credit says so.

### Nothing speakable in the prompt that isn't meant to be spoken

Sections used to be primed with the previous section's *verbatim* transcript tail (400 chars) under a `CONTEXT — already spoken immediately before this, do not repeat` header, so delivery continued instead of resampling cold. On 2026-08-17 the welcome section read the entire cold open aloud before its own first line and the episode opened with the teaser twice: 92.8 s of audio for a 969-char transcript, against 65–76 s on the six prior Gemini episodes, an excess matching the 25.5 s cold open.

The prompt shape was the same on all seven days and so was the model, so there is no wording that makes it safe — asking a text-to-speech model not to say words you have handed it is a request it honours most of the time. It is now `continuing: bool` and a fixed `CONTINUATION_NOTE` directive (`gemini_tts`), which carries the same "open mid-flow" intent with nothing quotable in it. **Never reintroduce prior dialogue into a TTS prompt.**

`gemini_tts` cannot import `degrade()` without a circular import, so it records degradations and the render path drains them via `_report_gemini_degradations()`. **A new fallback in `gemini_tts` must append to `_degradations`** or it will not reach the run report.

Two probes, asking different questions — both are `TTS Eval` workflow inputs that write their table to the job summary, and both spend real budget:

- `--probe-gemini` (`probe_gemini`) asks **what shape** Gemini will accept: the same text with progressively less prompt around it. Rung 0 failing while a later rung passes names the element Gemini is rejecting; every rung failing equally is an outage or a quota wall, not a prompt problem.
- `--probe-models` (`probe_models`) asks **which model answers, and how fast**: N real section requests per candidate, reporting answer rate and median/slowest latency. This is the one to run before trusting a nightly to a new model, and its slowest column is what `READ_TIMEOUT_MS_PER_CHAR` should be refitted against.

**`GEMINI_TTS_MODEL` is a repository *variable*, never a secret.** A model name is not a credential, and sourcing it from `secrets` made GitHub mask it everywhere — on 2026-08-28 the log could only say a request went unanswered on `***`, so the run report could not name the model that failed. `canary()` also prints the candidate list before probing it, so a withdrawn or misspelled model name reads as itself rather than as an outage.
