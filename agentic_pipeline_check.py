#!/usr/bin/env python3
"""
Standalone smoke test for the agentic research and polish/fact-check loops.

Runs research_deep_dive_with_agent() and polish_and_factcheck_with_agent()
against small hand-built sample data using real Anthropic (and, if
configured, Brave Search) API calls. Intended to be triggered from GitHub
Actions (.github/workflows/test-agentic-loop.yml) to sanity-check the
agentic tool-use loop end-to-end without running the full daily pipeline.

Usage:
    ANTHROPIC_API_KEY=... [BRAVE_SEARCH_API_KEY=...] python test_agentic_pipeline.py
"""

import os
import sys

from podcast_generator import (
    get_anthropic_client,
    research_deep_dive_with_agent,
    polish_and_factcheck_with_agent,
)

SAMPLE_DEEP_DIVE_ARTICLES = [
    {
        "title": "Starlink expands rural coverage in BC interior",
        "summary": (
            "Satellite internet provider Starlink announced expanded capacity "
            "for rural British Columbia communities, citing demand from remote "
            "households."
        ),
        "url": "https://example.com/starlink-bc",
        "_body": (
            "Starlink, the satellite internet service operated by SpaceX, said "
            "this week it has added capacity over the BC interior, aiming to "
            "reduce wait times for new rural subscribers."
        ),
    },
    {
        "title": "Cariboo Regional District reviews broadband strategy",
        "summary": (
            "Local government staff presented an updated broadband connectivity "
            "strategy covering underserved areas of the Cariboo."
        ),
        "url": "https://example.com/crd-broadband",
        "_body": (
            "The Cariboo Regional District board reviewed a staff report on "
            "broadband connectivity gaps and possible next steps for "
            "underserved electoral areas."
        ),
    },
]

SAMPLE_NEWS_ARTICLES = [
    {
        "title": "Quesnel council approves new bike lane pilot",
        "summary": "City council approved a pilot project for a downtown bike lane.",
        "url": "https://example.com/quesnel-bike-lane",
    },
]

SAMPLE_SCRIPT = """**RILEY:** Welcome to Cariboo Signals, an AI-generated review of the latest tech news and ideas that impact our rural communities. We're coming to you from the Cariboo region, the traditional territories of the Secwépemc, Tŝilhqot'in, and Dakelh nations. Today's theme is Tech & Gadgets — I'm Riley.
**CASEY:** And I'm Casey. We've got a packed show today.
**RILEY:** Let's get into the News Roundup.

**NEWS ROUNDUP**
**RILEY:** Quesnel council approved a new downtown bike lane pilot this week.
**CASEY:** Good first step, though I wonder what it costs to maintain per kilometre.
**RILEY:** Onward to today's Community Spotlight.

**COMMUNITY SPOTLIGHT**
**CASEY:** Big shoutout to the local food bank for their winter drive.
**RILEY:** Always great work from them.

**DEEP DIVE: CARIBOO CONNECTIONS - Tech & Gadgets**
**RILEY:** Starlink just expanded its rural BC capacity, and that's a big deal for places without fiber.
**CASEY:** Sure, but what does a Starlink dish actually weigh, and what's the real monthly cost out here?
**RILEY:** The Cariboo Regional District is also reviewing its broadband strategy, so there's local momentum too.
**CASEY:** Local momentum is good, but I'd want to know the actual budget figures before calling it progress.
**RILEY:** Either way, it's worth keeping an eye on. If you have a correction, a story tip, or want to get involved, feedback at cariboo signals dot c-a.
**CASEY:** Take care, everyone. See you tomorrow.
"""


def main():
    if not get_anthropic_client():
        print("❌ ANTHROPIC_API_KEY not set — cannot run agentic loop tests")
        sys.exit(1)

    if os.getenv("PODCAST_DEBUG_AGENT") is None:
        os.environ["PODCAST_DEBUG_AGENT"] = "1"

    if not os.getenv("BRAVE_SEARCH_API_KEY"):
        print("ℹ️  BRAVE_SEARCH_API_KEY not set — web_search tool will be unavailable, "
              "loops should still complete without it.\n")

    client = get_anthropic_client()
    failures = []

    print("=" * 60)
    print("1. research_deep_dive_with_agent")
    print("=" * 60)
    research = ""
    try:
        research = research_deep_dive_with_agent(SAMPLE_DEEP_DIVE_ARTICLES, "Tech & Gadgets", client)
        print(f"\nResult ({len(research)} chars):\n{research or '(empty — no research warranted)'}")
        if research and "PRE-RESEARCHED INSIGHTS" not in research:
            failures.append("research result missing expected 'PRE-RESEARCHED INSIGHTS' header")
    except Exception as e:
        failures.append(f"research_deep_dive_with_agent raised: {e}")

    print("\n" + "=" * 60)
    print("2. polish_and_factcheck_with_agent")
    print("=" * 60)
    try:
        polished = polish_and_factcheck_with_agent(
            SAMPLE_SCRIPT, "Tech & Gadgets", SAMPLE_NEWS_ARTICLES, SAMPLE_DEEP_DIVE_ARTICLES,
            research_insights=research or None,
        )
        print(f"\nResult ({len(polished)} chars):\n{polished}")
        if "**RILEY:**" not in polished or "**CASEY:**" not in polished:
            failures.append("polished script missing RILEY/CASEY markers")
        if polished.strip() == SAMPLE_SCRIPT.strip():
            print("\n⚠️  Polished script identical to input — agentic call may have "
                  "failed and fallen back to the original script")
    except Exception as e:
        failures.append(f"polish_and_factcheck_with_agent raised: {e}")

    print("\n" + "=" * 60)
    if failures:
        print(f"❌ {len(failures)} check(s) failed:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)

    print("✅ All agentic loop checks passed")


if __name__ == "__main__":
    main()
