# Editorial voice: anchor questions, naming nations, AI tells, the Meta Moment and inbound mail

*Moved verbatim from CLAUDE.md on 2026-09-23. CLAUDE.md keeps the standing rules; this file keeps the incidents and the reasoning behind them. Read it before changing the code it describes.*

## Weekly Anchor Questions (`weekly_anchor.py`, `config/weekly_anchors.json`)

The third rotation layer, above the daily theme and the super-cycle focus. One open question
per ISO week — "Why is everyone in tech so sad?" — that all seven deep dives circle from their
own theme's angle. Selected by `select_anchor()` in the non-critical `script/anchor` segment,
rendered into the script prompt's `{anchor_block}` by `format_anchor_for_prompt()`.

Unlike the focus, the anchor **is named on air**: the focus is a curation device, the anchor is
an editorial idea and the reason to listen more than one day a week.

- **Idempotency is bought with state, not the calendar.** `get_focus_for_day` is stateless
  because the rotation is a pure function of the date; an anchor cannot be, because the pool is
  eventually LLM-generated. Instead the week's choice is **pinned** in
  `podcasts/weekly_anchor_state.json` on the first run of the ISO week, and every later run that
  week — including a re-render days later — reads that record back unchanged. A second question
  appearing mid-week is the failure this prevents.
- **No repetition works on `dimension`, not wording.** Each question is tagged with the
  dimension of experience it opens (`labour-meaning`, `scale`, `trust`, …). An `id` is spent
  forever; a `dimension` has a 26-week cooldown. Keying the guard on the dimension is what stops
  a generated question returning as a paraphrase. Both checks are local — no API call.
- **Pool with LLM top-up.** `config/weekly_anchors.json` ships 11 seeded questions in order.
  `top_up_pool()` fires when eligible entries drop below `MIN_POOL_REMAINING` — before the pool
  empties, so a failed top-up costs a warning rather than the week's anchor — conditioned on
  every question and dimension already used. Roughly one call every 11 weeks.
- **`pin_week`** forces a question onto a specific ISO week. A **future** pin is never taken
  early; an **overdue** pin still runs, so shipping late does not bury a question that was
  scheduled deliberately.
- **Framing, never selection.** Article selection is untouched — the anchor is a lens for
  reading whatever the theme and focus already chose. One Claude call per week generates the
  seven per-weekday angles; a failure degrades to an anchor with `framings: {}`, which still
  works.
- **The escape hatch is load-bearing.** The prompt block ends with an instruction to drop the
  anchor entirely when the day's material does not genuinely reach it. Seven days orbiting one
  question is a standing invitation to manufacture connections — the same failure the roundup's
  `_NEVER_ANNOUNCE` block headers exist to prevent, with a much stronger pull. There is a test
  asserting that instruction is present.
- `weekly_anchor` cannot import `degrade()` without a circular import, so it records
  degradations and `run_script_stage` drains them via `_report_anchor_degradations()`. **A new
  fallback here must append to `_degradations`** or it will not reach the run report.
- Preview the schedule without spending or writing state: `python weekly_anchor.py --preview 12`.

## Naming a nation on air (`native_land.py`, `config/indigenous_nations.json`)

On 2026-09-18 the Wild Spaces episode ran its deep dive on a BC Wildfire Service
prescribed burn near Deer Park Mountain **outside Castlegar** — West Kootenay, Sinixt
territory, about 600 km southeast of here — and asked four separate times, in the cold
open, the deep dive and the outro, whether "Sinixt or **Tŝilhqot'in** voices" had shaped
the burn plan. Sinixt is right. Tŝilhqot'in territory is the Chilcotin plateau and comes
nowhere near Castlegar.

**Nothing was wrong about a fact the pipeline had.** `INDIGENOUS CONTEXT` in
`prompts.json` hands the writer the show's three acknowledgment nations as standing
regional context, the writer reached for the vocabulary it was given, and no stage after
it could tell that a nation and a place had been put together that do not go together.
It is the same failure as the Sunday Meta Moment's fabricated Ktunaxa story and the
roundup's borrowed candidate name: the prompt supplies a roster, and a roster with no
scope attached gets used out of scope.

- **The prompt fix is the load-bearing half.** Those three names now say in the prompt
  that they describe the Cariboo and nowhere else, that a nation is named only for its
  own territory and only when a source names it, and that a story the show has no
  sourced nation for asks its question without naming anyone. **Naming the wrong people
  is worse than naming none** — the escape hatch is the point, exactly as it is for the
  weekly anchor and the Meta Moment's NONE.
- **The check is the backstop**, and it measures the output rather than lengthening the
  instruction — the phrase ledger's trade, and `_meta_moment_unknown_names`'.
  `script/territory-check` runs after `script/tell-scrub`, deliberately: that day's error
  was in the cold open, the deep dive *and* the outro, and a check placed before
  `generate_cold_open` would have cleared two of the three.
- **It only ever disconfirms.** A finding needs a nation, an out-of-region place, a
  territory lookup that answered, and that nation absent from what came back. A lookup
  that fails, is unsure, or returns an empty list changes nothing. Native Land Digital
  says plainly that its maps are crowd-sourced, are not authoritative, and must not be
  used to define boundaries — so it may take away a claim the show cannot source and
  **must never supply one**. The rewrite deletes the wrong name and is forbidden from
  substituting a right one; SOURCED OR UNSAID is not weakened by a crowd-sourced map and
  must not be strengthened by one either.
- **The standing land acknowledgment is exempt by construction, not by a special case.**
  A sentence whose place names are all on `podcast.json`'s `local_places` is never
  checked. The welcome line names the three nations and the Cariboo, is correct, and is
  spoken every single episode — a check that rewrote it nightly would be worse than no
  check. What is left is precisely the observed failure: a house nation attached to a
  place the show is not from.
- **Two keyless lookups, both cached forever, both bounded.** Place name → coordinates
  via Open-Meteo's geocoder (already the show's weather vendor, so no new dependency and
  no new key), coordinates → territories via `native-land.ca`.
  **`NATIVE_LAND_API_KEY` is a repository *secret*** — the opposite of the
  `GEMINI_TTS_MODEL` rule two sections down, because a model name is not a credential
  and this is. It is sent when set, the request is made without one when it is not, and
  a missing or expired key costs the confirmation rather than the episode. The
  degradation row records only the exception *type*: the message carries the request
  URL, and the URL carries the key. The geocoder is also the *gazetteer* — a capitalized word that does not
  resolve to a Canadian place is cached as unresolved and costs one lookup once, ever —
  and a Canadian result in BC outranks a same-named town elsewhere, because checking
  Deer Park, Texas against this map would be worse than not checking.
- **Orthography is the whole difficulty.** Tŝilhqot'in, Tsilhqot'in and Chilcotin are one
  nation; "Secwépemc" in the script is "Secwepemc (Shuswap)" on the map. `_normalize`
  folds diacritics and punctuation away and matching is substring in both directions —
  a false *match* only means the line ships as written, which is the safe direction for
  this check to fail in.
- `native_land` cannot import `degrade()` without a circular import, so it records
  degradations and the script stage drains them via `_report_native_land_degradations()`.
  **A new fallback there must append to `_degradations`** or it will not reach the run
  report.
- **The lookup shape is not verified against the live API from this sandbox** — the
  network policy denies both hosts. The parser accepts a bare GeoJSON feature list *and*
  a FeatureCollection, and any other shape reads as "no answer", which produces no
  finding. Confirm it against a real response before trusting the row it writes.

**Opinion columns and crime incidents are filtered upstream, not here** —
`super-rss-feed`'s `podcast_content_exclusion()`, whose scope is the podcast pool alone
(the reader still gets the local RCMP story in `feed-local.json`). Articles already sitting
in `article_holding.json` when that shipped were admitted under the old rule and age out
on the 14-day hold window; nothing downstream re-checks them. **When a crime story or an
op-ed reaches an episode, read `config/podcast_schedule.json` → `excluded_content` in the
sibling repo before touching anything here.**

## Voice and AI Tells (`config/ai_tells.json`, `podcasts/phrase_ledger.json`)

Two mechanisms, because the obvious one had already failed. `script_generation_system`
banned `"[X] is carrying a lot of weight in that sentence"` verbatim for a long time and the
phrase still shipped; `genuinely` reached 146 uses across 30 episodes (~5/episode) without any
single script looking unusual. A longer prose ban list is not the fix, and `score_script`
counting hits into a JSON nobody reads is not enforcement.

**The corpus is one file.** `config/ai_tells.json` holds `hard_banned`, the regex `patterns`
and `soft_patterns` that `score_script` scans, the `ledger` tuning and the `rhythm` budget.
`score_script` falls back to `_FALLBACK_TELL_PATTERNS` when the file is missing — a style file
must never be able to fail a run. **A new pattern family goes in `soft_patterns` unless the
extra Opus escalation is intended and costed:** `soft_patterns` are reported but excluded from
`total_hits`, which gates `OPUS_QUALITY_HIT_THRESHOLD`. Adding two families to `patterns` took
Opus escalation from 2/30 episodes to 5/30 before they were moved.

### The prompt was teaching the tic

`genuinely` appeared **44 times in `prompts.json` prose** and 146 times in the scripts;
`directly` 31 and 94; `actually` 26 and 405. The model copies its instructions' register, so
the ban and the example sat in the same file. All 44 are gone (deleting the adverb never
changed an instruction's meaning) and `tests/test_ai_tells.py` fails if a hard-banned phrase
reappears in prompt prose outside a quoted ban example. **Check the burned list before writing
prompt copy** — the fastest way to install a new tic is to use it in the instructions.

### The ledger — the back catalogue as the ban list

`update_phrase_ledger` folds each finished script into a 21-episode window and promotes
anything spiking. Nobody predicted `genuinely`, and nobody should have to predict its
successor: `quietly` (31x/16 episodes) surfaced on its own.

Three filters decide what may be burned, and each exists because the unfiltered version
produced garbage on a backfill of the real 30-episode catalogue:

- **Adverbs only** (`unigram_mode`). Content words are subject matter — a news show says
  "story", "region" and "question" constantly and must keep doing so. Raw frequency burned all
  three. The generated register lives in stance adverbs.
- **Proper nouns never counted.** An n-gram containing a capitalized non-sentence-initial token
  is skipped, so "Williams Lake" and "Cariboo Regional District" can never be burned.
- **`min_repetition_ratio`** (count/episodes ≥ 2). Boilerplate is said once per episode, every
  episode; a tic recurs inside one. This is what keeps the show's own welcome copy
  ("impact our rural communities", 21x/21 episodes) off the list without parsing sections.

**`ngram_sizes` is `[1]` deliberately.** On the backfill, every multi-word phrase clearing the
thresholds was either basic English ("it's a", "rather than") or subject matter ("fire season"),
and banning those in the prompt would damage the script. Division of labour: the ledger
machine-detects the adverb register; multi-word tics are what a human notices, and
`hard_banned` is the channel for naming them.

**The tail of `hard_banned` is not a style tic.** The show renders overnight and listeners have
it before breakfast, and nothing in the prompt said so — 2026-09-21 aired "here's a concrete
version of tonight's argument" and "argue with either of us about tonight's conclusion". The
prompt fix is the load-bearing half (`CRITICAL REQUIREMENTS` → **TIME OF DAY**), and the
phrases placing the episode at night ride in `hard_banned` because that one list already buys
all three enforcement paths with no code: the BURNED PHRASES block, the post-cold-open scrub,
and the test that stops prompt prose teaching the phrase it bans.

**It is a rule about when the SHOW is, not about when the world is.** Across 237 scripts every
other "tonight" was correct — an overnight low, a meteor shower, a clear sky worth going
outside for — so only literal, always-wrong forms belong there: `tonight's <episode noun>`, an
evening greeting, a farewell to the night. A broader ban would delete the weather check. The
scrub's rewrite rule is split for the same reason: an intensifier is deleted, a night phrase
takes the daytime substitution.

Promotion requires `phrase in counts` — the window aggregate still holds a phrase for weeks
after the show stops saying it, so promoting off the aggregate alone re-fired daily, reset
`clean_streak`, and nothing could ever retire. A phrase retires after
`retire_after_clean_episodes` clean episodes, which frees the slot for whatever replaced it.
Idempotent on date, so a re-render never double-counts its own episode.

### Enforcement

`format_burned_phrases_for_prompt()` renders the block into the **dynamic** user prompt
(never the cached system prompt — it changes daily and would defeat the cache), the expand
retry, the cold open and both polish paths. `config_loader.format_static_tell_block()` carries
the config-only half so `generate_bespoke.py` can use it without importing the pipeline — the
same reason `atomic_write_text` lives there. Bespoke built its own prompt and inherited none of
this until then.

`script/tell-scrub` runs **after** `script/cold-open`, deliberately: `generate_cold_open` runs
after every polish pass, so the teaser is the one part of the episode nothing else cleans, and
it is the first thing a listener hears. It sends only the offending sentences to `SCRUB_MODEL`
(Haiku) — a few hundred tokens, against 3,400 words for a re-polish. A rewrite is spliced only
if it is clean and the original still matches verbatim; anything else keeps the original and
`degrade()`s, so a bad rewrite can never be worse than the tic.

### The rhythm budget

The vocabulary is half of it. 47 words per turn, 53 em-dashes an episode and every turn a
finished paragraph is a fingerprint on its own. The system prompt's `**RHYTHM**` section asks
for what the show should sound like — short turns, one flat unhedged statement, a disagreement
allowed to not resolve — and `score_rhythm` measures exactly those, reporting `over_budget`
into `episode.quality`. It is advisory: nothing blocks on it.

## The Sunday Meta Moment (`get_weekly_changelog`, `generate_meta_moment_text`)

One Haiku call turns the week's commit subjects into a short Riley/Casey segment about
changes to the show itself. The input is `git log --since=7d` over `GENERATION_PATHS`
(`review_scripts.py`) — subject lines only, minus merge commits and minus the embargoed
delivery surfaces in `podcast.json`.

**The failure mode is invention, and the old prompt demanded it.** A week's commits are
usually plumbing — "Split the Brave meters: Answers is its own plan now" has no
listener-facing form at all — while the prompt asked for the 3-4 most listener-noticeable
changes and 320-400 words regardless. A model asked for four good answers where none exist
supplies four: three of the four Sundays to 2026-08-30 aired a "weekly inspiration harvest"
that was never committed, and 08-30 backed it with a Ktunaxa Nation story that did not
exist. The instruction against it ("never fabricate names or details not in the commit
list") had been in the prompt the whole time.

- **NONE is a first-class answer**, with the segment lengths tiered by how many entries
  genuinely reach a listener. Same shape as the roadmap distiller's empty list, and as the
  weekly anchor's escape hatch: a segment that must find something finds something.
- **The escape hatch had three thumbs on it and no definition of what qualifies** — "most
  weeks the honest count is zero", "NONE is always a safe answer", "a week of internal
  plumbing is a NONE, not a challenge" — against one unglossed phrase, "a consequence a
  listener could notice". On 2026-09-06 a week carrying *Rank the Cariboo civic day on home
  jurisdiction, and center the WL election* and *Recall election stories for the civic day*
  came back NONE. The over-correction from the fabrication problem is its own failure: the
  prompt now says what counts (what the show picks, what order it airs things in, what the
  hosts say, how it sounds) and what does not (logging, retries, budgets, file layout), and
  asks for an honest count **in both directions** — never pad the list, and never drop a
  qualifying change because the segment would be short.
- **The reply cites before it speaks.** `COVERED: <commit line>` above the dialogue,
  matched back against the real subjects at `difflib` ratio ≥ 0.9 (`_meta_moment_covered`).
  A change the model made up has no line to copy.
- **Names are checked, not requested** (`_meta_moment_unknown_names`). Any capitalized word
  the dialogue can't source from the commit list, the host roster, the show title, the
  territory acknowledgment or the show's own place names (`local_places` / `home_places`)
  drops the segment. The place list is load-bearing and was missing: a commit writes "the WL
  election", a host reading it aloud says "the Williams Lake election", and the guard called
  Williams Lake an invention. Naming a place the show is *about* is never the fabrication
  this is looking for. Sentence-initial words, possessives and
  quoted asides are excluded — verified against the four aired segments, which flag only
  the fabrications. This is the phrase-ledger trade: measure the output instead of
  lengthening the ban.
- **Every Sunday without the segment `degrade()`s under `script/meta-moment`** — a guard
  drop *and* a NONE. A quiet week is a legitimate answer and not an error, but it is still a
  Sunday that aired without its Sunday segment, and until 2026-09-06 the only trace of that
  decision was one line in the job log. "Was that by design?" is a question the run report
  should answer without anyone reading the log.
  - **That promise had three holes and 2026-09-13 fell through one.** The segment vanished
    with no API call, no print and no row: `script/day-specific-inserts` opened and closed
    empty. The paths that skip *before* the model — an empty changelog, a missing client —
    and the caller's drop when the script carries no `**COMMUNITY SPOTLIGHT**` to splice
    ahead of all returned `""` in silence, so only the model's own NONE was ever reported.
    All three degrade now.
  - **An empty changelog is not evidence of a quiet week.** `_git` returned `""` for a
    *failed* command as readily as for no commits, which made the two indistinguishable —
    the same silence, from the same helper, that left every `reviews/review_*.md` a bare
    header since launch (`periodic-review.yml` checked out at depth 1, so `git log --since`
    had no week to read; it now checks out `fetch-depth: 0`). `_git` prints the exit code
    and stderr now, and the degradation says the row cannot tell which of the two it was.
- **Nothing listener-facing goes in the prompt unconditionally.** The sentence telling the
  hosts to say "transcripts in your podcast app" handed them a topic, and they used it in a
  week with no transcript commit; it now appears only when a commit earns it. The prompt
  teaching the tic is the same failure `genuinely` documented above.
- The last turn hands off **in general terms** — the Meta Moment is spliced ahead of the
  community spotlight and has not been told what follows it. On 2026-08-30 it previewed a
  deep-dive story two segments away, and invented that too.

## Inbound mail, and who caught it (`email_ingest.py`, `config/blocklist.json`)

Gmail items land in `podcasts/email_queue.json` as `newsletter`, `feedback` or `correction`.
Feedback and newsletters wait for their theme day; a correction is never theme-gated and airs
as the final beat of the next roundup (`docs/corrections-policy.md`).

**The queue is committed to a public repo.** Senders are masked, and `_sanitize` redacts
email addresses and phone numbers from bodies (`_redact_contact_details`), because a
signature carries both. Before the recipient allowlist (2026-07-25) the ingest also queued
personal mail as "feedback"; those items were removed on 2026-09-23 but remain in git history.

**The producer is not a listener.** On 2026-09-02 a correction Erich sent himself aired as
"A listener named Erich wrote in… Thanks, Erich" — the writer had only the body's signature to
go on, and a signature is not provenance. `config_loader.is_producer_sender()` answers it from
`config/blocklist.json` → `email_producer_senders`, and `email_ingest` stamps `from_producer`
onto the queued item.

- **It is decided at ingest because that is the last point identity exists.** The stored
  address is masked (`z***@gmail.com`) so the queue can be committed; a masked address matches
  every gmail sender whose name starts with the same letter, so the pipeline reads the flag and
  never re-derives it.
- **Only the attribution changes.** Production mail is queued, theme-gated and aired exactly
  like listener mail; the prompt block labels each item `[Listener correction]` or
  `[Production correction]` and says the show owns the in-house ones ("we caught this on our
  end"), never names the producer on air, and thanks a listener only for a listener's catch.
- **The block header stays `LISTENER CORRECTIONS`** even when every item is in-house — the
  placement and fabrication rules in `prompts.json` key on that exact name. Who caught it is
  per item, because that is what varies.
- **The fabrication guard covers both shapes.** `_corrections_ground_truth` and
  `strip_unsourced_correction` now treat an uncited "our production team caught…" the way they
  always treated an uncited "a listener flagged…" — the new wording is as inventable as the old.
