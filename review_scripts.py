#!/usr/bin/env python3
"""
review_scripts.py — Periodic script quality reviewer

Reads the last N days of podcast scripts and evaluates them against
the project's mission, goals, and quality standards using Claude.

Usage:
    python review_scripts.py                  # review last 5 days
    python review_scripts.py --days 7         # review last 7 days
    python review_scripts.py --output stdout  # print to terminal only
"""

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import anthropic

SCRIPTS_DIR = Path("podcasts")
CONFIG_DIR = Path("config")
REVIEWS_DIR = Path("reviews")

REVIEW_MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """\
You are the editorial director for Cariboo Signals, an AI-generated daily podcast.
Your job is to review recent episode scripts and provide honest, specific, actionable feedback.
Cite exact quotes when flagging problems. Be direct — praise what works, name what doesn't.\
"""

REVIEW_CRITERIA = """\
## Mission
"{title}" — {tagline}

{description}

## Hosts
- **Riley** (she/her): {riley_bio}
- **Casey** (they/them): {casey_bio}

## Quality Dimensions to Score (1–5 each)

1. **Mission Alignment** — Does every segment connect to rural BC communities, responsible tech, and the Cariboo region? Is the Indigenous context handled naturally (not forced or ignored)?

2. **Host Voice Differentiation** — Do Riley and Casey sound genuinely distinct? Riley: declarative, evidence-first, optimistic, faster cadence. Casey: blunter, drier, skeptical, shorter turns in debate, willing to hold a position.

3. **Banned Pattern Violations** — Flag any of these (quote the line, name the pattern):
   - `I want to [verb]...` / `Let me [verb]...` (announcing intent instead of acting)
   - Any sentence opening with `Here's [anything]`
   - `That's a fair/meaningful/important point` before a counterpoint
   - `steelman` (should be "strongest case for")
   - Stage directions: `(laughs)`, `*chuckles*`, `(shuffles papers)`, etc.
   - `weekly show` instead of `today's episode`
   - Personal family references (`my dad`, `my partner`, etc.)
   - `circling back to where we started`
   - Pre-announcing structure (`What I want to do is walk through...`)

4. **Counterpoint Quality** — Does the Deep Dive have at least 3–4 genuine point/counterpoint exchanges? Does each counterpoint introduce NEW information (not just "but that's risky")? Can either host maintain disagreement without conceding just to move the conversation along?

5. **Evidence Honesty** — Are specific statistics, dollar amounts, named studies, or project details grounded in the verified source articles? Flag any that appear fabricated or unverifiable.

6. **Segment Structure** — Welcome ≤160 words; News Roundup is efficient anchor-style (not chatty); Community Spotlight is warm and brief (~50–75 words from one host + a sentence from the other); Deep Dive is the longest and most substantive segment.

7. **Land Acknowledgement** — Is it present in the Welcome? Is the phrasing varied (check for repetition across the reviewed episodes)?

8. **Humor & Tone** — At least 3 understated light moments per episode? Dry, organic — not punchlines. AI self-reference used at most once across the whole week (not every episode).

9. **Transition Variety** — Are transitions unique each time? Any say "after the music" (banned)?

10. **Rural Cariboo Lens** — Does the episode consistently ask "what does this mean for communities like ours?" or does it drift into generic tech commentary?\
"""


def load_config() -> dict:
    with open(CONFIG_DIR / "podcast.json") as f:
        podcast = json.load(f)
    with open(CONFIG_DIR / "hosts.json") as f:
        hosts = json.load(f)
    return {
        "title": podcast["title"],
        "tagline": podcast["tagline"],
        "description": podcast["description"],
        "riley_bio": hosts["riley"]["full_bio"],
        "casey_bio": hosts["casey"]["full_bio"],
    }


def find_recent_scripts(days: int) -> list[tuple[str, str]]:
    """Return (filename, content) for scripts from the last N days, newest first."""
    scripts = []
    for i in range(days):
        date = datetime.now() - timedelta(days=i + 1)
        date_str = date.strftime("%Y-%m-%d")
        matches = sorted(glob.glob(str(SCRIPTS_DIR / f"podcast_script_{date_str}_*.txt")))
        for path in matches:
            with open(path) as f:
                content = f.read()
            scripts.append((Path(path).name, content))
    return scripts


def excerpt_script(content: str, max_chars: int = 8000) -> str:
    """Return a representative excerpt: full Welcome + News Roundup start + full Deep Dive."""
    if len(content) <= max_chars:
        return content

    # Find the Deep Dive marker
    deep_dive_marker = "**DEEP DIVE:"
    dd_idx = content.find(deep_dive_marker)

    if dd_idx == -1:
        # No Deep Dive found — take first chunk + last chunk
        half = max_chars // 2
        return content[:half] + "\n\n[...middle omitted...]\n\n" + content[-half:]

    # Take the first part up to a reasonable cutoff, then the full Deep Dive
    pre_dd = content[:dd_idx]
    deep_dive = content[dd_idx:]

    pre_budget = max_chars - len(deep_dive)
    if pre_budget < 1000:
        # Deep Dive alone exceeds budget — just take it all (Deep Dive is most important)
        return "[Welcome + News Roundup omitted for length]\n\n" + deep_dive
    if pre_budget >= len(pre_dd):
        return content

    return pre_dd[:pre_budget] + "\n\n[...News Roundup continues, omitted...]\n\n" + deep_dive


def build_review_prompt(scripts: list[tuple[str, str]], config: dict) -> str:
    criteria = REVIEW_CRITERIA.format(**config)

    script_blocks = []
    for filename, content in scripts:
        excerpt = excerpt_script(content)
        script_blocks.append(f"### {filename}\n\n{excerpt}")

    scripts_text = "\n\n---\n\n".join(script_blocks)

    return f"""\
{criteria}

---

## Scripts to Review

{scripts_text}

---

## Instructions

For **each script**, provide:
- A score table (dimension → score 1–5 → one-line rationale)
- **Flags**: Specific problems with exact quotes and the rule violated
- **Strengths**: What worked well with specific examples

Then provide a **Cross-Episode Synthesis** covering:
- Recurring patterns across these episodes (good and bad)
- The 2–3 most urgent issues to fix
- Concrete recommendations (specific prompt changes, config changes, or generation process tweaks)

Format as clean Markdown. Be direct and specific — vague feedback is not useful.\
"""


def run_review(days: int) -> str:
    config = load_config()
    scripts = find_recent_scripts(days)

    if not scripts:
        print(f"No scripts found in the last {days} days under {SCRIPTS_DIR}/", file=sys.stderr)
        sys.exit(1)

    print(f"Reviewing {len(scripts)} script(s) from the last {days} day(s)...")
    for name, _ in scripts:
        print(f"  • {name}")

    client = anthropic.Anthropic()

    response = client.messages.create(
        model=REVIEW_MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_review_prompt(scripts, config)}],
    )

    return response.content[0].text


def save_report(report: str, days: int) -> Path:
    REVIEWS_DIR.mkdir(exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    path = REVIEWS_DIR / f"review_{date_str}.md"

    with open(path, "w") as f:
        f.write(f"# Cariboo Signals Script Review — {date_str}\n\n")
        f.write(f"*Covering the last {days} day(s) of episodes.*  \n")
        f.write(f"*Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}*\n\n")
        f.write("---\n\n")
        f.write(report)

    return path


def main():
    parser = argparse.ArgumentParser(description="Review recent podcast scripts against mission and quality standards")
    parser.add_argument("--days", type=int, default=5, help="Number of days to review (default: 5)")
    parser.add_argument(
        "--output",
        choices=["file", "stdout", "both"],
        default="both",
        help="Where to send the report (default: both)",
    )
    args = parser.parse_args()

    report = run_review(args.days)

    if args.output in ("stdout", "both"):
        print("\n" + "=" * 72 + "\n")
        print(report)

    if args.output in ("file", "both"):
        path = save_report(report, args.days)
        print(f"\nReport saved → {path}")


if __name__ == "__main__":
    main()
