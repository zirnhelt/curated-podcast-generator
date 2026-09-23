# Curation: themes, the civic event, the roundup and the super-cycles

*Moved verbatim from CLAUDE.md on 2026-09-23. CLAUDE.md keeps the standing rules; this file keeps the incidents and the reasoning behind them. Read it before changing the code it describes.*

## Themes

Seven rotating daily themes indexed by weekday (0=Mon):
- 0 Mon: Arts, Culture & Digital Storytelling
- 1 Tue: Working Lands & Industry
- 2 Wed: Gear, Gadgets & Practical Tech
- 3 Thu: Indigenous Lands & Innovation
- 4 Fri: Wild Spaces & Outdoor Life
- 5 Sat: Cariboo Local Affairs (longer episode, 15 articles) — the one **geographic** theme (`geographic: true`), defined by where a story is rather than what it is about
- 6 Sun: Science, Wonder & the Natural World

**The geographic theme.** Saturday is the only theme defined by *where* a story is rather than
what it is about, flagged `geographic: true` with its place names listed in `place_keywords`.
Every other theme can let place names carry theme relevance; this one cannot, because every
candidate in its pool is local by construction — a place-name hit is a constant, not a
discriminator. `_build_theme_subject_keywords` strips the places (and the theme name, which
contributes a place and the bare word `local`) and leaves the civic vocabulary — `council`,
`bylaw`, `zoning`, `budget`, `referendum` — which is what ranks the deep dive and what gates the
roundup's `theme` block. Ranking on locality picked whichever local story named the most towns:
on 2026-08-22 that was a softwood-duty story, a ranching award and a Tyson beef-plant closure,
and the debate that came out of them was a Working Lands debate on a civic-affairs day.

**Locality is not centrality, and the flat place list could not tell them apart.**
`local_places` answers "is this story here?"; `home_places` (themes.json, geographic day only)
answers "is this story *ours*?". Williams Lake, the CRD and SD27 are the jurisdictions the show
is from; Quesnel, 100 Mile House, Bella Coola and Prince George are neighbours it covers. With
one flat list both questions had one answer, so the 2026-09-05 deep dive ran on Quesnel's
winter-shelter siting and a Quesnel council candidate — both civic, both local, and neither
Williams Lake. `_home_place_hits` ranks above the civic-keyword *count* in `_geographic_rank`
and leads the roundup's `local` block sort, so a denser Quesnel story no longer outranks a
thinner Williams Lake one. **It is an ordering rule, not an exclusion** — the neighbour story
still airs, and subject matter still gates entry, so this promotes a home *civic* story and
never a home speedway story. `home_places` is absent from the six topical themes, where the
term is a constant and changes no order.

**`event_focus` — a named civic event, bounded by dates rather than a rotation.** The fourth
selection layer (theme → super-cycle focus → weekly anchor → event), resolved by
`get_event_focus_for_day` from the theme's own `start`/`end`. Calendar-derived like
`get_focus_for_day`, so a re-render weeks later reproduces the same answer and the window needs
no cleanup commit when it closes. Currently the Williams Lake 2026 general local election
(nominations closed Sept 11, voting day Oct 17, window to Oct 24).

- **It is named on air, and that is the opposite of the focus rule.** A super-cycle focus is a
  curation device listeners have no reason to hear about; an election is the civic fact the
  coverage exists to serve. Its `lens` copy carries both the say-it-on-air instruction and the
  no-endorsement rule — report the race and the candidates' stated positions, never rank them.
- **An event match counts as civic subject matter on its own**, so a nomination story with no
  theme keyword still anchors the debate. The event vocabulary is deliberately **not** folded
  into `_build_theme_subject_keywords`, which also gates the roundup's `theme` block against the
  whole non-local pool: 'campaign', 'ballot' and 'candidate' would admit US politics to a
  Cariboo civic day exactly the way the bare word 'local' admitted "8 local AI models that run
  great on 8GB of VRAM" on 2026-08-22. Inside the geographic deep dive every candidate is
  already local by construction, which is what makes those words safe there and only there.
- **An election story airs twice, and the second airing says so.** Local news is never
  held — it is the most time-sensitive material in the pool — so a nomination story breaking
  on a Tuesday runs on Tuesday. It is *also* booked back for the next civic episode
  (`status: 'recall'` in `article_holding.json`, released by `route_articles_for_focus` with
  `_recalled_from`). Both dates are the point: it is news on the day it breaks and context on
  the day the show covers the race, so nothing is withheld from today to pay for Saturday.
  - **The recall is exempt from the citation prune, alone among the statuses.** Every other
    entry is dropped once its URL appears in recent citations; a recall exists *because* the
    story already aired, so that rule would delete the entry on the next run. Re-injection
    happens after `deduplicate_articles`, which is what lets a spent story return at all.
  - **`_recalled_from` is the inverse of `_held_from`.** A held story is one the listener has
    not heard, so explaining its timing would only expose the machinery; a recalled one they
    *have* heard, so pretending otherwise is the failure. The tag tells the hosts to say they
    covered it and lead with what has changed — a new name in the race, a deadline passed.
  - **It is booked before the on-theme early-exit**, which the router returns through for
    anything matching today's theme. An election story is usually on-theme wherever it lands
    (a mill-town candidate on Working Lands day), so a recall gated behind "off today's
    theme" would almost never fire.
  - **Two event lookups, two questions.** `get_event_focus_for_day` asks "is TODAY'S theme
    running an event" and steers the lens and the deep-dive ranking. `get_active_event_focus`
    asks "is one running at all" — the election is configured on the civic theme, so a
    Tuesday lookup by weekday returns None and nothing would ever be booked back.
  - Both `_is_local_article` and an event-keyword hit are required, so US midterm coverage
    (same vocabulary, not local) is never recalled.

- **Selecting the story was never the hard part — reporting it was.** The selection layers
  above put the right articles in the deep dive on 2026-09-12 and the segment still failed the
  listener: it reported a mayoral candidate's **2014** win (60.4%) as his standing today
  ("that's a mandate", "carrying a landslide from his previous term"), never mentioned that his
  actual last run ended in a third-place loss, and called the sitting mayor "the sitting
  incumbent" for the whole segment while its own source carried the name. Nothing was
  fabricated and nothing was selected wrong. The record simply was not asked for, and half the
  debate went to the week's anchor question instead of the race.
  - **The lens carries the reporting rules, not just the no-endorsement rule.** Name every race
    on the ballot (city mayor, council, the CRD electoral area director, the SD27 trustee — a
    race the episode never names is a race it did not cover); name people rather than roles;
    report a previous run's outcome, *loss included*, with the most recent result treated as
    the load-bearing one; treat a documented controversy, ethics finding, censure or
    resignation as part of the record rather than as an attack. **Omission is not neutrality**
    — reporting only the win is a thumb on the scale in the incumbent-challenger direction.
  - **Every one of those rules is bounded by SOURCED OR UNSAID**, which is what keeps them from
    becoming an invitation to characterize a real person. A claim comes from the day's articles
    or the research block and says where it was reported; an allegation is never rounded up
    into a finding; an unestablished record is said out loud to be unestablished. The failure
    mode of the old lens was omission; the failure mode of a record rule with no sourcing
    clause would be invention, which is worse.
  - **The anchor yields.** A named civic event is what the coverage exists to serve, so when
    the week's anchor question does not genuinely fit the race the lens tells the segment to
    drop it rather than bend around it — the same escape hatch `weekly_anchor` already carries,
    spent here deliberately.
  - **`event_focus.research` is the half that makes the rest reachable.** None of a candidate's
    record is in a nomination-day story, so a lens demanding it would otherwise produce nothing
    but "the show has not established that". The brief turns `research_deep_dive_with_agent`
    from a judgement call ("is research warranted?") into a standing roster sweep — prior
    offices, every previous result won *and* lost, the record in office, any documented
    controversy, and the incumbent looked up **by name in every race**, including the CRD
    directors and SD27 trustees the articles may only mention in passing. It ends by listing
    the candidates it could *not* source, which is what lets the hosts say so on air.
  - **The sweep costs one widened budget.** `EVENT_RESEARCH_SEARCH_LIMIT` (8) replaces the
    ordinary day's 4 only when a `research` brief is present, and
    `BRAVE_DEEP_DIVE_CALL_LIMIT` moved 10→16 so it does not starve
    `_resolve_script_questions_with_brave`, which runs after it on the same meter. It is a
    ceiling, not a floor — the other six days ask exactly what they asked before. At $5/1000
    Search requests over ~6 election Saturdays the whole widening is under a dime against the
    $10 monthly limit. There is a test asserting the headroom, because raising the sweep
    without raising the meter would silently spend the script-question pass instead.
  - **`home_places` carries the CRD electoral areas** (D, E, F and their communities) for the
    same reason it carries Williams Lake: those directors are on the listener's own ballot, so
    an area-director story must rank as *ours* rather than as neighbour coverage.
  - **An instruction to name people met a prompt with no names in it.** The lens has demanded
    every candidate by name since it was written, and on 2026-09-19 the deep dive still opened
    on "three regional director seats we straight up can't name a single candidate for" and
    could not say whether 100 Mile House's mayor had filed. Nothing selected wrong and nothing
    was fabricated — a nomination-day article reports a *count*, the research sweep can only
    look up names it was given, and the producer's filed list sat in
    `docs/wl-2026-election-candidates.md` marked "recognize, don't cite", read by nothing.
    `event_focus.roster` is that list as data, rendered by `_format_event_roster` into the
    deep-dive lens and into the research sweep's standing assignment.
    - **It settles who is running and nothing else**, and the block says so in the prompt.
      A record, a prior result, a platform or a controversy is still SOURCED OR UNSAID — a
      name on a nomination form is a source for the name and for nothing after it. That
      boundary is what makes the roster safe to inject at all: the failure it fixes is
      hedging, and the failure a wider roster would buy is invention, which is worse. The
      platform notes stay in the doc and are deliberately absent from the JSON.
    - **A race with no names renders as `NO FILED LIST`, not as nothing.** The ballot has four
      races on it whether or not the show has four filed lists, so the lens names the race,
      says in one plain sentence that the show does not have its candidates, and the sweep is
      told to spend a search there before a fourth search on a name the articles already
      cover. Said once inside the segment that is honest reporting; the 09-19 cold open turned
      it into the episode's hook, which is why `cold_open_generation` now forbids teasing an
      absence.
    - **Two copies drift**, so `tests/test_podcast_generator.py` asserts the doc and the JSON
      carry the same names — the lesson from this file's own claims about `super-rss-feed`.
      Nothing else needs cleanup: `event_focus` is date-bounded, so the roster stops being
      injected when the window shuts on Oct 24.

- **The downstream ranking cannot select what the feed never sent.** On 2026-09-05 the pool held
  "Three Williams Lake city councillors not seeking re-election this fall" (Williams Lake
  Tribune) and "Municipal elections nominations now open across the Cariboo" (My Cariboo Now)
  — and the first scored **11** on the Saturday theme charter against Saturday's `min_score` of
  18, so it never reached the feed at all. That is `super-rss-feed`'s gotcha 14 (joint-scoring
  collapse) landing on the day it matters most; the upstream half of this change adds Saturday
  to `targeted_rescore` with the Cariboo outlets as its `rescore_sources`. **When a Saturday
  deep dive looks wrong, check the upstream theme score before touching the ranking.**
  — **and that upstream half was never applied.** It is described above as though it shipped;
  `targeted_rescore.days` in `super-rss-feed` read `["tuesday", "wednesday"]` until 2026-09-17,
  with no `rescore_sources` on Saturday at all. A note in this file is not a change in the
  other repo, and nothing checks that the two agree — when a claim here is about
  `super-rss-feed`, read `config/podcast_schedule.json` there before trusting it.

## News Roundup Curation (`_annotate_roundup_blocks`, `_curate_roundup_pool`, `_sequence_roundup`)

The roundup's story count is derived from **airtime, not appetite**. The segment gets
~1,100–1,300 words of a 3,400-word script, and every story owes the listener what happened,
why it matters and the rural angle — `ROUNDUP_MIN_STORY_WORDS` (70) is the floor that takes.
`NEWS_ROUNDUP_COUNT` (15) is that budget divided by that floor. **A story that cannot be given
its floor is cut, never compressed**, and the prompt states the segment's word target so a
shorter list produces deeper stories rather than a shorter segment.

`NEWS_ROUNDUP_COUNT` bounds the **whole segment, bonus picks included**. It used to bound the
theme pool alone: `_curate_roundup_pool` returned `protected + kept_fill + bonus` and
`generate_podcast_script` then concatenated the full pre-curation bonus list back in. On
2026-08-13 that put 52 stories in a 1,237-word roundup — 24 words each, a headline crawl
("Archaeologists in Sweden uncovered a 9,000-year-old burial. A pistachio butter was recalled.
Ransomware operators are targeting managers."). Every coherence mechanism — blocks, cluster
adjacency, the no-forced-segue rules — ran on the 15 and was bypassed by the 37.

**`all_articles` is the curated pool and is authoritative.** The `bonus_articles` parameter to
`generate_podcast_script` is the *pre-curation* list; concatenating it back in re-admits
everything the cap just dropped.

Blocks, in airing order — curation metadata the hosts never name on air:

| Block | Contents |
|-------|----------|
| `local` | Cariboo/BC place name or regional outlet. Opens the show. Bonus picks are eligible — geography is orthogonal to the feed's theme judgment |
| `theme` / `theme_adjacent` | Net-positive theme relevance; `_adjacent` matches in the body only. Never bonus picks — the feed already made that call |
| discipline groups | Off-theme stories with ≥2 same-field siblings, kept adjacent so the back half plays as mini-arcs |
| `standalone` | Connects to nothing; the weakest material in the segment |
| `kicker` | One standalone, aired last, told properly — the roundup's deliberate closer |

**A themed episode whose roundup carries nothing on its theme now says so.** That is the
failure this whole selection stack exists to prevent, and until 2026-09-17 it was not an
error, a warning or a row: that day's blocks were `local:5, community_life:3,
life_sciences:3, physical_sciences:3, kicker:1` on Indigenous Lands day — fifteen stories,
empty theme block — and the run went green. `script/curate` `degrade()`s below
`ROUNDUP_THEME_FLOOR` on-theme stories, and again when the feed itself hands over
`THEME_POOL_FLOOR` (6 = the deep dive's 3 plus the roundup's 3, which do not share) or fewer
theme articles. The geographic day is exempt from both: it has no theme block by
construction, which is the whole point of `_geographic_rank`.
**Read the degradation as a scoring problem, not a supply problem** — on the day it fired,
the feed held 65 bonus articles against 4 theme ones and APTN was publishing daily. See
`super-rss-feed` gotcha 14, and check the theme's argmax share before touching `feeds.opml`.
Note `ROUNDUP_THEME_FLOOR` already existed but could only reserve theme slots *within*
`protected` when the arc overflowed the cap — it had nothing to floor when the theme block
was empty, which is the commoner failure and the one that was silent.

The **kicker** is why cutting the tail is an edit rather than a shortfall. Standalones used to
be read out at a sentence apiece; one of them given real airtime is worth more than ten
mentioned. It reserves its slot before the tail spends the budget, and yields it when the
protected arc alone fills the segment.

**No single discipline cluster may take more than `ROUNDUP_CLUSTER_MAX` (3) slots**, and that
holds whether or not the pool is over the cap. On 2026-08-22 the Cariboo Local Affairs roundup
sat exactly at its cap of 15 and still ran a seven-story US pharma and health-policy cluster
against two local stories and one theme story — which had qualified on the word "local"
("Scientists Saw Strange Spots on Local Fish"). Nothing bounded one field's share of a segment,
so the cap alone let the day's identity be decided by whatever the feed happened to be heavy in.
A cluster is kept adjacent so the back half plays as a mini-arc; past three it stops being an arc
and becomes what the episode is about. The overflow is dropped, never compressed — dropped
articles never reach citations, so dedup lets them resurface on a better-matched day.

**Blocks decide what airs together; `_sequence_roundup` decides the order inside one.** The
discipline clustering above ran on the tail alone — `local` and `theme` were sorted by
place-name density and keyword density respectively, neither of which says anything about what
a story is *about*. On 2026-09-06 the pool was 8 local + 7 theme, the tail was empty, and so no
coherence mechanism ran at all: the roundup aired a power outage, a charity ride, a library
opening, a cancer ride and a wildfire crew story in that order. `check_roundup_order` passed —
it compares block ranks, and there were two.

- **The pass is a chain, not a regroup.** Each story pulls its same-discipline siblings up
  behind it; nothing is promoted ahead of a story it did not already sit behind. So the lead
  never moves, which is the point: the first article of `local` is the show's front door and
  the first of `theme` is the day's strongest on-theme story, and a wholesale regroup would let
  a two-story cluster take either slot.
- **`ROUNDUP_CLUSTER_MAX` deliberately does not apply inside an arc block.** The cap exists to
  stop off-theme filler deciding what the episode is about; a fire week's local block *is* the
  episode.
- **It runs at both consumers** — `_curate_roundup_pool`'s return and the re-annotation in
  `generate_podcast_script`. The second is the prompt's only view of the order, so sequencing
  only the first would change the citations and leave the air order untouched.
- **`disciplines.json` had no civic vocabulary and needed one.** The taxonomy was written for
  the off-theme tail, which is science and tech feed material; the local block is outages,
  wildfire, council, fundraisers, schools, health and highways, and none of it could group.
  `public_safety`, `civic_affairs` and `community_life` are that vocabulary.
- **`_infer_discipline` counts word-boundary hits** (`_keyword_hit_count`), not substrings.
  Plain `in` matching filed "Traffic-pattern changes coming for Highway 1 at Mount Lehman"
  under astrophysics, because 'star' is inside "starting" — harmless while this only sorted the
  tail, and not harmless once it decides which stories air next to each other.

**A local election story can break on any day, and until 2026-09-15 it carried none of the
election-accuracy rules.** `event_focus`'s "NAME PEOPLE, NOT ROLES" and record-sourcing
instructions (see the `event_focus` section below) are built into `_build_theme_lens` and
injected only into the Deep Dive prompt, only on the weekday the event is configured for —
Saturday. A nomination story is local news and is never held (see Article holding above), so it
airs in the roundup the day it breaks, which for a multi-town nomination sweep is whatever
weekday the wire ran it — with zero of those rules in scope. On 2026-09-15 (a Tuesday) the
roundup covered both the Williams Lake and South Cariboo mayoral nominations back to back and
merged them: it reported Walt Cobb's Williams Lake opponent as "a South Cariboo realtor" — that
candidate (David Jurek) runs in the separate 100 Mile House race — and never named Surinderpal
Rathor, the actual Williams Lake incumbent the source article named. The fact-check pass could
not have caught it either: `TASK 2` in `polish_and_factcheck` explicitly scopes itself to the
Deep Dive and carves the News Roundup out of scope but for two named exceptions (bill substance,
leadership-race winners) — candidate-to-race attribution wasn't one of them.
**LOCAL ELECTION RACES** is now a standing roundup rule in `script_generation_system`
(unconditional, not gated behind `event_focus`'s weekday): keep each town's race distinct, name
a sourced incumbent or opponent instead of leaving them as "the sitting incumbent," and never
borrow a name from a different town's race. A third fact-check exception,
**LOCAL ELECTION CANDIDATE ATTRIBUTION**, backs it up in both `polish_and_factcheck` and
`agentic_polish_and_factcheck` — the roundup is otherwise off-limits to the fact-check pass, but
a candidate pairing gets checked against the verified sources like a bill's substance or a
leadership race's winner do.

**The segment used to read out its own filing system.** `_NEVER_ANNOUNCE` has kept block
*names* off the air since 2026-08-11, but the COVERAGE CUE rule opposite it asked the opening
line to reflect "how much ground there is", explicitly "(count and depth of sourcing)" — and
the ◆ headers hand the model the counts. So the rule and the ban were pulling opposite ways,
and the rule won: "Six stories close to home to start, one that ties straight into today's
theme, and a longer tail after that", "Fifteen stories in the queue today", "a full docket
today". That is the running order narrated. The cue is editorial framing now — what kind of
day it is out there — with counts, container nouns ("docket", "queue", "batch", "lineup") and
the running order named as things never to say, and `_NEVER_ANNOUNCE` says the header count is
a pacing budget. **Where the stories sit is still fair game**: "a lot of it close to home
today" is a fact about the news, not about how the file was sorted, and cutting it would cost
the roundup its one honest opening move.

Two prompt rules carry the rest: **NO HEADLINE CRAWL** (never stack unrelated stories into one
host turn as one-sentence mentions) and **DO NOT MANUFACTURE CONNECTIONS** — an abstract bridge
that could join *any* two stories ("from one contested piece of land to another", "whoever
controls the categories controls what counts") sounds like insight and carries none. The escape
hatch matters more than the prohibition: if the shared thing can't be named in plain words,
there is no thread, and the hosts just move on.

## Super Cycles (`config/super_cycles.json`)

Each daily theme (except Saturday, deliberately uncycled) rotates through a multi-week **focus** — e.g. Tuesday cycles agriculture → forestry → mining → tourism, one focus per week. Friday runs a 3-week cycle, all other cycled days 4-week. The cycle position is calendar-derived (`(date.toordinal() // 7) % cycle_length` per weekday via `get_focus_for_day`) — stateless, idempotent on re-runs, predictable ahead of time.

- **Selection:** the deep dive prefers focus-matching articles; a thin focus week (<3 matches) degrades to plain theme selection (logged `focus_fallback`). The focus lens is appended to the theme lens in the script prompt.
- **Subtlety:** the focus is deliberately unannounced on air — it shapes selection and emphasis only. Hosts name and acknowledge the weekday theme, never a rotating sub-theme; every focus-derived prompt block carries a do-not-announce instruction.
- **Article holding (`route_articles_for_focus`):** off-theme, non-urgent articles matching an upcoming day within 14 days are held in `podcasts/article_holding.json` and released (flagged `_held_from`, framed as "earlier this week") on that day. Urgent ones (`_boosted_score ≥ 85`) air same-day in the bonus bucket (never deep-dive) and are remembered in the aired-early ledger for an on-air callback when their day arrives. Holding never shrinks the pool below the roundup + deep-dive budget, and **never holds a local story** — local news is the most time-sensitive material in the pool and geography is orthogonal to the rotation (`_is_local_article`, shared with the roundup's `local` block).
  - **Both buckets are routed.** The hold loop used to iterate `theme_articles` alone — the one bucket that by definition holds nothing off-theme. Off-theme material arrives in `bonus_articles`: 72 of it against 8 theme articles on 2026-08-17, so nothing was ever eligible and Monday's roundup aired a PLA-brittleness piece that scored two hits on Wednesday's Maker & Repair focus keywords — enough for the old focus-only matcher, which never saw it.
  - **A slot matches on its theme keywords OR its focus keywords**, and `get_upcoming_day_slots` emits a slot for every upcoming day. Focus-only matching missed whole categories: forestry is a Tuesday theme keyword every week but only reaches a Tuesday slot on the weeks the rotation sits on Forestry, so the 2026-08-17 lumber-tariffs opinion piece scored 0 focus hits on all 14 upcoming days (3 against Tuesday's theme) and had nowhere to go. The theme is the day's standing identity; the focus only narrows it.
  - **Keyword sets that gate a decision are strict** (`_build_strict_theme_keywords`): theme-name words plus the explicit config keywords, never the description prose. `_build_theme_keywords` folds every word of the description in, which is fine for *ranking* — an extra fuzzy hit only moves an article up a list — and wrong for a gate. Saturday's description contributed `that`, `shape`, `everyday` and `life`, so nothing in the pool could read as weak on today's theme and the router had 42 keywords to match a slot on.
  - **A geographic day is never a routing target** (`_is_geographic_theme`, themes.json `geographic: true`). Cariboo Local Affairs is defined by *where* a story is; every other theme is defined by what it is about. Geography is decided by `_is_local_article`, which also exempts local stories from holding, so the day has no import channel to fill and every match it wins is a false one. Its keyword list took the bare word `local` literally: five articles were waiting for 2026-08-22 — New York's housing shortage, a Brooklyn ADU that "follows local and zoning laws", two US drug-pricing pieces, and "8 local AI models that run great on 8GB of VRAM". Two of them aired.
  - **A local story that belongs to another day airs today and defers its deep dive.** It is never held — local news stays the most time-sensitive material in the pool — but when it carries none of today's *subject* keywords and answers an upcoming day's theme, it gets `_no_deep_dive` and an aired-early ledger entry so the callback lands on the day whose question it actually answers. On 2026-08-22 the Cariboo Local Affairs deep dive ran on softwood duties, a ranching award and a Tyson beef-plant closure — Tuesday's episode, aired on Saturday and spent for the week by dedup, because every one of them is local and locality was the whole score.
  - **`_no_deep_dive` is now read.** It was written by the router and read by nothing: `_ensure_deep_dive_substance` was free to swap back into the deep dive exactly what the router kept out of it. `select_deep_dive_from_feed` holds flagged articles back, and restores them only below `DEEP_DIVE_ELIGIBLE_FLOOR` (2) — a debate with no sources is a worse failure than a debate one day early.
  - **A released article carried the labels of the day it was held on, and both consumers
    read them** (`_relabel_for_day`). `_keyword_matches`, `_is_bonus`, `_theme_score` and
    `_theme_score_raw` are computed by the feed *relative to whichever day the article
    arrived on*, and the holding pen stores that snapshot verbatim. So a story held on
    Wednesday *because it is Thursday material* was released on Thursday still stamped with
    Wednesday's verdict that it is off-theme. `_annotate_roundup_blocks` tests `_is_bonus`
    before it tests theme relevance, and `select_deep_dive_from_feed` split on
    `_keyword_matches > 0` — and upstream defines `_is_bonus` as *exactly* `kw_matches == 0`,
    so those were one gate applied twice rather than two opinions.
    On 2026-09-17 all three articles held for Indigenous Lands day were released into the
    pool and cut by the same run as "over budget/unconnected": two APTN pieces on First
    Nations wildfire impacts and Indigenous land guardians, and a modern-treaties story.
    They classified `standalone`, `standalone` and `kicker` — the weakest material in the
    segment. The roundup aired fifteen stories with an **empty theme block** and the run
    went green. Re-labelled against the target day, two land in `theme` and one in
    `theme_adjacent`.
    **A label that describes another day is worse than no label**, so `_relabel_for_day`
    drops the two stale scores rather than rewriting them: there is no charter judgment for
    today's theme to substitute, and a wrong number that looks authoritative is the failure
    mode this exists to prevent. `_held_from` and `_recalled_from` now carry standing in
    both consumers — arc-protected in `_curate_roundup_pool`, reachable by `strong_match` in
    the deep dive, sorted last so nothing is promoted over a genuine keyword match.
    **A story the router imported for today must not be droppable by the run that imported
    it.**
  - **The feed's charter score gets a vote in the export decision** (`_theme_fit_raw`,
    `HOLD_MIN_THEME_RAW`, `HOLD_PROTECT_TOP_FRAC`). Literal substring hits on title+summary
    were the only voice, and on 2026-09-17 that exported the day's single best-fitting
    Indigenous story *off* Indigenous Lands day: an IndigiNews feature on an Nlaka'pamux
    community's wildfire-mitigation programme, 98th percentile on Thursday's own feed,
    scoring **zero** strict Thursday keywords — the nation's name is not in the list and
    `[IndigiNews]` is stripped as a source tag — which matched Friday's wildfire slot twice
    and left.
    Neither `_theme_score` nor `_theme_score_raw` was read anywhere in `podcast_generator.py`
    before this. **Use the raw score, never the percentile**: `_theme_score` is a rank within
    that day's feed, so the top of a collapsed distribution reads 90-100 however poor the fit
    (`super-rss-feed` gotcha 13) — on the day in question a paleo-wildfire story and a
    consumer-electronics piece sat at 95 and 89 on Indigenous Lands day.
    Two bars, because neither works alone. The absolute floor (`HOLD_MIN_THEME_RAW`, 25,
    anchored to the thinnest upstream `min_score`) is the honest one and is what the upstream
    targeted rescore makes reachable — Thursday's whole pool topped out at 58 before it. The
    pool-relative bar (`HOLD_PROTECT_TOP_FRAC`) is what fires meanwhile, on a collapsed
    charter where nothing reaches the floor; it carries `HOLD_RANK_MIN_THEME_RAW` and a
    `HOLD_RANK_MIN_COVERAGE` requirement, because **rank without a floor is not evidence** —
    on a pool where nothing is scored, a raw of 2 is top of the heap and means nothing.
    Both log every time they fire, so they can be refitted off a measured month.
    The cost is asymmetric on purpose: a genuinely off-theme story at the top of a bad
    distribution stays home, which is one story in the roundup tail; the day's best on-theme
    story cannot leave, which is the episode.

- **Repeat-topic guard (`format_prior_coverage_for_prompt`):** local word-overlap check of deep-dive titles against recent episode topics and debate questions; on a match, hosts are instructed to acknowledge the earlier discussion and center what's new. Evolving-story context carries the same instruction.
