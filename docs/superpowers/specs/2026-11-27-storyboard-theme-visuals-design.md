# Storyboard & theme adherence visuals on the daily review — design

## Intent

The nightly review (`episode_review.py` → `podcasts/reviews/episode-review-{date}.html`) already
*says* how well the script kept to its storyboard (flow numbers, the Haiku fidelity judgement) and
how on-theme the episode was (`theme_fit` line). Nothing *shows* it. The producer wants to see at a
glance (B) tonight's adherence and (D) the pattern across days and themes.

## Goals

- **B — Plan vs. aired diagram** for tonight: planned roundup order on top, aired order below,
  connectors between them (crossings = reorder), named bridges marked kept/skipped, and the
  deep-dive beats as status pips with question / stances / landing beside them.
- **D — Adherence heatmap**: one row per night, columns frame, order, bridges, question, stances,
  beats, landing, theme fit; cells kept / loose / drift / no data. Row label = day + theme, so a
  theme that keeps drifting shows up.

## Non-goals / anti-goals

- No new API call. B and D reuse the storyboard, the flow line and the existing fidelity call.
- Not a signal: the heatmap never feeds `run_signals` / the roadmap (fidelity is judgement, by rule).
- Page only. The RSS copy (`content_html`) stays lean; no SVG in the feed.
- No JS, no chart library: inline SVG + the page's existing CSS tokens, like `_sparkline`.

## Constraints

- All plan/model text in the SVG is `html.escape`d (it's third-party-derived).
- Citations JSON is written through `atomic_write_json` (already true).
- Tests must not touch live `podcasts/` state (fixtures in `tmp_path`).
- Works in light and dark (existing `.spark`-style classes, no hard-coded colours) and at phone width
  (SVG `viewBox`, table in `.table-wrap`).

## Approaches considered

- **A** sparklines, **C** theme-fit strip plot — not picked (A lacks per-night detail; C needs
  per-article charter scores the citations don't keep). B + D picked by the producer.
- For B's aired order: (1) re-derive first mentions inside `episode_review.py`, or (2) have the
  generator save what it already computes. **Picked (2)**: `storyboard_flow_line` already finds each
  story's first-mention turn; copying `_article_first_mention` into the review would fork the matcher.
- For D's history: (1) re-read old citations/log files each night, or (2) store each night's cells in
  its `reviews/index.json` entry. **Picked (2)**: the index is already the review's only history
  (that's how the sparklines work); logs aren't kept.

## The how

**Generator (script stage, `podcast_generator.py`)**
- Split `storyboard_flow_line` into a helper returning the numbers *plus* per-story aired turn
  positions (keyed by plan `n`) and per-beat aired booleans; the log line is unchanged.
- Save that as `citations["storyboard_aired"] = {"roundup": [{"n", "turn"|null}…], "beats": [bool…]}`
  via `generate_citations_file(..., storyboard_aired=...)`.

**Review (`episode_review.py`)**
- `load_storyboard_aired(date)` via `_citations_key`.
- `render_plan_diagram(plan, aired, fidelity) -> str`: inline SVG.
  - Top row: plan stories in plan order (chip = `n` + first ~4 words of `point`, full point in `<title>`).
  - Bottom row: same stories sorted by aired turn; un-aired stories greyed at the end.
  - Connector per story; a link that crosses another is drawn in the "loose" colour.
  - Bridge marks between top-row chips: solid = named bridge, gap = clean pivot.
  - Beat pips: fidelity status per beat when the judgement exists, else the counted `aired` booleans.
  - Question / stances / landing as three small labelled status chips from the fidelity judgement.
  - Inserted in `build_review` right before `render_plan` (page only).
- `adherence_cells(facts, fidelity) -> dict[str, str]`: each column graded `kept|loose|drift|none`.
  - From fidelity: frame (aired/changed/dropped), order (held/mostly held/changed), bridges
    (used/partly/ignored; "none planned" → none), question, stances, landing.
  - Beats: counted ratio from `storyboard_flow` (≥0.8 kept, ≥0.5 loose, else drift).
  - Theme fit (from `theme_fit`): kept when 0 off-charter and 0 unused on-theme; drift when either
    is ≥2; loose otherwise. *(My call; thresholds are constants at the top for tuning.)*
- Stored as `"adherence": {...}` plus `"theme"` in the night's index entry (`update_index` gains a param).
- `render_adherence_heatmap(index, today_cells, date) -> str`: HTML table (not SVG, for accessibility),
  last 14 nights *(my call: two weeks so each theme appears twice)*, newest first, today highlighted;
  cells carry a symbol as well as a colour (✓ ◐ ✗ ·) so it doesn't rely on colour alone. Placed
  after "This week's rotation".

## Rollout note

Old index entries have no `adherence`, so the heatmap fills in over the next two weeks (blank `·`
cells until then). B appears from the first night after the generator change ships.

## Left to the builder

- Exact chip sizes, label truncation, SVG layout maths.
- CSS class names (reuse existing tokens).
- Test shape: unit tests for `adherence_cells`, the diagram (escaping, missing data, un-aired story),
  the heatmap (missing history), and the generator's `storyboard_aired` payload.
