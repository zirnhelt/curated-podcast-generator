#!/usr/bin/env bash
# SessionStart: make `python -m pytest tests/` work in a fresh cloud container.
# Local machines are left alone; reinstalls only when requirements-lock.txt changes.
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0
cd "$CLAUDE_PROJECT_DIR" || exit 0
stamp="${HOME}/.cache/cariboo-test-deps.sha"
want="$(sha256sum requirements-lock.txt | cut -d' ' -f1)"
if [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$want" ] && python -c 'import pytest' 2>/dev/null; then
  exit 0
fi
if python -m pip install -q --disable-pip-version-check -r requirements-lock.txt pytest >/dev/null 2>&1; then
  mkdir -p "$(dirname "$stamp")" && echo "$want" > "$stamp"
else
  echo "test deps install failed; run: pip install -r requirements-lock.txt pytest" >&2
fi
exit 0
