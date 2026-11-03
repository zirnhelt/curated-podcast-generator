---
paths:
  - "podcast_generator.py"
  - "tests/test_podcast_generator.py"
  - "weekly_anchor.py"
  - "native_land.py"
  - "email_ingest.py"
  - "standing_notes.py"
  - "tests/test_ai_tells.py"
  - "tests/test_native_land.py"
  - "tests/test_email_ingest.py"
  - "tests/test_standing_notes.py"
  - "config/prompts.json"
  - "config/ai_tells.json"
  - "config/hosts.json"
  - "config/weekly_anchors.json"
  - "config/indigenous_nations.json"
  - "config/standing_notes.txt"
---
<!-- Moved verbatim from CLAUDE.md on 2026-11-02. Claude Code loads this file when a
     matching path is read or edited. Keep each rule to one or two lines; the story goes
     in docs/decisions/. -->

### Editorial voice — see [docs/decisions/editorial-voice.md](../../docs/decisions/editorial-voice.md)

**Weekly anchor** (`weekly_anchor.py`) — one open question per ISO week, **named on air**, circled by all seven deep dives.
- It is pinned in `weekly_anchor_state.json` on the week's first run; every later run reads it back.
- No repetition by **dimension** (26-week cooldown) as well as by id.
- It frames, never selects.
- The prompt's escape hatch (drop the anchor when the material doesn't reach it) is load-bearing, and tested.

**Naming a nation.** The three house nations describe the Cariboo and nowhere else. Name a nation only for its own territory, and only when a source names it. **Naming the wrong people is worse than naming none.**
- `script/territory-check` (`native_land.py`) only ever disconfirms. A crowd-sourced map may remove a claim; it must never supply one.
- **Detection is whole words, places are whole phrases, and only an unambiguous BC place can disconfirm.** A name in `place_aliases` (Chilcotin, Okanagan, Thompson…) names the people only with a suffix ("Nation", "people"); "Cariboo-Chilcotin" names no one. `podcasts/native_land_cache.json` rides the script commit.
- The land acknowledgment is exempt by construction (`local_places`).
- `NATIVE_LAND_API_KEY` is a secret; degradation rows record only the exception type.

**AI tells** (`config/ai_tells.json`, `podcasts/phrase_ledger.json`).
- A new pattern family goes in `soft_patterns` unless the extra Opus escalation is intended and costed.
- **Never use a burned phrase in prompt prose** (a test enforces it). The model copies its instructions' register.
- The phrase ledger burns spiking adverbs only; multi-word tics go in `hard_banned` by hand.
- The burned-phrases block goes into the dynamic user prompt, never the cached system prompt.
- `script/tell-scrub` runs after the cold open.
- **The cold open gets no example greeting or closer**; it gets the past week's (`_format_recent_cold_opens`) as ones not to reuse.
- **Storyboard before script** (`storyboard_script`): one structured call plans the roundup's airing order and each story's point and bridge ("clean pivot" when nothing is shared) and the deep dive's question, stances and beats, each saying why the next follows. It may move same-subject stories together and fold same-story reports into one (`merge_with`, `_merged_into`); `apply_storyboard_order` keeps local first, relabels a moved story to the block it lands in, and airs the draft order on an invalid plan. A kicker on an earlier story's subject is moved beside it in code (`kicker_repeats`, `_unbookend_kicker`): the prompt alone let it close the show. It rides in the dynamic prompt, never the cached one; a missing or partial plan `degrade()`s and the script runs unplanned. The polish gets the deep dive's question and beat chain only (`_storyboard_polish_addendum`), never the full plan.
- **The storyboard sees the week so far** (`week_so_far_block`, `config/prompts.json` → `storyboard_week_so_far`): the last `WEEK_SO_FAR_DAYS` (6) days' deep-dive questions and landings, read from their citations JSON, today's anchor marked THIS WEEK. It moves the week's question on or picks up an open landing when today's material adds a fact; an earlier anchor's days give open ends only, never this week's thread; it never re-asks one. A callback is allowed, never required. Dynamic prompt only.
- **The script is checked against its storyboard** (`storyboard_flow_line`): roundup pairs aired in the plan's order, named bridges, planned beats aired (`STORYBOARD_BEAT_AIRED_OVERLAP`). Drift is the `storyboard-drift` signal; a clean pivot is never a defect, so named bridges are shown, not flagged. The review page renders the plan (`render_plan`) and the producer's card carries a whole-episode "Flow" thumb.
- **The two polish prompts share their rules line for line** (a test enforces it); change both or neither.
- **Already aired** (`config/prompts.json` → `aired_ledger`): the past week's Roundup and Deep Dive lines that today's material restates go into the dynamic prompt, and the draft's repeats go to polish. Python matching, never whole past scripts in the prompt. A candidate's name is never a repeat. The polish gets the research half of the ledger too: it is told to weave in findings the draft left out, and a polish that adds repeats `degrade()`s (`script/aired-check`).
- **A roundup story in the deep dive gets a callback, never a retelling.** `find_roundup_echoes` (same `_echoes` matcher) hands the draft's deep dive lines that restate the roundup to both polishes (`roundup_echo_template`); more than `ROUNDUP_ECHO_SLACK` left after polish `degrade()`s `script/roundup-echo`. Rule (u) asks for "we mentioned up top", not "state the fact itself".
- **TIME OF DAY:** listeners hear the show in the morning. Never "tonight's episode".

**Host voice cards** (`hosts.json` → `voice_style`): style and perspective from a real writer's prose, rendered under each host's SPEECH rules. Never personal traits, civic positions, examples, or the source's own phrasing; a test runs every line through the tell lists.

**Sunday Meta Moment.** One Haiku call turns the week's commit subjects into a segment.
- **NONE is a first-class answer.**
- The reply cites its commit lines (`COVERED:`) before it speaks, and unknown capitalised names drop the segment.
- Every Sunday without the segment `degrade()`s, whatever the reason.
- Nothing listener-facing goes into that prompt unconditionally.
- A thin week (under `META_MOMENT_SPARSE_BELOW` show commits) folds in `super-rss-feed`'s human commits under `UPSTREAM_HEADER`; only `- ` lines are citable subjects.
- Every week also folds in `super-rss-feed`'s Saturday weekly run (`get_upstream_week_changes`: `weekly-state-article.json` → `_changes`), only while it is under 7 days old. **No example names in the prompt prose**: the hosts said "Google" because the prompt did.
- **Never `git fetch --depth` over the full checkout**: a shallow repo answers `git log --since` with one commit and no error.
- **Protect the segment; never force it.** A NONE or a guard drop gets one retry, with upstream changes and the refusal reason (`script/meta-moment/retry`); a failed call degrades; the splice falls back to the deep dive; and a Sunday that ships without it turns the run red (last step, after deploy).

**Inbound mail** (`email_ingest.py`). `podcasts/email_queue.json` is public: senders are masked and contact details redacted at ingest.
- "From the producer" is decided at ingest (`is_producer_sender`, stamped `from_producer`); never re-derive it from the masked address.
- The block header stays `LISTENER CORRECTIONS`; attribution is per item.
- A correction airs as the final beat of the next roundup (`docs/corrections-policy.md`).
- **A correction that should outlive its episode becomes a standing note.** Every Sunday `standing_notes.py` (in `periodic-review.yml`) reads the correction and feedback emails no run has seen, makes one Haiku call, and opens a PR adding proposed lines to `config/standing_notes.txt`. Merge adopts; close declines for good. Email text is untrusted, so it is quoted as data and nothing reaches a prompt without a merged PR. One proposal PR at a time.
