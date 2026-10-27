# The Curator's Manifesto

The spirit behind both curators: `super-rss-feed` (the reader's feeds) and
`curated-podcast-generator` (Cariboo Signals). The rules in each `CLAUDE.md` say *what*
the code does; this says *why*, and settles the cases the rules don't reach.

Written 2026-10-27 from 25 closed questions answered by the producer. The answer key is at
the bottom; change an answer there first, then the prose.

## The one rule

**Never mislead the listener.** Every other value bends before this one. A thin story is
cut, not stretched. A gap stays unsaid, never papered over. A correction is made loudly.

## Who it's for

- **The show serves the listener; the feed serves the reader.** They share an editor and
  most of their values, but not one purpose. The feed can follow the producer's
  curiosity; the show serves the community, and when the producer's taste and the
  listener's interest diverge, the listener wins.
- **The Cariboo is a sensitivity, not a filter.** A story doesn't need a local angle to
  matter. When one has a local angle, find it and say it plainly. When it has none, don't invent one.
- **Close beats far.** A small, true local story outranks a big, well-reported world story.

## What earns a slot

- **Interesting is enough.** A story can get in because it delights, or because it helps
  someone understand something. It doesn't have to be important, and it doesn't have to
  be actionable.
- **Distant bad news stays out.** Crime, disaster and tragedy from elsewhere with no
  local link don't air, however well they're reported.
- **Alarm needs an anchor.** An expert's warning earns a slot only when there's a specific
  incident, finding or policy behind it **and** a local or theme connection.
- **Launches are not news.** Product launches, funding rounds and model releases are left
  out unless they bear directly on the day's theme.
- **Community life is news.** Festivals, parades and civic events belong. Sports don't.
- **Leave room to be surprised.** Keep a deliberate slot for the story outside the
  stated interests: the one nobody asked for but the listener is glad to hear.

## Stance and tone

- **On technology, let evidence set the tone.** Neither booster nor skeptic by default.
- **Favour solutions over problems, on purpose.** Given two true stories, the one about
  something being fixed, built or protected comes first.
- **Indigenous coverage is a core beat, led by self-determination and achievement.** It
  is never decoration. The naming rules in `editorial-voice.md` still hold: name the
  right people, or no one.
- **The hosts are warm and they disagree.** Real friction on substance; affection
  between the people. A disagreement is worth airing when it shows the listener
  something, never as theatre.
- **Humour is welcome when it's dry and earned.** No bits, no forced jokes.
- **The hosts may have views on stakes and implications.** What a decision costs, who it
  helps, what to watch. Never endorsements, never rankings of candidates.

## Trust and accuracy

- **Thin sourcing means cut.** If the story can't be told from what's sourced, it doesn't
  air.
- **The paper that was there is the authority**, unless it is reprinting someone
  else's syndicated copy. Then credit the original, and don't treat the local masthead
  as proof the story happened locally.
- **Corrections are specific, not ceremonial.** They close the roundup, and they name
  what was wrong, what is right, and when it aired. A vague correction is worse than none.
- **Come back to a story only with new facts.** A developing story is welcome when
  something has changed. Repeating it isn't continuity, it's a failure.

## How it decides

- **When in doubt, leave it out.** Precision over recall. The ratings can't flag a story
  the listener already sat through.
- **Short and strong beats full and thin, within a floor.** A lighter feed or a shorter
  episode is better than padding, but every category ships something and the episode stays
  a show, not a bulletin.
- **Quality first where the listener hears it.** The script and polish run on Sonnet 5.5;
  everything upstream stays cheapest-first. A move further up earns its place in a trial,
  not on faith. Every dollar should reach the listener's ear or the reader's screen, and
  none should go on calls made just in case.

## Where the values live in code

**The show** carries them as `SHOW VALUES` in the cached script system prompt
(`config/prompts.json` → `script_generation_system`), kept free of the phrases in
`config/ai_tells.json`:

```
SHOW VALUES: Never mislead the listener; if a story can't be told from its sources, leave
it out. Where a story holds something being fixed, built or protected, give that its due.
On technology, let the evidence set the tone, neither booster nor skeptic. Indigenous
stories lead with self-determination and achievement. The hosts are warm with each other
and disagree on substance; they may weigh stakes and costs, and never endorse. Humour is
dry and earned. Come back to a past story only with a new fact.
```

**The feed** gets no values paragraph: its gate already says the same things through
its own levers, and a new paragraph would change `GATE_RUBRIC_ID` and re-gate the whole
cache to repeat them.

| Value | Feed lever |
|-------|-----------|
| Close beats far | `w_local` (0.20), `local_signals`, the `local` feed |
| Distant bad news stays out | `standing_preferences.txt` → "Bad news without local relevance" |
| Alarm needs an anchor | `standing_preferences.txt` → "Doomsaying and catastrophizing" |
| Launches are not news | `GATE_REJECT_RUBRIC` → "Fluffy AI/tech" (ai-tech and homelab only) |
| Community life yes, sports no | `GATE_REJECT_RUBRIC` → Sports, and its KEEP for community events |
| Leave room to be surprised | The wildcard (below) |

## Decisions (2026-10-27)

Where the answers pulled against the code, and what was decided.

| Answer | Was | Now |
|--------|-----|-----|
| 22 short and strong | Feed filled `min_slots` regardless of the floor; the show made 2 expand passes and aborted under 2,800 words | **Softened, both.** `min_slots` halved, rounded up, so no category ships empty (ai-tech 5→3, local 3→2). One expand pass; abort floor 2,400 words (~14 min) |
| 24 quality first | Looked like Haiku 5.5 on script and polish (the `CLAUDE_SONNET_MODEL` default since 2026-10-08) | **Already in place.** `CLAUDE_SCRIPT_MODEL` and `CLAUDE_POLISH_MODEL` were set to `claude-sonnet-5-5`, so the show's words were never on Haiku. No trial needed; a further step up (e.g. Opus) would get one |
| 19 prominent corrections | The roundup's final beat | **Kept at the end.** The prompt already demands subject, wrong detail, air date and correct detail; answer 19 now reads "specific, not ceremonial" |
| 23 serendipity | No slot | **Both.** Feed: one wildcard a day on top of the slots, the highest `q_gate` (≥ 55, p90) that the interest profile missed (relevance under 40, or never deep-scored). Show: the kicker comes from the better half of the standalones, the one the day's charter fits least |
| 20 only with new facts | A recalled election story always aired | Its prompt tag now says to leave it out when nothing has moved |
| 5 close beats far | News keeps a world lane (`world_slots`, 4) | Kept: a floor for world events, not a preference over local |

**Still open:** answer 9 ("launches are never news") is enforced only on ai-tech and
homelab. The gate leaves funding and merger news under `[news]` to the score on purpose.
Widen it only after the ratings show it's needed.

## Answer key (2026-10-27)

| # | Question | Answer |
|---|----------|--------|
| 1 | Taste vs listener | Listener wins |
| 2 | Feed and show, same values? | No: feed = curiosity, show = community |
| 3 | Cariboo as lens or beat | A beat, with local sensitivity |
| 4 | Important vs interesting | Delight is enough |
| 5 | Small local vs big world | Local |
| 6 | Must be useful? | No, understanding is enough |
| 7 | Distant bad news | Out, full stop |
| 8 | Expert warnings | Only with an incident/finding/policy, plus a local or theme link |
| 9 | Launches and funding | Never news, unless they bear directly on the day's theme |
| 10 | Community listings | Welcome, except sports |
| 11 | Stance on tech | Neutral, evidence sets the tone |
| 12 | Solutions over problems | Yes, deliberately |
| 13 | Indigenous coverage | Centre it, lead with self-determination and achievement |
| 14 | Hosts disagree? | Both: real friction and warmth |
| 15 | Humour | Welcome, dry and earned |
| 16 | Hosts have opinions? | Yes, on stakes and implications |
| 17 | Thin source | Cut |
| 18 | Local vs national authority | Local, unless syndicated |
| 19 | Corrections | Prominent, then settled as specific, at the roundup's end |
| 20 | Developing stories | Only with new facts |
| 21 | When in doubt | Leave it out |
| 22 | Full-thin vs short-strong | Short and strong, then softened to keep a floor |
| 23 | Serendipity | Yes, a deliberate slot, in both |
| 24 | Cost vs quality | Quality first where the listener hears it (Sonnet 5.5 on script and polish) |
| 25 | The one rule | Never mislead the listener |
