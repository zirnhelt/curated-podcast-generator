#!/usr/bin/env bash
# Walks through obtaining a new GMAIL_REFRESH_TOKEN and updating the GitHub secret.
set -euo pipefail

VENV_DIR="$HOME/.venv/gmail-auth"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "=================================================="
echo "  Gmail Refresh Token Setup"
echo "=================================================="
echo ""

# ---------------------------------------------------------------------------
# Step 1 – Python venv
# ---------------------------------------------------------------------------
echo "Step 1/5 — Setting up Python virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "  Created venv at $VENV_DIR"
else
    echo "  Venv already exists at $VENV_DIR"
fi
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"
echo "  Activated."
echo ""

# ---------------------------------------------------------------------------
# Step 2 – Dependencies
# ---------------------------------------------------------------------------
echo "Step 2/5 — Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet google-auth google-auth-oauthlib google-api-python-client
echo "  Done."
echo ""

# ---------------------------------------------------------------------------
# Step 3 – Get credentials from Google Cloud Console
# ---------------------------------------------------------------------------
echo "Step 3/5 — OAuth client credentials"
echo ""
echo "  You need your GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET."
echo "  These come from Google Cloud Console, not GitHub."
echo ""
echo "  To find them:"
echo "    1. Go to https://console.cloud.google.com"
echo "    2. Select your project (top-left dropdown)"
echo "    3. Navigate to: APIs & Services → Credentials"
echo "    4. Under 'OAuth 2.0 Client IDs', click your client name"
echo "    5. Copy the 'Client ID' and 'Client Secret' shown on that page"
echo ""
read -rp "  Paste your Client ID:     " GMAIL_CLIENT_ID
if [ -z "$GMAIL_CLIENT_ID" ]; then
    echo "  ERROR: Client ID cannot be empty." >&2
    exit 1
fi

read -rsp "  Paste your Client Secret: " GMAIL_CLIENT_SECRET
echo ""
if [ -z "$GMAIL_CLIENT_SECRET" ]; then
    echo "  ERROR: Client Secret cannot be empty." >&2
    exit 1
fi
echo ""

# ---------------------------------------------------------------------------
# Step 4 – Run the auth flow
# ---------------------------------------------------------------------------
echo "Step 4/5 — Running OAuth flow..."
echo ""
echo "  A browser window will open. Log in with the Google account"
echo "  that owns the Gmail inbox, then click Allow."
echo ""
export GMAIL_CLIENT_ID
export GMAIL_CLIENT_SECRET

cd "$REPO_DIR"
REFRESH_TOKEN_OUTPUT=$(python email_ingest.py --auth 2>&1)
echo "$REFRESH_TOKEN_OUTPUT"

# Extract the token value from the output line: GMAIL_REFRESH_TOKEN=<value>
REFRESH_TOKEN=$(echo "$REFRESH_TOKEN_OUTPUT" | grep '^GMAIL_REFRESH_TOKEN=' | cut -d'=' -f2-)
if [ -z "$REFRESH_TOKEN" ]; then
    echo ""
    echo "  ERROR: Could not extract refresh token from output above." >&2
    echo "  Check the error messages and try again." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 5 – Update GitHub secret
# ---------------------------------------------------------------------------
echo ""
echo "Step 5/5 — Update the GitHub secret"
echo ""
echo "  Your new refresh token is:"
echo ""
echo "    $REFRESH_TOKEN"
echo ""
echo "  Now update the GMAIL_REFRESH_TOKEN secret in GitHub:"
echo "    1. Go to your repository on GitHub"
echo "    2. Settings → Secrets and variables → Actions"
echo "    3. Find GMAIL_REFRESH_TOKEN and click Update"
echo "    4. Paste the token above and save"
echo ""
echo "  Then re-run the 'Ingest emails' workflow to confirm it works."
echo ""
echo "=================================================="
echo "  Done!"
echo "=================================================="
echo ""

deactivate 2>/dev/null || true
