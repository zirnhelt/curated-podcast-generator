---
paths:
  - "podcast_generator.py"
  - "tests/test_podcast_generator.py"
  - "episode_review.py"
  - "tests/test_episode_review.py"
  - "tests/test_run_report.py"
  - "config_loader.py"
  - ".github/**"
  - "cloudflare/**"
---
<!-- Moved verbatim from CLAUDE.md on 2026-11-02. Claude Code loads this file when a
     matching path is read or edited. Keep each rule to one or two lines; the story goes
     in docs/decisions/. -->

### Operations — see [docs/decisions/operations.md](../../docs/decisions/operations.md)

**Scheduling.** A Cloudflare Worker (`cloudflare/scheduler/`, shared with `super-rss-feed`) dispatches the 1:05 / 2:05 / 3:05 AM Pacific ladder with a `run_slot` input; GitHub's cron is best-effort and a late run misses the listener's morning.
- The Worker **starts** runs; `check-episode` alone decides whether one is needed. Never add a second "did today ship?" check.
- Keep the one GitHub cron (`5 11 * * *`) as the backstop for the Worker being down. Don't remove it; don't add ladder slots back.
- Anything that needs to know which rung it is on reads `inputs.run_slot`, never `github.event.schedule`.
- Deploy the Worker with **Actions → Deploy Cloudflare Scheduler**; there is no local wrangler path.

**Script length.**Scripts under `TARGET_SCRIPT_WORDS` get up to `EXPAND_MAX_PASSES` (1) expand pass; under `MIN_SCRIPT_WORDS` after that the run aborts. The script call, its retries and the polish carry `max_tokens` 64000. About 19–20 min ships (167 words a minute), against the 22 min the prompts still say.

**Segments.** Every phase runs in `with segment(name, critical=...)`.
- `critical=True` (default) propagates; pass `exit_code=` to turn it into a process exit status.
- `critical=False` swallows the exception. **Pre-assign the block's outputs to their fallback before the `with`.**
- `SystemExit` always passes through. Each memory/state-file write gets its own segment.

**`degrade(name, detail)`.** When you add a fallback, add a `degrade()` call — a silent fallback is the failure this exists to catch. `run_publish_stage` derives exit 78 from these records. Modules that cannot import `degrade()` without a cycle (`gemini_tts`, `weekly_anchor`, `native_land`) append to their own `_degradations` list, which the pipeline drains; a new fallback there must append too. `write_run_report()` runs from a `finally`, so a crashed run still reports.

**Money preflight** (`check_api_budget`, `_check_tts_budget`). Recognise billing walls by their **credit wording, never the status code** (`_billing_wall()`): Gemini uses the same 429 for an ordinary rate limit. TTS is preflighted in the script stage, before the day's state is spent. OpenAI is the universal fallback, so its health alone answers "can this day ship?"; the primary is probed only when OpenAI is walled, and the run aborts only when nothing left can render. A wall can also arrive mid-run: any `except` around a Claude call that falls back must call `_abort_if_billing_wall(e)` first.

**Commits.** Every workflow commits through the `./.github/actions/commit-push` composite action, never an inline `git add`/`commit`/`push`. Its `--autostash` rebase is load-bearing. **If a stage writes a tracked file, some step must stage it.**

**Atomic writes.** Every memory/state/feed write goes through `config_loader.atomic_write_text` / `atomic_write_json`. The loaders read a truncated JSON as `{}`, so a non-atomic write silently loses history.

**Third-party text and URLs.** Episode notes are HTML in a public feed: feed titles, bylines and model summaries go in `html.escape`d, links only when `_public_http_url` passes, and every CDATA body (bespoke feed included) through `_cdata_safe`. Every fetch of a feed- or model-supplied URL goes through `_get_public`, never a bare `requests.get`: each hop is checked by name and by resolved address, the body is capped (`MAX_BODY_BYTES`) and the whole call is abandoned at `MAX_FETCH_SECONDS`. Keep that clock a thread join, never a check between chunks.

**Brave spend.** Two plans, two meters: Search ($5/1000, capped) and Answers (monthly credit).
- **The Search cap is shared with `super-rss-feed`** — one key serves both repos. Read Brave's per-key usage export before trusting any estimate.
- A 402 closes that meter for the run (`_trip_brave_wall`); a spent meter is a reason to ask the other one, not to give up.
- Four per-run budgets: `BRAVE_SEARCH_CALL_LIMIT` (speculative body backfill), `BRAVE_DEEP_DIVE_CALL_LIMIT` (demand-driven research), `BRAVE_EVENT_CALL_LIMIT` (the election roll call and the weekday election pass), `BRAVE_ANSWERS_CALL_LIMIT` (4 a run, refit 2026-10-12 off $0.041 a query). **Only the three rate-limit wrappers may call `_brave_search`** (a test enforces it). Answers is never called from the speculative path.
- **A research pass's allowance is enforced in code** (`_budgeted`), never by the prompt or `max_iterations` alone: parallel tool calls walk past both. Fact resolution reads the deep dive first (`_script_question_excerpt`), never a prefix of the script.
- With both meters closed, skip the research pass rather than report "no research warranted".
- Paid body backfill runs on the curated roundup plus `ROUNDUP_BACKFILL_SPARES`, never the pre-curation pool. Direct factual lookups default to Answers; Search is for context and anything attributed to an outlet.

**Daily review → roadmap** (`episode_review.py`). One Haiku call a night turns the review into candidate findings; dedup, counting and rendering are Python. An item reaches `ROADMAP.md` on its `ROADMAP_MIN_OCCURRENCES`th sighting. The tool owns only the block between `<!-- reviews:begin -->` and `<!-- reviews:end -->`; a human closes an item by checking its box.
- **Findings are keyed on signals** read off the facts (`run_signals`: `degraded:<segment>`, `short-script`, `citations:*`, `no-local-roundup`, `split-topic`, `deep-dive-non-local` (from `roundup_shape_line`), `off-charter-theme-block`, `off-theme-deep-dive`, `thin-deep-dive` (from `theme_fit_line`), `storyboard-drift` (from `storyboard_flow_line`), …), never on the model's id or title, and an item closes itself once its signal has been absent `SIGNAL_QUIET_DAYS`. A new failure mode worth tracking gets a signal, not a looser title match.
- Only tool-written items retire. The review's run labels must follow the schedule (`_trigger_label`); a stale label was the most-sighted roadmap item for three weeks.
- Once the review is live on Pages, the review job dispatches `super-rss-feed`'s `refresh-editorial.yml` (scheduler PAT), so it reaches `feed-local` that morning rather than with the next nightly.
