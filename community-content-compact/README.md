# Community Content Compact

A framework for evaluating the relationship between content producers and the communities they represent, draw from, or claim to serve — regardless of how the content was made.

-----

## Origin

This framework emerged from a Cariboo Signals episode harvest (May 18, 2026) covering arts, culture, and digital storytelling. The episode's hosts converged on a set of conditions under which AI-generated cultural content is defensible. That convergence became the seed of a broader question: do these conditions apply only to AI content, or to all externally produced creative content?

The answer is all of it. The power asymmetry between producer and community doesn't change based on the technology involved.

-----

## Files

|File              |Purpose                                                                                                                 |
|------------------|------------------------------------------------------------------------------------------------------------------------|
|`manifesto.md`    |The original argument: when AI-generated cultural content is and isn't defensible, and the unsolved governance gap      |
|`framework.md`    |The full scoring framework: two tracks, seven categories, 35 points (Track B) or 20 points (Track A), five verdict tiers|
|`scoring-card.jsx`|A React scoring card — nutrition-panel style — for filling in and sharing scores for a specific piece of content        |
|`self-assessment.md`|Cariboo Signals scored against Track A, with rationale and improvement path                                           |

-----

## How the Framework Works

The framework routes reviewers to one of two tracks based on the producer's relationship to the community:

**Track A — Community-Insider Production**
For producers who are community members, non-commercial, self-funded or micro-funded, and producing primarily for the community itself. 6 categories, 20 points. Verdict labels: Needs Work / Developing / Sound / Exemplary.

**Track B — External Production**
For institutional, commercial, mixed, or externally funded producers. 7 categories, 35 points. Verdict labels: Extractive / High Risk / Conditional / Defensible / Community-Serving.

Two automatic disqualifiers apply to both tracks and end the evaluation regardless of score.

Track A includes a **Continuous Improvement** section — a tiered growth path toward Track B standards, organized by effort level.

-----

## The Scoring Card

`scoring-card.jsx` is a standalone React component. It has two tabs:

- **Score** — enter content name, producer, track, disqualifier status, and category scores
- **Card** — renders a nutrition-panel-style summary with category bars, total score, percentage fill, and a color-coded verdict

It can be embedded in a web page, run as a standalone artifact in Claude.ai, or used as a reference for the printable markdown framework.

-----

## Relationship to Cariboo Signals

Cariboo Signals scores **18/20 — Exemplary** on Track A. It is:

- Self-funded (~$4/month in API fees)
- Community-adjacent (Horsefly, BC / Secwépemc territory)
- Non-commercial
- Transparent about AI generation

The Track A Continuous Improvement section in `self-assessment.md` maps directly to a Get Well Plan developed through Claude Code for the project, sequenced by effort.

-----

## License and Use

Offered for adaptation and use by any organization working on these questions. Attribution appreciated but not required.

*Developed through Cariboo Signals, May 2026.*
*github.com/zirnhelt/curated-podcast-generator*
