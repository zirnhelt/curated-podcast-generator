# Community Content Compact

A structured accountability framework for evaluating whether content produced about, from, or on behalf of a community actually serves that community.

## Files

| File | Description |
|---|---|
| `manifesto.md` | The founding argument — why this framework exists and what it's trying to fix |
| `framework.md` | The full two-track scoring rubric (Track A: community-insider; Track B: external producer) |
| `scoring-card.jsx` | Standalone React component for scoring and sharing a content review |

## Relationship to Cariboo Signals

This framework was developed through [Cariboo Signals](https://zirnhelt.github.io/curated-podcast-generator/) — an AI-generated daily podcast about technology and society in rural British Columbia.

Building an AI content system for a specific community forced concrete decisions about transparency, displacement, consent, and governance that general AI ethics frameworks do not address well. The Compact emerged from applying this rubric to Cariboo Signals itself. The score was uncomfortable: **8/35, Extractive**. That discomfort is the point.

The framework is offered for adaptation and use by any organization working on these questions. It is not a certification program. It is a structured way to have a harder conversation earlier — ideally before content is published, not after harm is done.

## How to Use

1. Complete the **Content Identification** header
2. Check the two **Automatic Disqualifiers** — a Yes ends the evaluation
3. Select your **track** based on producer-community relationship:
   - **Track A** if the producer is a member of, directly employed by, or directly accountable to the community they cover
   - **Track B** if the producer stands outside the community they cover
4. Score each category using the track-specific criteria
5. Sum the scores and read the verdict
6. Use the Notes section to document remediation commitments

The `scoring-card.jsx` component renders a shareable, printable visual summary of a completed evaluation.

## Scoring at a Glance

| Score | Verdict |
|---|---|
| *Any DQ* | **Disqualified** — Do not proceed |
| 0–10 | **Extractive** — Fundamental redesign required |
| 11–18 | **High Risk** — Specific conditions must be met before publication |
| 19–25 | **Conditional** — Written remediation plan required |
| 26–31 | **Defensible** — Proceed with active accountability maintenance |
| 32–35 | **Community-Serving** — Document and share this model |

---

*Developed through Cariboo Signals, May 2026.*
*Offered for adaptation and use by any organization working on these questions.*
*github.com/zirnhelt/curated-podcast-generator*
