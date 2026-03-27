#!/usr/bin/env bash
# debug-seed-dispatch.sh — Verify your GitHub PAT and seed-content workflow dispatch
#
# Usage:
#   ./scripts/debug-seed-dispatch.sh <PAT> [content] [--type url|thought] [--method repo|workflow]
#
# Examples:
#   ./scripts/debug-seed-dispatch.sh ghp_xxxx "https://example.com/article"
#   ./scripts/debug-seed-dispatch.sh ghp_xxxx "https://example.com/article" --method repo
#   ./scripts/debug-seed-dispatch.sh ghp_xxxx "What if communities owned their LTE towers?" --type thought
#
# What this checks:
#   1. PAT is valid and not expired
#   2. PAT has the scope required for the chosen method:
#        --method repo      → 'repo' scope only  (RECOMMENDED for iOS Shortcuts)
#        --method workflow  → 'repo' + 'workflow' scopes
#   3. The dispatch API call returns 204 (real success), not 4xx
#
# Common failures:
#   401  — PAT is expired or invalid
#   403  — PAT is missing required scope, or secondary rate limit hit
#           (secondary rate limit: "You have exceeded a secondary rate limit")
#   404  — Workflow file not found on 'main', or wrong repo owner/name
#   422  — Invalid inputs (check required fields: type, content)
#   429  — Secondary rate limit (retry after the Retry-After header value)
#
# iOS Shortcut recommendation:
#   Use --method repo (repository_dispatch). It only requires 'repo' scope and
#   is less prone to GitHub secondary rate limits than workflow_dispatch.
#   If your Shortcut gets a 403 "Bad credentials" response, your PAT is likely
#   missing the 'workflow' scope — switch to repository_dispatch instead.

set -euo pipefail

REPO="zirnhelt/curated-podcast-generator"
WORKFLOW="seed-content.yml"
REF="main"

PAT="${1:-}"
CONTENT="${2:-https://example.com/test-article}"
TYPE="url"
METHOD="repo"   # default: repository_dispatch (recommended)

# Parse optional flags
i=3
while [[ $# -ge $i ]]; do
  arg="${!i}"
  case "$arg" in
    --type)
      i=$((i+1)); TYPE="${!i}"
      ;;
    --method)
      i=$((i+1)); METHOD="${!i}"
      ;;
  esac
  i=$((i+1))
done

if [[ -z "$PAT" ]]; then
  echo "Usage: $0 <PAT> [content] [--type url|thought] [--method repo|workflow]"
  echo ""
  echo "Get a PAT at: https://github.com/settings/tokens"
  echo "  --method repo     requires: 'repo' scope only  (recommended for iOS Shortcuts)"
  echo "  --method workflow requires: 'repo' + 'workflow' scopes"
  exit 1
fi

echo "=== Seed Dispatch Diagnostic ==="
echo "Repo:     $REPO"
echo "Method:   $METHOD ($([ "$METHOD" = "repo" ] && echo "repository_dispatch — recommended" || echo "workflow_dispatch"))"
echo "Type:     $TYPE"
echo "Content:  $CONTENT"
echo ""

# Step 1: Check PAT validity and scopes
echo "--- Step 1: Checking PAT validity and scopes ---"
SCOPE_RESPONSE=$(curl -si \
  -H "Authorization: Bearer $PAT" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/user" 2>&1)

HTTP_STATUS=$(echo "$SCOPE_RESPONSE" | grep -i "^HTTP/" | tail -1 | awk '{print $2}')
SCOPES=$(echo "$SCOPE_RESPONSE" | grep -i "x-oauth-scopes:" | sed 's/.*: //' | tr -d '\r')
RETRY_AFTER=$(echo "$SCOPE_RESPONSE" | grep -i "^retry-after:" | awk '{print $2}' | tr -d '\r')

if [[ "$HTTP_STATUS" == "429" ]] || echo "$SCOPE_RESPONSE" | grep -qi "secondary rate limit"; then
  echo "ERROR: GitHub secondary rate limit hit (HTTP $HTTP_STATUS)"
  echo "You have exceeded GitHub's secondary rate limit."
  if [[ -n "$RETRY_AFTER" ]]; then
    echo "Retry after: ${RETRY_AFTER}s"
  else
    echo "Wait 60–120 seconds before retrying."
  fi
  echo "Tip: iOS Shortcuts that use workflow_dispatch are more prone to this."
  echo "     Switch your Shortcut to repository_dispatch (--method repo) to reduce this."
  exit 1
fi

if [[ "$HTTP_STATUS" != "200" ]]; then
  echo "ERROR: PAT authentication failed (HTTP $HTTP_STATUS)"
  echo "Check that your PAT is valid and not expired."
  exit 1
fi

echo "HTTP status: $HTTP_STATUS (OK)"
echo "Scopes:      ${SCOPES:-<none — may be a fine-grained PAT>}"

# Scope check depends on method
if [[ "$METHOD" == "workflow" ]]; then
  if ! echo "$SCOPES" | grep -q "workflow"; then
    echo ""
    echo "ERROR: PAT is missing the 'workflow' scope required for workflow_dispatch!"
    echo "Fix options:"
    echo "  1. Add 'workflow' scope: github.com/settings/tokens → regenerate token"
    echo "  2. Switch to repository_dispatch: rerun with --method repo (only needs 'repo' scope)"
    echo ""
    echo "iOS Shortcuts recommendation: use repository_dispatch (--method repo) to avoid"
    echo "both this error and GitHub secondary rate limits."
    exit 1
  fi
  echo "✓ 'workflow' scope present"
else
  if ! echo "$SCOPES" | grep -q "\brepo\b"; then
    echo ""
    echo "WARNING: PAT may be missing 'repo' scope (needed for repository_dispatch)."
    echo "Scopes found: ${SCOPES:-<none>}"
    echo "If the dispatch fails with 403, add 'repo' scope at github.com/settings/tokens"
  else
    echo "✓ 'repo' scope present"
  fi
fi
echo ""

# Step 2: Check workflow exists on main (only needed for workflow_dispatch)
if [[ "$METHOD" == "workflow" ]]; then
  echo "--- Step 2: Checking workflow file on '$REF' ---"
  WF_RESPONSE=$(curl -si \
    -H "Authorization: Bearer $PAT" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/$REPO/contents/.github/workflows/$WORKFLOW?ref=$REF" 2>&1)

  WF_STATUS=$(echo "$WF_RESPONSE" | grep -i "^HTTP/" | tail -1 | awk '{print $2}')

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
fi

# Step 3: Trigger the dispatch
if [[ "$METHOD" == "repo" ]]; then
  echo "--- Step 2: Triggering repository_dispatch (recommended) ---"
  PAYLOAD=$(cat <<EOF
{
  "event_type": "seed-content",
  "client_payload": {
    "type": "$TYPE",
    "content": "$CONTENT",
    "note": "test from debug-seed-dispatch.sh",
    "priority": "normal",
    "theme_hint": ""
  }
}
EOF
)
  DISPATCH_URL="https://api.github.com/repos/$REPO/dispatches"
else
  echo "--- Step 3: Triggering workflow_dispatch ---"
  PAYLOAD=$(cat <<EOF
{
  "ref": "$REF",
  "inputs": {
    "type": "$TYPE",
    "content": "$CONTENT",
    "note": "test from debug-seed-dispatch.sh",
    "priority": "normal",
    "theme_hint": ""
  }
}
EOF
)
  DISPATCH_URL="https://api.github.com/repos/$REPO/actions/workflows/$WORKFLOW/dispatches"
fi

DISPATCH_RESPONSE=$(curl -si \
  -X POST \
  -H "Authorization: Bearer $PAT" \
  -H "Accept: application/vnd.github+json" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  "$DISPATCH_URL" 2>&1)

DISPATCH_STATUS=$(echo "$DISPATCH_RESPONSE" | grep -i "^HTTP/" | tail -1 | awk '{print $2}')
DISPATCH_BODY=$(echo "$DISPATCH_RESPONSE" | tail -5)
RETRY_AFTER=$(echo "$DISPATCH_RESPONSE" | grep -i "^retry-after:" | awk '{print $2}' | tr -d '\r')

echo "HTTP status: $DISPATCH_STATUS"

case "$DISPATCH_STATUS" in
  204)
    echo ""
    echo "SUCCESS! Dispatch accepted (204 No Content)."
    echo "Check: https://github.com/$REPO/actions"
    ;;
  401)
    echo ""
    echo "ERROR: 401 Unauthorized — PAT is invalid or expired."
    echo "Generate a new PAT at: github.com/settings/tokens"
    echo "Response body: $DISPATCH_BODY"
    ;;
  403)
    if echo "$DISPATCH_BODY" | grep -qi "secondary rate limit"; then
      echo ""
      echo "ERROR: 403 — GitHub secondary rate limit hit."
      echo "You've made too many requests in a short time."
      if [[ -n "$RETRY_AFTER" ]]; then
        echo "Retry after: ${RETRY_AFTER}s"
      else
        echo "Wait 60–120 seconds before retrying."
      fi
      echo ""
      echo "To reduce secondary rate limit exposure from your iOS Shortcut:"
      echo "  • Use repository_dispatch (--method repo) instead of workflow_dispatch"
      echo "  • Avoid running the Shortcut more than once per minute"
    else
      echo ""
      echo "ERROR: 403 Forbidden — PAT lacks required permissions."
      if [[ "$METHOD" == "workflow" ]]; then
        echo "Ensure your PAT has both 'repo' and 'workflow' scopes."
        echo "Or switch to repository_dispatch: rerun with --method repo (only needs 'repo' scope)"
      else
        echo "Ensure your PAT has 'repo' scope."
      fi
      echo "Response body: $DISPATCH_BODY"
    fi
    ;;
  404)
    echo ""
    echo "ERROR: 404 Not Found — repo or workflow not found."
    echo "Check: repo name '$REPO', ref '$REF'"
    echo "Response body: $DISPATCH_BODY"
    ;;
  422)
    echo ""
    echo "ERROR: 422 Unprocessable — inputs validation failed."
    echo "Required: type (url|thought), content (non-empty)"
    echo "Response body: $DISPATCH_BODY"
    ;;
  429)
    echo ""
    echo "ERROR: 429 — Secondary rate limit."
    if [[ -n "$RETRY_AFTER" ]]; then
      echo "Retry after: ${RETRY_AFTER}s"
    else
      echo "Wait 60–120 seconds before retrying."
    fi
    echo "Switch your iOS Shortcut to repository_dispatch (--method repo) to reduce this."
    ;;
  *)
    echo ""
    echo "Unexpected HTTP $DISPATCH_STATUS"
    echo "Response body: $DISPATCH_BODY"
    ;;
esac
