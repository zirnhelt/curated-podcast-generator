# Williams Lake 2026 General Local Election — Candidate Reference

*Producer notes, compiled after nominations closed (Sept 11, 2026). Voting day
Oct 17, 2026.*

**The names below are wired into the pipeline.** The machine-readable copy is
`config/themes.json` → theme `5` → `event_focus.roster`, which
`_format_event_roster` renders into the Saturday deep-dive lens and into the
`event_focus.research` sweep's standing assignment. This file is the prose
half — the platform notes and the reasoning — and the JSON is the half the
show reads. `tests/test_podcast_generator.py` asserts every name here appears
there, so **edit both or neither**: a correction that lands only in this file
changes nothing on air, and one that lands only in the JSON loses the context
for why.

**What the roster settles, and what it does not.** It settles exactly one
question — who is running, and in which race — because that closed and became
public on nomination day. That is why the hosts may now name candidates flatly
instead of hedging: on 2026-09-19 the deep dive opened on "three regional
director seats we straight up can't name a single candidate for" and could not
say whether 100 Mile House's mayor had filed, because the lens demanded names
and the prompt carried none.

Everything *after* the name is still `SOURCED OR UNSAID`. Prior terms, previous
results won and lost, the record in office, any controversy — those come from
the day's articles or the research sweep, with an outlet and a date, exactly as
before. The platform notes below are *what the candidate says about themselves*
and are deliberately **not** in the JSON: a name on a nomination form is a
source for the name and for nothing after it.

Scope: the City of Williams Lake ballot — mayor, council, and the SD27 Zone 6
trustee seat — as filed in the City's Declaration of Candidates (Form 8-8, signed
by the Chief Election Officer, Sept 11, 2026) — and the three CRD electoral areas
ringing the city (D, E, F), from the Williams Lake Tribune's "Six Cariboo Regional
District directors facing challengers" (Sept 15, 2026).

That CRD race sat here as an empty `NO FILED LIST` race until 2026-09-26, and the
episode that day told listeners the candidate list "isn't findable anywhere we
looked" — eleven days after the Tribune printed it, with Area F (Horsefly)
acclaimed. An empty race is now rendered as a gap *in the roster*, the research
sweep searches it first, and the hosts are told never to call something
unfindable. Fill a race in here and in the JSON the day its list is public.

## Mayor

- **Surinderpal Rathor** (incumbent) — intergovernmental collaboration,
  housing expansion, fiscal management. Cites past debt reduction and lower
  mill rates, partnerships with Williams Lake First Nation and the CRD.
  Priorities: senior-government funding for the water treatment plant, the
  Boitanio Mall housing redevelopment, a new indoor multiplex.
- **Walt Cobb** (former mayor, served 2014–2022) — fiscal restraint, council
  teamwork, finishing major capital works. Emphasizes stabilizing city
  finances against rising infrastructure costs and the forestry downturn.
  Senior housing initiatives, core municipal service delivery.

## Council — incumbents seeking re-election

- **Sheila Boehm** — mental health, addictions recovery, public safety.
  NCLGA leadership background; recovery-oriented care models, complex care
  beds, expanded health resources.
- **Michael Moses** — social equity, downtown vitality, relationships with
  surrounding Indigenous communities. Affordable housing expansion, active
  transportation, collaborative local governance.
- **Scott Nelson** — law and order, economic growth, resource sector support.
  Municipal camera systems, support for local forestry operators.

## Council — former councillors seeking a return

- **Jason Ryll** — General Manager, Vista Radio; served on council
  2014–2022. Financial management, downtown security, support for social
  agencies on homelessness, tourism development, reconciliation.
- **Paul French** — prior council experience. Budget discipline, core
  infrastructure maintenance, small business support.

## Council — new candidates and community leaders

- **Ruth Lloyd** — former Williams Lake Tribune journalist. Transparent
  city decision-making, clear public communication, pragmatic approach to
  downtown safety and housing.
- **Whitney Spearing** — Director of Natural Resources, Williams Lake First
  Nation. Environmental stewardship, wildfire mitigation, land management,
  joint city/First Nations economic development.
- **Greg Jeannotte** — co-founder, R.I.S.E. Society. Substance use recovery
  services, reintegration programs, grassroots health solutions.
- **Rafiullah Sahibzada** — Environmental Protection Officer, BC Ministry of
  Environment. Environmental management, administrative efficiency,
  community livability.
- **Charlene Hays, Cianna O'Connor, Billie Sheridan, Jared Wardlaw-Gimbel,
  Nathan Wiebe, Kayla Zaruk** — community members; stated priorities vary
  across downtown renewal, family infrastructure, small business retention,
  and fiscal oversight.

## Council — withdrawal after close of nominations

Fifteen filed. **Jared Wardlaw-Gimbel** withdrew by the Sept. 18 withdrawal
deadline (Williams Lake Tribune, Sept 23), so fourteen are running. The JSON
carries him in the council race's `"withdrawn"` list: the renderer says 14, never
15, and tells the hosts never to describe him as running.

## CRD electoral area directors — Areas D, E and F

- **Steve Forseth** (Area D, Fox Mountain / McLeese Lake; incumbent) — acclaimed.
  No vote in Area D.
- **Melynda Neufeld** (Area E, South Lakeside / Dog Creek; incumbent) — director
  since 2022, board vice-chair since November 2023. Challenged by
  **Mary Forbes**.
- **Maureen LeBourdais** (Area F, 150 Mile House / Horsefly / Likely; incumbent) —
  acclaimed. No vote in Area F.

## School trustee — SD27 Zone 6 (City of Williams Lake)

One to be elected; one filed.

- **Michael Franklin** — Williams Lake. No platform notes yet.

## Using this list

- **Names are citable; records mostly are not.** The show may say "Ruth Lloyd
  is running for council" on the strength of this list alone. It may not say what
  she did, won, lost or was accused of without a sourced finding, even when the
  fact is also written above. The one exception is `event_focus.roster.records`
  in the JSON: a short line **a named outlet published on a date**, rendered as
  SOURCED BACKGROUND with that attribution. The platform notes in this file are
  the candidates' own words and stay out; add a record line only with its outlet
  and date.
- **Update both copies, don't append forever.** If a candidate withdraws, is
  acclaimed, or a race changes shape between now and Oct 17, edit this file
  *and* `event_focus.roster` in place rather than layering corrections — it's a
  snapshot, not a ledger. The drift test will fail if only one moves.
- **An empty race is a real answer.** A race with no names is rendered as
  `NO FILED LIST` and named on air as a gap in what the show has. Leaving it
  empty is honest; guessing at it from a neighbouring race is the 2026-09-15
  failure that put a 100 Mile House candidate in the Williams Lake mayoral race.
- **The window closes itself.** `event_focus` is bounded by `start`/`end`
  (Sept 1 – Oct 24, 2026), so the roster stops being injected the day the window
  shuts. No cleanup commit, and nothing to remember.
