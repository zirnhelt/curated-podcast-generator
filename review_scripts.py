#!/usr/bin/env python3
"""
review_scripts.py — Periodic script quality reviewer

Reads the last N days of podcast scripts and evaluates them against
the project's mission, goals, and quality standards using Claude.

Usage:
    python review_scripts.py                  # review last 7 days
    python review_scripts.py --days 7         # review last 7 days
    python review_scripts.py --output stdout  # print to terminal only
"""

import argparse
import difflib
import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import anthropic

SCRIPTS_DIR = Path(os.environ.get("MEMORY_DIR", Path(__file__).parent)) / "podcasts"
CONFIG_DIR = Path(__file__).parent / "config"
REVIEWS_DIR = Path("reviews")

REVIEW_MODEL = "claude-sonnet-4-6"

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Files that shape episode content — used to summarize "what changed" for the reviewer.
GENERATION_PATHS = [
    "podcast_generator.py",
    "config/prompts.json",
    "config/hosts.json",
    "config/podcast.json",
    "config/themes.json",
]

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

## Daily Theme Rotation
Each weekday carries a fixed assigned theme — the show doesn't free-float between topics, and a given date is always reviewed against the theme its weekday owns:

{themes_overview}

Each script below is labeled with the theme its date is assigned. Use that label (not just the filename slug) as ground truth when scoring Dimension 12.

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

10. **Rural Cariboo Lens** — Does the episode consistently ask "what does this mean for communities like ours?" or does it drift into generic tech commentary? Specifically: does the Deep Dive name at least one specific Cariboo community, First Nation, or organization in its second half? The absence of any named Cariboo entity in the final third of the Deep Dive is a score of 3 or below on this dimension, regardless of how often "rural" or "communities like ours" appears. For Indigenous-topic episodes, the named entity must be a Cariboo Nation or organization, not national Indigenous organizations alone.

11. **Named Attribution (C1 — Consent)** — Flag any line where a real, named individual appears to be quoted or paraphrased but the phrasing goes beyond what is directly supported by the source article. Patterns to flag: (a) direct-speech quotes not present verbatim in source articles, (b) paraphrased opinions attributed to a named person ("X believes…", "X told us…", "According to X…") where no such statement exists in the citations, (c) fabricated anecdotes or positions assigned to real people. Score: 5 = no unanchored attributions; 3 = at least one paraphrased attribution that is plausible but not verifiable; 1 = a fabricated or unsupported direct quote attributed to a named real individual.

12. **Theme Adherence** — Does the episode actually commit to *its assigned* daily theme (labeled above each script), from Welcome through Deep Dive — or does it drift into another day's theme territory, lean on generic tech commentary, or land on an angle that could air under any theme without changing a word? Name the specific point where the episode drifts off-theme, and call out when a segment would honestly belong to a different day's theme. Score 5 = the theme visibly shapes the episode's choices throughout; 3 = the theme is present but thin or front-loaded then abandoned; 1 = the theme is a label only — the content is interchangeable with another day's episode. Episodes marked **Format: Standalone introduction/trailer** are not part of the daily theme rotation — score Theme Adherence N/A for these rather than penalizing them against a date-based theme.\
"""


def load_config() -> dict:
    with open(CONFIG_DIR / "podcast.json") as f:
        podcast = json.load(f)
    with open(CONFIG_DIR / "hosts.json") as f:
        hosts = json.load(f)
    with open(CONFIG_DIR / "themes.json") as f:
        themes = json.load(f)

    themes_overview = "\n".join(
        f"- {WEEKDAY_NAMES[int(day)]}: **{theme['name']}** — {theme['description']}"
        for day, theme in sorted(themes.items(), key=lambda kv: int(kv[0]))
    )

    return {
        "title": podcast["title"],
        "tagline": podcast["tagline"],
        "description": podcast["description"],
        "riley_bio": hosts["riley"]["full_bio"],
        "casey_bio": hosts["casey"]["full_bio"],
        "themes": themes,
        "themes_overview": themes_overview,
    }


def is_trailer_episode(filename: str, themes_config: dict) -> bool:
    """True if the script's sibling citations file marks it as a standalone trailer
    (e.g. the "introducing the show" episode), which is exempt from the daily
    theme rotation and should not be scored against a date-derived theme.

    Detected either via an explicit `episode_type: "trailer"` field, or by the
    citations' declared theme not matching any theme in the weekday rotation
    (e.g. "Introducing The Show").
    """
    match = re.match(r"podcast_script_(\d{4}-\d{2}-\d{2})_(.+)\.txt$", filename)
    if not match:
        return False
    date_str, slug = match.groups()
    citations_path = SCRIPTS_DIR / f"citations_{date_str}_{slug}.json"
    if not citations_path.exists():
        return False
    try:
        with open(citations_path) as f:
            citations = json.load(f)
    except (json.JSONDecodeError, OSError):
        return False
    episode = citations.get("episode", {})
    if episode.get("episode_type") == "trailer":
        return True
    declared_theme = episode.get("theme")
    rotation_theme_names = {theme["name"] for theme in themes_config.values()}
    return bool(declared_theme) and declared_theme not in rotation_theme_names


def assigned_theme_for(filename: str, themes_config: dict) -> str | None:
    """The theme this script's date is assigned under the fixed weekday rotation.

    Returns None for standalone/trailer episodes (e.g. the show intro), which
    are not part of the rotation and should not be scored against whatever
    theme happens to own that date.
    """
    if is_trailer_episode(filename, themes_config):
        return None
    match = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
    if not match:
        return None
    weekday = datetime.strptime(match.group(1), "%Y-%m-%d").weekday()
    theme = themes_config.get(str(weekday))
    return theme["name"] if theme else None


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

    # The Welcome (including the land acknowledgement) is short — always keep
    # it verbatim so it doesn't get scored as "omitted" even when the rest of
    # the News Roundup has to be dropped for length.
    news_roundup_marker = "**NEWS ROUNDUP**"
    nr_idx = pre_dd.find(news_roundup_marker)
    welcome = pre_dd[:nr_idx] if nr_idx != -1 else ""

    pre_budget = max_chars - len(deep_dive)
    if pre_budget < 1000:
        # Deep Dive alone exceeds budget — keep the Welcome, drop the rest
        return welcome + "\n\n[News Roundup omitted for length]\n\n" + deep_dive
    if pre_budget >= len(pre_dd):
        return content

    return pre_dd[:pre_budget] + "\n\n[...News Roundup continues, omitted...]\n\n" + deep_dive


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def _prompt_template_diff(commit_hash: str) -> str:
    """Diff the actual prompt *text* a commit changed (not raw JSON lines).

    config/prompts.json stores each template as one giant escaped string, so a
    plain `git diff` just shows two enormous opaque lines. Parsing both sides as
    JSON and diffing the unescaped template text line-by-line gives the reviewer
    something it can actually read and reason about.
    """
    try:
        new = json.loads(_git("show", f"{commit_hash}:config/prompts.json"))
        old = json.loads(_git("show", f"{commit_hash}^:config/prompts.json"))
    except (json.JSONDecodeError, ValueError):
        return ""

    sections = []
    for key in sorted(set(new) | set(old)):
        old_text = (old.get(key) or {}).get("template", "")
        new_text = (new.get(key) or {}).get("template", "")
        if old_text == new_text:
            continue
        diff_lines = [
            line for line in difflib.unified_diff(old_text.splitlines(), new_text.splitlines(), lineterm="", n=1)
            if line[:3] not in ("---", "+++") and not line.startswith("@@")
        ]
        if diff_lines:
            sections.append(f"`{key}`:\n" + "\n".join(diff_lines))

    return "\n\n".join(sections)


def summarize_recent_changes(days: int, max_diff_chars: int = 6000) -> str:
    """Summarize commits + prompt-text diffs touching generation code in the review window.

    Gives the reviewer a basis for connecting episode-quality shifts to actual
    prompt/code changes — and for grounding the Improvement Action Plan in what's
    already been tried versus what's still untouched.
    """
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    log = _git(
        "log", "--reverse", f"--since={since}", "--date=short",
        "--pretty=format:%h|%ad|%s", "--", *GENERATION_PATHS,
    )
    if not log:
        return (
            "No commits touched the generation code, prompts, or show/host configuration "
            "in this window — any quality shifts below aren't explained by recent system changes."
        )

    commit_lines = []
    diff_chunks = []
    remaining = max_diff_chars
    for entry in log.splitlines():
        commit_hash, date, subject = entry.split("|", 2)
        touched = _git("show", "--name-only", "--format=", commit_hash, "--", *GENERATION_PATHS)
        touched = ", ".join(line.strip() for line in touched.splitlines() if line.strip())
        commit_lines.append(f"- **{date}** `{commit_hash}` {subject} _(touched: {touched})_")

        if remaining > 0 and "config/prompts.json" in touched:
            prompt_diff = _prompt_template_diff(commit_hash)
            if prompt_diff:
                chunk = f"### {date} `{commit_hash}` — {subject}\n\n{prompt_diff}"
                if len(chunk) > remaining:
                    chunk = chunk[:remaining] + "\n[...truncated...]"
                diff_chunks.append(chunk)
                remaining -= len(chunk)

    parts = [f"**Commits touching generation code/config since {since}** (oldest first):\n\n" + "\n".join(commit_lines)]
    if diff_chunks:
        parts.append(
            "**Prompt text changes** — the actual instructions given to the show's writers, diffed as "
            "readable text rather than raw JSON (use these to judge whether episodes after a given date "
            "reflect the new instructions, and whether the change had the intended effect):\n\n"
            + "\n\n---\n\n".join(diff_chunks)
        )
    return "\n\n".join(parts)


def build_review_prompt(scripts: list[tuple[str, str]], config: dict, recent_changes: str) -> str:
    criteria = REVIEW_CRITERIA.format(**config)
    themes_config = config["themes"]

    script_blocks = []
    for filename, content in scripts:
        excerpt = excerpt_script(content)
        theme = assigned_theme_for(filename, themes_config)
        if theme:
            theme_line = f"**Assigned theme for this date:** {theme}\n\n"
        elif is_trailer_episode(filename, themes_config):
            theme_line = "**Format:** Standalone introduction/trailer episode — not part of the daily theme rotation.\n\n"
        else:
            theme_line = ""
        script_blocks.append(f"### {filename}\n\n{theme_line}{excerpt}")

    scripts_text = "\n\n---\n\n".join(script_blocks)

    return f"""\
{criteria}

---

## Recent Code & Prompt Changes

These episodes were generated by the prompts and code shown below (as of when each ran). Use this
history to judge whether episode quality reflects recent changes — call out when a change is clearly
working, clearly not, or too recent to tell — and to root your Improvement Action Plan in what's
already been tried versus what's still untouched.

{recent_changes}

---

## Scripts to Review

Each script is labeled with the theme assigned to its date (see Daily Theme Rotation above and Dimension 12).

{scripts_text}

---

## Instructions

For **each script**, provide:
- A score table (dimension → score 1–5 → one-line rationale)
- **Flags**: Specific problems with exact quotes and the rule violated
- **Strengths**: What worked well with specific examples

Then provide a **Cross-Episode Synthesis** covering:
- Recurring patterns across these episodes (good and bad)
- Whether the prompt/code changes listed above are visible in this week's episodes — did a change
  measurably help, measurably hurt, or is it too early to judge?
- Overall theme adherence across the week — is the daily rotation doing real editorial work, or are
  episodes converging on the same handful of moves regardless of their assigned theme?
- The 2–3 most urgent issues to fix

Finally, close with a section titled `## Improvement Action Plan` — a numbered, priority-ordered list
of concrete next steps (most urgent first). For each item, include all four of:
1. **Problem** — one line, naming the episode(s) where it showed up
2. **Lever** — the exact thing to change: a template key in `config/prompts.json`, a specific config
   file/key, or a step in `podcast_generator.py` — name it precisely, don't say "the prompt"
3. **Change** — the specific edit to make, in enough detail that someone could make it without
   re-reading the scripts
4. **Signal** — what to look for in next week's episodes that would confirm it worked

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

    recent_changes = summarize_recent_changes(days)

    client = anthropic.Anthropic()

    response = client.messages.create(
        model=REVIEW_MODEL,
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_review_prompt(scripts, config, recent_changes)}],
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
    parser.add_argument("--days", type=int, default=7, help="Number of days to review (default: 7)")
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
