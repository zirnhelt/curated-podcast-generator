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
- **Corrections are prominent and specific.** Trust is the product: say what was wrong,
  what is right, and when it aired.
- **Come back to a story only with new facts.** A developing story is welcome when
  something has changed. Repeating it isn't continuity, it's a failure.

## How it decides

- **When in doubt, leave it out.** Precision over recall. The ratings can't flag a story
  the listener already sat through.
- **Short and strong beats full and thin.** A lighter feed or a shorter episode is better
  than padding.
- **Quality first, with no wasted spend.** The show is worth paying for. Every dollar
  should reach the listener's ear or the reader's screen, and none should go on calls made
  just in case.

## Prompt blocks

Compressed versions for the prompts, kept free of the burned and banned phrases in
`config/ai_tells.json`. **Not wired in yet**: adding them is a prompt change and must be
costed and reviewed like one.

**Curator (feed scoring / quality gate):**

```
EDITOR'S VALUES: Interesting is enough; a story need not be important or actionable.
Prefer close to far: a true Cariboo story beats a big world story. Distant crime, disaster
and tragedy with no local link: reject. A risk warning needs a concrete incident, finding
or policy AND a local or theme link. Launches, funding and model releases: reject unless
tied to the day's theme. Community events: keep; sports: reject. When unsure, reject.
```

**Show (script system prompt):**

```
SHOW VALUES: Never mislead. If a story can't be told from its sources, it isn't told.
Lead with what is being fixed, built or protected. Technology: let the evidence set the
tone. Indigenous stories: lead with self-determination and achievement. The hosts are
warm and they disagree on substance; they may weigh stakes and costs, never endorse.
Humour is dry and earned. Return to a past story only with a new fact.
```

## Where this disagrees with today's code

These answers pull against rules already in place. Each one needs a decision before it
reaches code. Until then, the existing rule stands.

| Answer | Current behaviour | Tension |
|--------|-------------------|---------|
| 22 short and strong | `super-rss-feed` fills `min_slots` regardless of the floor; the show expands a short script and aborts under `MIN_SCRIPT_WORDS` (2800) | Both systems pad by design |
| 24 quality first | Both `CLAUDE.md` files call cost "a hard constraint"; Sonnet roles default to Haiku 5.5 since 2026-10-08 | Quality first could justify putting Sonnet back on the script |
| 20 only with new facts | Election stories are booked back (`status: 'recall'`) for the next civic episode | A recall without a new fact is a repeat |
| 23 serendipity slot | No slot exists; the review batch's 2 `in_feed` canaries are the nearest thing | Would be new curation logic |
| 19 prominent corrections | Corrections air as the roundup's final beat | Last place isn't prominent |
| 5 close beats far | News has a protected world lane (`world_slots`, 4) | Probably fine as a floor, but worth stating |

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
| 19 | Corrections | Prominent |
| 20 | Developing stories | Only with new facts |
| 21 | When in doubt | Leave it out |
| 22 | Full-thin vs short-strong | Short and strong |
| 23 | Serendipity | Yes, a deliberate slot |
| 24 | Cost vs quality | Quality first |
| 25 | The one rule | Never mislead the listener |
