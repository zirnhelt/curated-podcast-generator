# Storyboard & Theme Adherence Visuals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Draw tonight's plan-vs-aired storyboard adherence (B) and a 14-night adherence heatmap (D) on the nightly review page.

**Architecture:** The script stage already computes each roundup story's first-mention turn and each beat's aired flag inside `storyboard_flow_line`; it now also saves them to the citations JSON as `storyboard_aired`. `episode_review.py` grades each night into kept/loose/drift cells, stores them in the night's `reviews/index.json` entry, and renders an inline-SVG diagram plus an HTML heatmap table on the page only.

**Tech Stack:** Python 3.11, stdlib only (`html.escape`, inline SVG, existing `PAGE` CSS tokens), pytest.

**Spec:** `docs/superpowers/specs/2026-11-27-storyboard-theme-visuals-design.md`

## Global Constraints

- No new API call; nothing new reaches a prompt.
- Heatmap/diagram never feed `run_signals` or the roadmap.
- Page only: `content_html` (RSS copy) is unchanged.
- No JS, no chart library; colours only via existing CSS vars (`--good`, `--warn`, `--bad`, `--muted`/`.note`), so dark mode follows.
- Every plan/model/article string in the SVG or table goes through `html.escape` (attribute values via `_attr`).
- Citations JSON keeps going through `atomic_write_json`; tests redirect `PODCASTS_DIR` / `REVIEWS_DIR` / `INDEX_FILE` into `tmp_path` and leave `git status` clean.
- Type hints on every function; PEP 8.

## Review Focus

1. A plan/article string containing `<`, `&` or `"` — escaped in chip text and `<title>`; no raw markup in output. (Task 4 test.)
2. Old nights: citations without `storyboard_aired`, index entries without `adherence` — diagram returns `""`, heatmap row shows `·` cells, no exception. (Tasks 3, 4 tests.)
3. A planned story that never aired (`turn` is `None`) — drawn greyed at the end of the aired row, connector dashed. (Task 4 test.)
4. Merged stories (`merged: true`) — one chip per lead; the lead's aired turn is the earliest of its group. (Task 4 test.)
5. Fidelity call failed (`None`) but flow data present — beat pips fall back to the counted booleans; the fidelity-derived cells are `none`. (Tasks 2, 4 tests.)

---

### Task 1: Generator saves `storyboard_aired`

**Files:**
- Modify: `podcast_generator.py` — `storyboard_flow_line` (~7928), `generate_citations_file` (~10410, storyboard block ~10547), call sites (~15659 segment `script/roundup-shape`, ~15738)
- Test: `tests/test_podcast_generator.py` (class holding `test_a_kept_plan_reads_clean`, ~8960; `test_citations_keep_the_plan`, ~2086)

**Interfaces:**
- Produces: `storyboard_flow(script: str, roundup: list, plan: dict | None) -> dict | None` — `None` when no plan, else
  `{"in_order": int, "pairs": int, "named": int, "bridges": int, "beats": list[bool], "roundup": list[{"title": str, "turn": int | None, "merged": bool}]}`.
  `roundup` is in the order of the `roundup` argument (the applied plan order); `merged = bool(a.get("_merged_into"))`.
- Produces: citations key `storyboard_aired` = `{"roundup": [...], "beats": [...]}` (the two lists above).
- `storyboard_flow_line` keeps its exact output; it becomes a formatter over `storyboard_flow`.

- [ ] **Step 1: Write failing tests**

```python
def test_flow_payload_records_turns_and_beats(self):
    import podcast_generator as pg
    beats = [{"claim": "The tanker ban caps crude cargo", "evidence": "12,500 tonnes north coast"},
             {"claim": "Heiltsuk governance argument", "evidence": "Coastal First Nations Slett"}]
    flow = pg.storyboard_flow(self.SCRIPT, list(reversed(self.ROUNDUP)), self._plan(beats))
    assert [r["turn"] for r in flow["roundup"]] == [2, 1, 0]
    assert flow["roundup"][0]["title"] == self.ROUNDUP[2]["title"]
    assert flow["beats"] == [True, False]
    assert flow["roundup"][0]["merged"] is False

def test_flow_payload_unplanned(self):
    import podcast_generator as pg
    assert pg.storyboard_flow(self.SCRIPT, self.ROUNDUP, None) is None
```
(Turn values are whatever `_article_first_mention` returns for the fixture — adjust the literal list to the real indices after Step 2 if they differ, keeping the reversed-order shape.)

In the citations test class:
```python
def test_citations_keep_the_aired_order(self, monkeypatch, tmp_path):
    import podcast_generator as pg
    monkeypatch.setattr(pg, "PODCASTS_DIR", tmp_path)
    aired = {"roundup": [{"title": "a", "turn": 0, "merged": False}], "beats": [True]}
    pg.generate_citations_file([], [], "Working Lands & Industry", storyboard_aired=aired)
    saved = json.loads(next(tmp_path.glob("citations_*.json")).read_text())
    assert saved["storyboard_aired"] == aired
```

- [ ] **Step 2: Run** `python -m pytest tests/test_podcast_generator.py -k "flow_payload or aired_order" -v` — Expected: FAIL (`storyboard_flow` / `storyboard_aired` missing).

- [ ] **Step 3: Implement.** Move the body of `storyboard_flow_line` into `storyboard_flow`; `storyboard_flow_line` returns `"🧩 Storyboard flow: unplanned"` on `None`, else formats the same string from the dict. Add `storyboard_aired=None` kwarg to `generate_citations_file`, saved next to `storyboard` when truthy. At the call site: pre-assign `_storyboard_aired = None` **before** `with segment("script/roundup-shape", critical=False)`; inside, compute `flow = storyboard_flow(...)` once, print the line from it, set `_storyboard_aired = {"roundup": flow["roundup"], "beats": flow["beats"]} if flow else None`; pass `storyboard_aired=_storyboard_aired` to `generate_citations_file`.

- [ ] **Step 4: Run** `python -m pytest tests/test_podcast_generator.py -k "storyboard or citations" -v` — Expected: PASS, including the three existing `storyboard_flow_line` tests unchanged.

- [ ] **Step 5: Commit** `feat(script): save storyboard aired order to citations`

---

### Task 2: Grade a night into adherence cells and store them in the index

**Files:**
- Modify: `episode_review.py` — new constants + `adherence_cells` near `review_metrics` (~920); `update_index` (~1595); `main` (~2342)
- Test: `tests/test_episode_review.py`

**Interfaces:**
- Produces: `ADHERENCE_COLUMNS: list[tuple[str, str]]` = `[("frame","Frame"), ("order","Order"), ("bridges","Bridges"), ("question","Question"), ("stances","Stances"), ("beats","Beats"), ("landing","Landing"), ("theme","Theme fit")]`
- Produces: `adherence_cells(facts: dict[str, Any], fidelity: dict | None) -> dict[str, str]` — every key of `ADHERENCE_COLUMNS`, value in `{"kept","loose","drift","none"}`.
- Produces: `update_index(..., adherence: dict[str, str] | None = None, theme: str = "")` stores `"adherence"` and `"theme"` on the entry.

Grading (constants at top of the block: `BEATS_KEPT_RATIO = 0.8`, `BEATS_LOOSE_RATIO = 0.5`, `THEME_DRIFT_COUNT = 2`):
| Column | kept | loose | drift | none |
|---|---|---|---|---|
| frame (`fidelity.roundup.frame`) | aired | changed | dropped | missing |
| order (`roundup.order`) | held | mostly held | changed | missing |
| bridges (`roundup.bridges`) | used | partly used | ignored | none planned / missing |
| question | asked | reworded | dropped | missing |
| stances | held | blurred | swapped | missing |
| landing | aired | changed | dropped | missing |
| beats (`facts.storyboard_flow[4]/[5]`) | ≥0.8 | ≥0.5 | <0.5 | no flow / planned 0 |
| theme (`facts.theme_fit[0]` off-charter, `[4]` unused) | both 0 | otherwise | either ≥2 | no theme_fit |

- [ ] **Step 1: Write failing tests**

```python
def test_adherence_cells_grade_a_clean_night():
    fid = {"roundup": {"frame": "aired", "order": "held", "bridges": "used"},
           "question": "asked", "stances": "held", "landing": "aired"}
    facts = {"storyboard_flow": [4, 4, 2, 4, 5, 5], "theme_fit": [0, "", "p80", 3, 0]}
    assert set(er.adherence_cells(facts, fid).values()) == {"kept"}

def test_adherence_cells_grade_drift_and_loose():
    fid = {"roundup": {"frame": "dropped", "order": "mostly held", "bridges": "none planned"},
           "question": "reworded", "stances": "swapped", "landing": "changed"}
    facts = {"storyboard_flow": [1, 4, 0, 3, 2, 5], "theme_fit": [1, "x", "p40", 1, 2]}
    assert er.adherence_cells(facts, fid) == {
        "frame": "drift", "order": "loose", "bridges": "none", "question": "loose",
        "stances": "drift", "beats": "drift", "landing": "loose", "theme": "drift"}

def test_adherence_cells_without_fidelity_keep_counted_columns():
    cells = er.adherence_cells({"storyboard_flow": [3, 4, 1, 3, 3, 5], "theme_fit": [1, "x", "p60", 2, 0]}, None)
    assert cells["beats"] == "loose" and cells["theme"] == "loose"
    assert all(cells[k] == "none" for k in ("frame", "order", "bridges", "question", "stances", "landing"))

def test_update_index_stores_adherence(monkeypatch, tmp_path):
    monkeypatch.setattr(er, "INDEX_FILE", tmp_path / "index.json")
    er.update_index("2026-11-27", "t", "c", adherence={"frame": "kept"}, theme="Wild Spaces")
    entry = json.loads((tmp_path / "index.json").read_text())[0]
    assert entry["adherence"] == {"frame": "kept"} and entry["theme"] == "Wild Spaces"
```

- [ ] **Step 2: Run** `python -m pytest tests/test_episode_review.py -k "adherence" -v` — Expected: FAIL.

- [ ] **Step 3: Implement** `adherence_cells` (a dict lookup per fidelity column; treat non-dict `fidelity`/`roundup` as missing) and the `update_index` params. In `main`, compute `cells = adherence_cells(facts, fidelity)` once and pass `adherence=cells, theme=facts.get("theme_feed") or facts.get("theme_configured") or ""`.

- [ ] **Step 4: Run** `python -m pytest tests/test_episode_review.py -v` — Expected: PASS.

- [ ] **Step 5: Commit** `feat(review): grade storyboard and theme adherence per night`

---

### Task 3: Adherence heatmap (D)

**Files:**
- Modify: `episode_review.py` — `render_adherence_heatmap` after `render_cycle` (~1192); `build_review` (~1491) gains `adherence` param and places it right after `render_cycle(...)` in `page_body`; CSS in `PAGE` (~1013); `main` passes `adherence=cells`
- Test: `tests/test_episode_review.py`

**Interfaces:**
- Consumes: `ADHERENCE_COLUMNS`, index entries' `"adherence"`/`"theme"` (Task 2).
- Produces: `render_adherence_heatmap(history: list[dict], today: dict[str, str], date: str, theme: str) -> str`; `HEATMAP_NIGHTS = 14`; `build_review(..., adherence: dict[str, str] | None = None)`.

Behaviour: rows = today (from `today`/`theme`) plus history entries dated before `date`, newest first, at most `HEATMAP_NIGHTS`. Row label `"{Ddd} {MM-DD} · {theme}"`. Cell `<td class="adh adh-{grade}" title="{Column}: {grade}">{symbol}</td>` with symbols kept `✓`, loose `◐`, drift `✗`, none `·`; today's row `class="today"`. Wrapped in `<div class="table-wrap">`, heading `<h2>Plan and theme, night by night</h2>`, a one-line legend. Returns `""` when no row has any non-`none` cell. CSS: `.adh-kept{color:var(--good)} .adh-loose{color:var(--warn)} .adh-drift{color:var(--bad)}`, `.adh{text-align:center}`.

- [ ] **Step 1: Write failing tests**

```python
def test_heatmap_rows_newest_first_with_old_entries_blank():
    history = [{"date": "2026-11-26", "theme": "Indigenous Lands", "adherence": {"order": "drift"}},
               {"date": "2026-11-25", "theme": "Gear"}]  # pre-feature entry
    html = er.render_adherence_heatmap(history, {"order": "kept"}, "2026-11-27", "Wild <Spaces>")
    assert html.index("11-27") < html.index("11-26") < html.index("11-25")
    assert "Wild &lt;Spaces&gt;" in html and "adh-drift" in html and "·" in html

def test_heatmap_caps_at_fourteen_nights():
    history = [{"date": f"2026-11-{d:02d}", "adherence": {"order": "kept"}} for d in range(1, 27)]
    html = er.render_adherence_heatmap(history, {"order": "kept"}, "2026-11-27", "")
    assert html.count("<tr") == er.HEATMAP_NIGHTS + 1  # + header row

def test_heatmap_empty_when_no_data():
    assert er.render_adherence_heatmap([], {k: "none" for k, _ in er.ADHERENCE_COLUMNS}, "2026-11-27", "") == ""

def test_heatmap_is_page_only(...):  # build_review: heatmap in page, not in content
```
(The last test: call `build_review` with a minimal `facts`/`runs` the existing build_review tests use, `adherence={"order": "kept"}`; assert `"Plan and theme, night by night"` in `page` and not in `content`.)

- [ ] **Step 2: Run** `python -m pytest tests/test_episode_review.py -k heatmap -v` — Expected: FAIL.
- [ ] **Step 3: Implement** per the behaviour above.
- [ ] **Step 4: Run** `python -m pytest tests/test_episode_review.py -v` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(review): 14-night storyboard and theme adherence heatmap`

---

### Task 4: Plan-vs-aired diagram (B)

**Files:**
- Modify: `episode_review.py` — `load_storyboard_aired` next to `load_storyboard` (~1206); `render_plan_diagram` before `render_plan` (~1276); `build_review` places it immediately before `render_plan(...)` in `page_body`; CSS for `svg.plan`
- Test: `tests/test_episode_review.py`

**Interfaces:**
- Consumes: citations `storyboard_aired` (Task 1), `storyboard` plan, fidelity dict.
- Produces: `load_storyboard_aired(date: str) -> dict | None` (via `_citations_key(date, "storyboard_aired", dict)`); `render_plan_diagram(plan: dict | None, aired: dict | None, fidelity: dict | None) -> str`.

Behaviour:
- Returns `""` when `plan` or `aired` is missing or `aired["roundup"]` is empty.
- Stories = leads (`merged` false) of `aired["roundup"]` in list order; each lead owns the merged entries that follow it; its turn = min non-None turn of the group, else `None`.
- Top row: leads in that order. Bottom row: leads sorted by turn, `None` last. Chip label = article title with a leading `[Outlet] ` stripped, cut to ~28 chars with `…`; full title in `<title>`.
- Connector per lead top→bottom; class `cross` when its top/bottom ranks invert against another lead's (i.e. it's not in order), `miss` (dashed, greyed) when turn is `None`, else `ok`.
- Bridge tick between top-row chips i and i+1 when `plan.roundup.stories[i].bridge_to_next` is a named bridge (not `clean pivot`/`handoff`/empty); class from fidelity `roundup.bridges` (`used` → ok, else loose); absent fidelity → neutral.
- Beat pips: one per beat; status from `fidelity.beats[j].status` (aired/changed/dropped) when present, else `aired["beats"][j]` (True → aired, False → dropped).
- Three chips — Question / Stances / Landing — from fidelity, mapped with Task 2's grading; omitted when fidelity is `None`.
- `<svg class="plan" viewBox="0 0 W H" role="img" aria-label="{n} stories, {k} aired in plan order; {a}/{b} beats aired">`; width scales with story count (viewBox only, CSS `width:100%`). Heading `<h2>Plan vs. aired</h2>` plus a one-line legend.

- [ ] **Step 1: Write failing tests**

```python
AIRED = {"roundup": [{"title": "[CBC] Wildfire <update>", "turn": 0, "merged": False},
                     {"title": "[Tribune] Broadband & towers", "turn": 4, "merged": False},
                     {"title": "[Observer] Broadband too", "turn": 3, "merged": True},
                     {"title": "[CBC] Clinic hours", "turn": 2, "merged": False},
                     {"title": "[CBC] Mill closes", "turn": None, "merged": False}],
         "beats": [True, False]}
PLAN = {"roundup": {"stories": [{"bridge_to_next": "fire to towers"}, {"bridge_to_next": "clean pivot"},
                                {"bridge_to_next": "clean pivot"}, {"bridge_to_next": "handoff"}]},
        "deep_dive": {"beats": [{}, {}]}}

def test_diagram_escapes_and_draws_one_chip_per_lead():
    svg = er.render_plan_diagram(PLAN, AIRED, None)
    assert "<update>" not in svg and "Wildfire &lt;update&gt;" in svg
    assert "Broadband too" not in svg          # merged report has no chip
    assert svg.count('class="chip') == 2 * 4   # 4 leads, top + bottom

def test_diagram_marks_unaired_and_reordered():
    svg = er.render_plan_diagram(PLAN, AIRED, None)
    assert 'class="link miss"' in svg           # Mill never aired
    assert 'class="link cross"' in svg          # Broadband (turn 3) after Clinic (turn 2)

def test_diagram_beats_fall_back_to_counted():
    svg = er.render_plan_diagram(PLAN, AIRED, None)
    assert svg.count('class="pip aired"') == 1 and svg.count('class="pip dropped"') == 1
    assert "Question" not in svg

def test_diagram_uses_fidelity_when_present():
    fid = {"beats": [{"beat": 1, "status": "changed", "note": ""}, {"beat": 2, "status": "aired", "note": ""}],
           "question": "asked", "stances": "blurred", "landing": "aired", "roundup": {"bridges": "used"}}
    svg = er.render_plan_diagram(PLAN, AIRED, fid)
    assert 'class="pip changed"' in svg and "Stances" in svg

def test_diagram_empty_for_old_nights():
    assert er.render_plan_diagram(PLAN, None, None) == ""
    assert er.render_plan_diagram(None, AIRED, None) == ""
```

- [ ] **Step 2: Run** `python -m pytest tests/test_episode_review.py -k diagram -v` — Expected: FAIL.
- [ ] **Step 3: Implement** per the behaviour above; CSS classes `.chip`, `.link.ok/.cross/.miss`, `.pip.aired/.changed/.dropped`, `.bridge` using `var(--good)/var(--warn)/var(--bad)` and the muted text colour.
- [ ] **Step 4: Run** `python -m pytest tests/test_episode_review.py -v` — Expected: PASS.
- [ ] **Step 5: Visual check.** Render a page from today's real citations with `--no-llm` (`python episode_review.py --date 2026-11-27 --no-llm` or call `build_review` in a scratch script writing to the scratchpad, never to `podcasts/reviews/`), screenshot light + dark at 390 px and 1200 px wide with Playwright; confirm no horizontal page scroll and legible chips. Then `git status` — revert anything under `podcasts/`.
- [ ] **Step 6: Commit** `feat(review): plan-vs-aired storyboard diagram`

---

### Task 5: Rules, decision record, full suite

**Files:**
- Modify: `.claude/rules/editorial-voice.md` (storyboard-check bullet), `docs/decisions/editorial-voice.md`

- [ ] **Step 1:** Append to the "script is checked against its storyboard" bullet: "`storyboard_aired` (citations) feeds the page's plan-vs-aired diagram, and each night's graded cells (`adherence_cells`) ride in `reviews/index.json` for the 14-night heatmap; both are page-only and never signals." Add a dated (2026-11-27) short entry to the decision record giving the why (producer wanted adherence visible, not just stated; no new API spend).
- [ ] **Step 2: Run** `python -m pytest tests/ -q` — Expected: all pass. Then `git status` — no `podcasts/` changes.
- [ ] **Step 3: Commit** `docs: record the adherence visuals` and push to `claude/intelligent-lovelace-q6m486`.
