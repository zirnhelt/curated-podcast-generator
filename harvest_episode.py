#!/usr/bin/env python3
"""
harvest_episode.py — Episode deep dive harvester

Extracts the deep dive debate from a single episode into a formal Markdown
briefing document: positions, evidence, convergence, action items, and sources.

Usage:
    python harvest_episode.py                          # most recent episode
    python harvest_episode.py --date 2026-05-21        # specific episode
    python harvest_episode.py --output stdout          # terminal only
    python harvest_episode.py --output file            # save only (default: both)
    python harvest_episode.py --model claude-opus-4-7  # model override
"""

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import anthropic

PODCASTS_DIR = Path(os.environ.get("MEMORY_DIR", Path(__file__).parent)) / "podcasts"
REVIEWS_DIR = Path("reviews")

DEFAULT_MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """\
You are a content analyst for Cariboo Signals, a daily AI-generated podcast from
the Cariboo region of British Columbia. The show features two hosts: Riley (tech
optimist, engineering background, believes in pragmatic rural tech adoption) and
Casey (community development advocate, skeptical of tech hype, prioritizes
governance and consent).

Each episode includes a "Deep Dive" segment — a structured debate between Riley
and Casey on a topic relevant to rural BC communities.

Your job is to produce a formal "Episode Harvest" — a polished Markdown briefing
document that answers: "Based on this episode's deep dive, what should a listener
in a rural BC community actually *know* and *do*?"

The document must follow this exact structure. Output only the Markdown — no
preamble, no explanation, no ```markdown fences.

---

# Episode Harvest: [Deep Dive Title]
*[Formatted Date] · [Theme]*

## Episode Summary
[2–3 sentences. What did this episode argue or conclude? Do not write "the hosts
discuss" — state what the episode found or established.]

## Deep Dive: [Deep Dive Title]
**Central Question:** [exact question from the structured data]
**Riley's Take:** [1–2 sentences — her core position and reasoning]
**Casey's Take:** [1–2 sentences — their core position and reasoning]
**Where They Converge:** [the resolution and any agreed next steps — this is the
most important part; be specific about what they agreed to]

## Recommendations & Action Items
[Bulleted list of 4–8 concrete things a listener in the Cariboo region can do.
Be specific: name the institution, the question to ask, the resource to find.
"Contact your local government" is not specific. "Ask Williams Lake City Council
at their next meeting whether data governance agreements are in place before
partnering with external research institutions" is specific.]

## Resources & Organizations
| Name | Purpose | Link |
|------|---------|------|
[One row per named entity — organizations, tools, websites, government bodies,
research groups, specific named people in institutional roles. Include a URL only
where one is explicitly present in the provided citations data. Do not fabricate
URLs. If no URL is available, leave the Link cell empty.]

## Further Reading
[Bulleted list of source articles from the deep dive, formatted as Markdown links.
Title as link text, URL as target. Skip any entry whose URL is not a real article
(ads, sponsor pages, non-article URLs).]

---

Rules:
- Every section is required. If data is genuinely unavailable, write "(none
  identified)" rather than omitting the section.
- Recommendations must be specific and actionable — not generic advice.
- The Resources table must include every named organization or tool mentioned
  anywhere in the episode, not just the deep dive.
- Do not fabricate URLs. Only include URLs explicitly present in the provided
  citations data.\
"""


def find_episode(date_str: str | None) -> tuple[Path, Path, str, str]:
    """Return (citations_path, script_path, episode_date, theme_slug)."""
    if date_str:
        matches = sorted(glob.glob(str(PODCASTS_DIR / f"citations_{date_str}_*.json")))
        if not matches:
            print(f"No episode found for {date_str} in {PODCASTS_DIR}/", file=sys.stderr)
            sys.exit(1)
        cit_path = Path(matches[0])
    else:
        all_citations = glob.glob(str(PODCASTS_DIR / "citations_*.json"))
        if not all_citations:
            print(f"No episodes found in {PODCASTS_DIR}/", file=sys.stderr)
            sys.exit(1)
        cit_path = Path(
            max(
                all_citations,
                key=lambda f: re.search(r"citations_(\d{4}-\d{2}-\d{2})_", f).group(1),
            )
        )

    m = re.match(r"citations_(\d{4}-\d{2}-\d{2})_(.+)\.json", cit_path.name)
    if not m:
        print(f"Unexpected filename format: {cit_path.name}", file=sys.stderr)
        sys.exit(1)
    episode_date = m.group(1)
    theme_slug = m.group(2)

    scr_path = PODCASTS_DIR / f"podcast_script_{episode_date}_{theme_slug}.txt"
    if not scr_path.exists():
        print(f"Citations file found but no matching script: {scr_path}", file=sys.stderr)
        sys.exit(1)

    return cit_path, scr_path, episode_date, theme_slug


def load_episode_data(cit_path: Path, scr_path: Path) -> dict:
    """Load and validate both files; return structured dict."""
    with open(cit_path, encoding="utf-8") as f:
        citations = json.load(f)
    with open(scr_path, encoding="utf-8") as f:
        script_text = f.read()

    episode = citations.get("episode", {})
    segments = citations.get("segments", {})
    dd = segments.get("deep_dive", {})

    return {
        "episode": episode,
        "deep_dive": {
            "title": dd.get("title", ""),
            "articles": dd.get("articles", []),
            "discussion": dd.get("discussion"),  # None for pre-structured episodes
        },
        "script_text": script_text,
    }


def build_harvest_prompt(data: dict) -> str:
    """Build the user-turn prompt from episode data."""
    episode = data["episode"]
    deep_dive = data["deep_dive"]
    script_text = data["script_text"]

    # Deep dive discussion block
    disc = deep_dive["discussion"]
    if disc:
        riley_ev = "\n".join(f"  - {e}" for e in disc.get("riley_key_evidence", []))
        casey_ev = "\n".join(f"  - {e}" for e in disc.get("casey_key_evidence", []))
        topics = ", ".join(disc.get("topics_covered", []))
        discussion_block = f"""\
**Central Question:** {disc.get("central_question", "")}

**Riley's Position:** {disc.get("riley_position", "")}
**Riley's Key Evidence:**
{riley_ev}

**Casey's Position:** {disc.get("casey_position", "")}
**Casey's Key Evidence:**
{casey_ev}

**Where They Converge (Resolution):** {disc.get("resolution", "")}

**Topics Covered:** {topics}"""
    else:
        discussion_block = (
            "(Structured discussion data not available — extract positions, "
            "evidence, and resolution from the episode script below.)"
        )

    # Deep dive source articles — filter out non-article URLs
    dd_article_lines = []
    for a in deep_dive["articles"]:
        url = a.get("url", "")
        title = a.get("title", "(untitled)")
        if a.get("discussed", True) and url.startswith("http") and "?" not in url.split("/")[-1]:
            dd_article_lines.append(f"- [{title}]({url})")
    dd_articles_block = "\n".join(dd_article_lines) if dd_article_lines else "(none)"

    return f"""\
## Episode Metadata

- **Date:** {episode.get("formatted_date", episode.get("date", ""))}
- **Theme:** {episode.get("theme", "")}
- **Episode Title:** {episode.get("title", "")}
- **Deep Dive Title:** {deep_dive["title"]}

---

## Deep Dive: Structured Discussion

{discussion_block}

---

## Deep Dive: Source Articles

{dd_articles_block}

---

## Full Episode Script

{script_text}

---

## Your Task

Produce the Episode Harvest document as specified in your system instructions.
Focus on the Deep Dive debate. Extract concrete, actionable insights — do not
simply restate what was said. Translate the content into what a listener should
know and do.

For the Resources & Organizations table, include every named organization, tool,
platform, government body, institution, or initiative mentioned anywhere in the
script or the citations data above. Do not omit any named entity. Include a URL
only where one is explicitly present in the data provided.

Output only the Markdown document. No preamble, no commentary.\
"""


def run_harvest(data: dict, model: str) -> str:
    """Call Claude and return the harvest document."""
    client = anthropic.Anthropic()
    prompt = build_harvest_prompt(data)

    print(f"Generating harvest with {model}...")

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    return response.content[0].text


def save_harvest(document: str, episode_date: str, theme_slug: str) -> Path:
    """Save harvest document to reviews/harvest_YYYY-MM-DD_theme_slug.md."""
    REVIEWS_DIR.mkdir(exist_ok=True)
    path = REVIEWS_DIR / f"harvest_{episode_date}_{theme_slug}.md"

    with open(path, "w", encoding="utf-8") as f:
        f.write(document)
        if not document.endswith("\n"):
            f.write("\n")

    return path


def main():
    parser = argparse.ArgumentParser(
        description="Harvest deep dive takeaways from a recent episode into a formal document"
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        default=None,
        help="Episode date to harvest (default: most recent episode)",
    )
    parser.add_argument(
        "--output",
        choices=["file", "stdout", "both"],
        default="both",
        help="Where to send the harvest document (default: both)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Claude model to use (default: {DEFAULT_MODEL})",
    )
    args = parser.parse_args()

    if args.date:
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            print(
                f"Invalid date format: {args.date!r}. Expected YYYY-MM-DD.",
                file=sys.stderr,
            )
            sys.exit(1)

    cit_path, scr_path, episode_date, theme_slug = find_episode(args.date)
    print(f"Harvesting episode: {cit_path.name}")
    print(f"  Script:    {scr_path.name}")

    data = load_episode_data(cit_path, scr_path)

    episode = data["episode"]
    print(f"  Theme:     {episode.get('theme', theme_slug)}")

    if not data["deep_dive"]["discussion"]:
        print(
            "  Warning: no structured discussion block found. "
            "Deep dive structure will be extracted from the script.",
            file=sys.stderr,
        )

    document = run_harvest(data, args.model)

    if args.output in ("stdout", "both"):
        print("\n" + "=" * 72 + "\n")
        print(document)

    if args.output in ("file", "both"):
        path = save_harvest(document, episode_date, theme_slug)
        print(f"\nHarvest saved → {path}")


if __name__ == "__main__":
    main()
