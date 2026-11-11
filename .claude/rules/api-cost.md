---
paths:
  - "podcast_generator.py"
  - "tests/test_podcast_generator.py"
  - "episode_review.py"
  - "review_scripts.py"
  - "standing_notes.py"
  - "weekly_anchor.py"
  - "config_loader.py"
  - ".github/workflows/*.yml"
---
<!-- Moved verbatim from CLAUDE.md on 2026-11-02. Claude Code loads this file when a
     matching path is read or edited. Keep each rule to one or two lines; the story goes
     in docs/decisions/. -->

## API cost — models, routing and prices

The general rules are in the root CLAUDE.md under API Cost Discipline.

- **Default to the cheapest model.** Escalate (Haiku → Sonnet → Opus) only when demonstrably required — justify explicitly. Opus is only used for polish escalation (`select_review_model`): thin deep-dive sourcing (<3 sources by `_deep_dive_source_count`: article rows, a Kagi cluster's outlets, answered research questions) or more than `OPUS_QUALITY_HIT_THRESHOLD` (3) pre-polish tell hits. The hit count fired it about 1 day in 5 on Sonnet 5 drafts and 0 of 4 on Haiku 5.5 drafts (0–1 hits); revisit after the polish trial, not before (Opus overrides `CLAUDE_POLISH_MODEL`).
- **Haiku 5.5, Sonnet 5 and Opus 5 think when `thinking` is omitted, and thinking shares `max_tokens` with the answer.** A small-budget or structured call must pass `thinking=thinking_off(model)` (`config_loader`), never a literal: Haiku 5.5 and Sonnet 5 take `{"type": "disabled"}`, Sonnet 5.5 rejects it with a 400 and takes `between_tools`, and Opus 5.5 can't turn thinking off at all. Otherwise it returns no text: the weekly anchor framings and the weekly script review both failed this way for months.
- **One switch for Sonnet, one for Haiku.** Every Sonnet role follows `CLAUDE_SONNET_MODEL` (a repository variable both workflows pass through; **unset means `claude-haiku-5-5`** since 2026-10-08, see [operations.md](../../docs/decisions/operations.md)); every Haiku role follows `CLAUDE_HAIKU_MODEL` (`config_loader.HAIKU_MODEL`, default `claude-haiku-5-5`). A per-role `CLAUDE_*_MODEL` still overrides either: `CLAUDE_SCRIPT_MODEL`, `CLAUDE_POLISH_MODEL` and `CLAUDE_STORYBOARD_MODEL` (daily-podcast.yml; the storyboard defaults to `claude-sonnet-5-5` there), `CLAUDE_REVIEW_MODEL` (the weekly script reviewer, periodic-review.yml only: `episode_review.py` reads it too, so never pass it to daily-podcast.yml). Opus escalation is `claude-opus-5-5`; a Haiku 5.5 decline retries on `claude-sonnet-5` (`_REFUSAL_FALLBACK`).
- **A budget over `STREAM_ABOVE_MAX_TOKENS` (16000) streams**, forced in `send_claude`: the SDK refuses a non-streaming call it estimates at over 10 minutes (~21K on Sonnet) before sending it. The storyboard's 32000 died that way on 2026-11-01.
- **The storyboard goes through the Batch API** (`_storyboard_via_batch`, half price) and waits `STORYBOARD_BATCH_TIMEOUT` (300 s) before planning in real time; every such fallback `degrade()`s as `script/storyboard-batch`. `PODCAST_STORYBOARD_BATCH=0` turns it off.
- **A refusal is retried once on the model it replaced** (`send_claude`, `_REFUSAL_FALLBACK`) and always `degrade()`s as `claude/refusal`. Every Sonnet or Opus call goes through `send_claude` or `create_message`, never a bare `client.messages.create`.

**Anthropic prices** (USD per million tokens, first-party API, checked 2026-10-08; refresh from the pricing page before any cost decision):

| Model | Input | Output | Cache read |
|-------|-------|--------|------------|
| Haiku 5.5 (prompt ≤ 100K tokens) | $0.10 | $0.50 | $0.01 |
| Haiku 5.5 (prompt > 100K) | $0.50 | $2.50 | $0.05 |
| Haiku 4.5 | $1 | $5 | $0.10 |
| Sonnet 5 / 5.5 | $2 | $10 | $0.20 |
| Sonnet 4.5 | $3 | $15 | $0.30 |
| Opus 5.5 | $4 | $20 | $0.20 |
| Opus 5 | $5 | $25 | $0.50 |

Cache writes are 1.25x input (5-minute TTL) or 2x (1-hour TTL). The Batch API halves every line, cache included. A cancelled batch still bills the requests that finished before the cancel. Baseline for September 2026 was $31.21 billed (list price, no discounts) across both keys: `cariboosignals` $21.0 (Sonnet 5 script and polish about 3/4 of it), `superfeed` $10.2 (Haiku scoring $9.2).
