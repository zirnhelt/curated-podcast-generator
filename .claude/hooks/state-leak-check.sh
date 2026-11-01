#!/usr/bin/env bash
# Stop hook: catch writes into the live podcasts/ state files (CLAUDE.md "State-file isolation").
# ponytail: git status, not a file watcher.
input=$(cat)
# Claude already saw this warning once this turn; don't loop.
grep -q '"stop_hook_active": *true' <<<"$input" && exit 0
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
leaked=$(git status --porcelain -- 'podcasts/*.json' 2>/dev/null)
[ -z "$leaked" ] && exit 0
{
  echo "podcasts/ state files are modified (CI owns these):"
  echo "$leaked"
  echo "If a test or local run wrote them: git checkout -- <file>. If intentional, tell the user why."
} >&2
exit 2
