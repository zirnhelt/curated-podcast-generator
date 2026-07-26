#!/usr/bin/env bash
# debug-seed-dispatch.sh — Verify your GitHub PAT and seed-content workflow dispatch
#
# Usage:
#   SEED_PAT=ghp_xxxx ./scripts/debug-seed-dispatch.sh [content]
#   ./scripts/debug-seed-dispatch.sh <PAT> [content]        # legacy, see note
#
# Example:
#   SEED_PAT=ghp_xxxx ./scripts/debug-seed-dispatch.sh "https://example.com/article"
#   SEED_PAT=ghp_xxxx ./scripts/debug-seed-dispatch.sh "What if communities owned their LTE towers?" --type thought
#
# Prefer SEED_PAT: a token passed as an argument is visible to every other
# process on the box via `ps` and lands in your shell history.
#
# What this checks:
#   1. PAT has the 'workflow' scope (required for workflow_dispatch)
#   2. The seed-content.yml workflow is reachable on main
#   3. The dispatch API call returns 204 (real success), not 4xx (silent failure)
#
# Common failures:
#   401  — "Bad credentials": PAT is expired or revoked. PATs expire on a fixed
#          date; the iOS Shortcut keeps working until that day and then silently
#          stops seeding. Regenerate at github.com/settings/tokens and update
#          the Authorization header in the Shortcut.
#   403  — PAT is missing the 'workflow' scope (add it at github.com/settings/tokens)
#   404  — Workflow file not found on 'main', or wrong repo owner/name
#   422  — Invalid inputs (check required fields: type, content)

set -euo pipefail

REPO="zirnhelt/curated-podcast-generator"
WORKFLOW="seed-content.yml"
REF="main"

PAT="${SEED_PAT:-}"
CONTENT=""
TYPE="url"

# When SEED_PAT is unset, the first positional argument is the PAT (legacy form).
if [[ -z "$PAT" && $# -gt 0 ]]; then
  PAT="$1"
  shift
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --type) TYPE="${2:-url}"; shift 2 ;;
    *) CONTENT="$1"; shift ;;
  esac
done

CONTENT="${CONTENT:-https://example.com/test-article}"

if [[ -z "$PAT" ]]; then
  echo "Usage: SEED_PAT=<token> $0 [content] [--type url|thought]"
  echo "       $0 <PAT> [content] [--type url|thought]"
  echo ""
  echo "Get a PAT at: https://github.com/settings/tokens"
  echo "Classic token scopes: repo + workflow"
  echo "Fine-grained token permissions: Contents (read/write) + Actions (read/write)"
  exit 1
fi

echo "=== Seed Dispatch Diagnostic ==="
echo "Repo:     $REPO"
echo "Workflow: $WORKFLOW"
echo "Ref:      $REF"
echo "Type:     $TYPE"
echo "Content:  $CONTENT"
echo ""

# Step 1: Check PAT scopes
echo "--- Step 1: Checking PAT scopes ---"
SCOPE_RESPONSE=$(curl -si \
  -H "Authorization: Bearer $PAT" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/user" 2>&1)

# Both greps must tolerate "no match": a 401 body carries no x-oauth-scopes
# header, and under `set -euo pipefail` a bare failing grep in a command
# substitution aborts the script before the 401 handler below can report it.
HTTP_STATUS=$(echo "$SCOPE_RESPONSE" | grep -i "^HTTP/" | tail -1 | awk '{print $2}' || true)
SCOPES=$(echo "$SCOPE_RESPONSE" | grep -i "x-oauth-scopes:" | sed 's/.*: //' | tr -d '\r' || true)

if [[ "$HTTP_STATUS" == "401" ]]; then
  echo "ERROR: 401 Bad credentials — the PAT is expired or revoked."
  echo "This is the failure mode where seeding just stops working one day:"
  echo "the token hit its expiry date and every dispatch now 401s."
  echo "Fix: regenerate at https://github.com/settings/tokens, then update the"
  echo "     Authorization header in the iOS Shortcut with the new token."
  exit 1
fi

if [[ "$HTTP_STATUS" != "200" ]]; then
  echo "ERROR: PAT authentication failed (HTTP $HTTP_STATUS)"
  echo "Check that your PAT is valid and not expired."
  exit 1
fi

echo "HTTP status: $HTTP_STATUS (OK)"
echo "Scopes:      ${SCOPES:-<none — fine-grained token>}"

# Fine-grained tokens send no x-oauth-scopes header; their permissions can only
# be checked by making the real call, which Step 3 does.
if [[ -z "$SCOPES" ]]; then
  echo "Fine-grained token detected — scopes cannot be read from the API."
  echo "It needs Actions: read/write on $REPO. Step 3 is the real check."
elif ! echo "$SCOPES" | grep -q "workflow"; then
  echo ""
  echo "ERROR: PAT is missing the 'workflow' scope!"
  echo "Without this scope, GitHub silently accepts the API call but never fires the action."
  echo "Fix: Go to github.com/settings/tokens → regenerate token with 'workflow' scope enabled."
  exit 1
else
  echo "✓ 'workflow' scope present"
fi
echo ""

# Step 2: Check workflow exists on main
echo "--- Step 2: Checking workflow file on '$REF' ---"
WF_RESPONSE=$(curl -si \
  -H "Authorization: Bearer $PAT" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/$REPO/contents/.github/workflows/$WORKFLOW?ref=$REF" 2>&1)

WF_STATUS=$(echo "$WF_RESPONSE" | grep -i "^HTTP/" | tail -1 | awk '{print $2}' || true)

if [[ "$WF_STATUS" == "200" ]]; then
  echo "✓ Workflow file found on '$REF' (HTTP $WF_STATUS)"
elif [[ "$WF_STATUS" == "404" ]]; then
  echo "ERROR: Workflow file not found on '$REF' (HTTP $WF_STATUS)"
  echo "The workflow must be on the default branch to be triggerable."
  exit 1
else
  echo "WARNING: Unexpected HTTP $WF_STATUS when checking workflow file"
fi
echo ""

# Step 3: Trigger the dispatch
echo "--- Step 3: Triggering workflow_dispatch ---"

PAYLOAD=$(cat <<EOF
{
  "ref": "$REF",
  "inputs": {
    "type": "$TYPE",
    "content": "$CONTENT",
    "note": "test from test-seed-dispatch.sh",
    "priority": "normal",
    "theme_hint": ""
  }
}
EOF
)

DISPATCH_RESPONSE=$(curl -si \
  -X POST \
  -H "Authorization: Bearer $PAT" \
  -H "Accept: application/vnd.github+json" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  "https://api.github.com/repos/$REPO/actions/workflows/$WORKFLOW/dispatches" 2>&1)

DISPATCH_STATUS=$(echo "$DISPATCH_RESPONSE" | grep -i "^HTTP/" | tail -1 | awk '{print $2}' || true)
DISPATCH_BODY=$(echo "$DISPATCH_RESPONSE" | tail -5)

echo "HTTP status: $DISPATCH_STATUS"

case "$DISPATCH_STATUS" in
  204)
    echo ""
    echo "SUCCESS! Workflow dispatch accepted (204 No Content)."
    echo "Check: https://github.com/$REPO/actions/workflows/$WORKFLOW"
    ;;
  401)
    echo ""
    echo "ERROR: 401 Unauthorized — PAT is invalid or expired."
    echo "Regenerate your PAT at: https://github.com/settings/tokens"
    echo "Required scopes: repo + workflow"
    echo "Response body: $DISPATCH_BODY"
    ;;
  403)
    echo ""
    echo "ERROR: 403 Forbidden — PAT lacks required permissions."
    echo "Ensure your PAT has both 'repo' and 'workflow' scopes."
    echo "Response body: $DISPATCH_BODY"
    ;;
  404)
    echo ""
    echo "ERROR: 404 Not Found — workflow or repo not found."
    echo "Check: repo name '$REPO', workflow '$WORKFLOW', ref '$REF'"
    echo "Response body: $DISPATCH_BODY"
    ;;
  422)
    echo ""
    echo "ERROR: 422 Unprocessable — inputs validation failed."
    echo "Required inputs: type (url|thought), content (non-empty)"
    echo "Response body: $DISPATCH_BODY"
    ;;
  *)
    echo ""
    echo "Unexpected HTTP $DISPATCH_STATUS"
    echo "Response body: $DISPATCH_BODY"
    ;;
esac
