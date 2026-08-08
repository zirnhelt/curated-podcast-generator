## Context

Why this change was needed. What problem does it solve? Any architectural tradeoffs or constraints worth noting?

## Summary

What changed. Keep this concise — the Context section explains the motivation and tradeoffs above.

## New dependencies or breaking changes

- Any additions to `requirements.txt`? Version pinned? Compatibility notes?
- Any changes to output/config format? Schema updates to the memory/state JSON in `podcasts/`, citations, or the feed XML?
- None if not applicable.

## API cost impact

- Did Claude model choice, prompt size, batch size, or call frequency change?
- OpenAI/Azure/Gemini TTS or Cohere/Brave call volume changes?
- Net impact on per-episode/daily API costs?
- None if not applicable.

## Config changes

- Files under `config/` touched? Which ones?
- Prompt changes in `prompts.json` — which pass (generation, polish, cold open, Gemini TTS style)?
- Any changes to themes, super-cycle focuses, the scoring rubric, or the blocklist?
- None if not applicable.

## Stages, segments & degrade()

- Which stage(s) are affected (script/recover/render/publish)?
- New `segment()` blocks — critical or non-critical, and are the block's outputs pre-assigned to their fallback?
- New fallback paths — does each one call `degrade()`?
- Any change to exit codes 75–78?
- None if not applicable.

## Testing

- What test? (pytest, a local `--stage` run, a re-render against a past date, etc.)
- Command or steps to reproduce verification?
- `git status` clean afterwards — no leaked `podcasts/` state-file writes?
- None if verification is obvious from the diff and commit message.
