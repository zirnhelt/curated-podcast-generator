#!/usr/bin/env python3
"""
Email ingest for the Cariboo Signals podcast generator — Gmail API edition.

Connects to Gmail via OAuth2, fetches unread messages from a configured label,
classifies them as newsletter or listener feedback, sanitizes body text against
prompt injection, auto-assigns a podcast theme via keyword scoring, and appends
items to podcasts/email_queue.json for pickup by the daily generation run.

Usage:
  python email_ingest.py [--dry-run]

Required environment variables (store as GitHub Secrets):
  GMAIL_CLIENT_ID       OAuth2 client ID from Google Cloud Console
  GMAIL_CLIENT_SECRET   OAuth2 client secret
  GMAIL_REFRESH_TOKEN   Offline refresh token (obtained once via OAuth flow)

Optional:
  GMAIL_LABEL           Gmail label to filter (default: INBOX).
                        Use a specific label like "newsletters" or "podcast" to
                        target only pre-labelled emails.  Supports nested labels
                        with "/" e.g. "podcast/incoming".

One-time setup (local, run once to get GMAIL_REFRESH_TOKEN):
  1. Create a Google Cloud project and enable the Gmail API.
  2. Create OAuth2 credentials (Desktop app type) — download client_secret.json.
  3. Run:  python email_ingest.py --auth
     This opens a browser, asks you to approve Gmail access, and prints a
     refresh token to store in GitHub Secrets.

Security note:
  All body text is sanitized (HTML stripped, prompt-injection chars removed,
  truncated) before storage.  Newsletter body is NOT forwarded to Claude — only
  freshly fetched URL metadata is used.  Feedback body is wrapped in explicit
  untrusted-content delimiters in the script prompt.
"""

import argparse
import base64
import email
import email.header
import email.utils
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


SCRIPT_DIR = Path(__file__).parent
QUEUE_FILE = SCRIPT_DIR / "podcasts" / "email_queue.json"
THEMES_FILE = SCRIPT_DIR / "config" / "themes.json"

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
TOKEN_URI = "https://oauth2.googleapis.com/token"

# Feedback bodies are truncated to this many chars before storage/prompting
FEEDBACK_MAX_CHARS = 500
# Newsletter preview kept short — only used for theme scoring
NEWSLETTER_MAX_CHARS = 200
URL_MIN_LEN = 10


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------

class _TextExtractor(HTMLParser):
    """Minimal HTML → plain-text extractor."""

    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip_tags = {"script", "style", "head"}
        self._in_skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._skip_tags:
            self._in_skip += 1
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self._skip_tags:
            self._in_skip = max(0, self._in_skip - 1)

    def handle_data(self, data):
        if not self._in_skip:
            self._parts.append(data)

    def get_text(self):
        return " ".join("".join(self._parts).split())


def _strip_html(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        return parser.get_text()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)


# ---------------------------------------------------------------------------
# Sanitization (prompt injection defence)
# ---------------------------------------------------------------------------

def _mask_email(addr: str) -> str:
    """Return a privacy-safe version of an email address for storage.

    Full address  →  first char + *** + @domain.tld
    "Alice <alice@example.com>"  →  "a***@example.com"
    Used so that email_queue.json (which is committed to the repo) does not
    contain full email addresses should it ever be accidentally re-published.
    """
    m = re.search(r"([^<@\s]+)@([\w.\-]+)", addr)
    if not m:
        return "[redacted]"
    local, domain = m.group(1), m.group(2)
    return f"{local[0]}***@{domain}"


_INJECTION_PATTERN = re.compile(
    r"<[^>]*>"            # residual HTML/XML tags
    r"|{{.*?}}"           # Jinja/template delimiters
    r"|\[\[.*?\]\]"       # wiki-style brackets
    r"|\{%.*?%\}"         # template tags
    r"|`{3,}"             # triple backtick fences
    r"|\bsystem\b\s*:"    # role markers
    r"|\bASSISTANT\b\s*:"
    r"|\bUSER\b\s*:"
    r"|\bHUMAN\b\s*:",
    re.IGNORECASE | re.DOTALL,
)


def _sanitize(text: str, max_chars: int) -> str:
    """Strip HTML, remove prompt-injection patterns, normalize whitespace, truncate."""
    if not text:
        return ""
    text = _strip_html(text)
    text = _INJECTION_PATTERN.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


# ---------------------------------------------------------------------------
# URL extraction and validation
# ---------------------------------------------------------------------------

_URL_PATTERN = re.compile(r"https?://[^\s<>\"')\]]+")
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "169.254.169.254"}


def _is_safe_url(url: str) -> bool:
    try:
        parts = urlparse(url)
    except Exception:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = parts.netloc.split(":")[0].lower()
    if not host or host in _BLOCKED_HOSTS:
        return False
    ip_match = re.match(r"^(\d+)\.(\d+)\.", host)
    if ip_match:
        a, b = int(ip_match.group(1)), int(ip_match.group(2))
        if a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168):
            return False
    return len(url) >= URL_MIN_LEN


def _extract_urls(plain: str, html: str) -> list:
    raw = _URL_PATTERN.findall(plain + " " + html)
    seen, result = set(), []
    for url in raw:
        url = url.rstrip(".,;:!?\"'")
        if url not in seen and _is_safe_url(url):
            seen.add(url)
            result.append(url)
        if len(result) >= 10:
            break
    return result


# ---------------------------------------------------------------------------
# Theme scoring
# ---------------------------------------------------------------------------

def _load_themes() -> dict:
    try:
        with open(THEMES_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"  ⚠️  Could not load themes config: {e}", file=sys.stderr)
        return {}


def _load_email_sender_blocklist() -> dict:
    blocklist_file = SCRIPT_DIR / "config" / "blocklist.json"
    try:
        with open(blocklist_file) as f:
            data = json.load(f)
        return data.get("email_sender_blocklist", {})
    except (OSError, json.JSONDecodeError) as e:
        print(f"  ⚠️  Could not load sender blocklist: {e}", file=sys.stderr)
        return {}


def _load_subject_blocklist() -> list:
    blocklist_file = SCRIPT_DIR / "config" / "blocklist.json"
    try:
        with open(blocklist_file) as f:
            data = json.load(f)
        return [p.lower() for p in data.get("email_subject_blocklist", {}).get("patterns", [])]
    except (OSError, json.JSONDecodeError) as e:
        print(f"  ⚠️  Could not load subject blocklist: {e}", file=sys.stderr)
        return []


def _is_blocked_subject(subject: str, patterns: list) -> bool:
    """Return True if the subject starts with any blocked pattern (whole-word, case-insensitive)."""
    subj = subject.strip().lower()
    for pattern in patterns:
        if re.match(r"^" + re.escape(pattern) + r"(\s|$)", subj):
            return True
    return False


def _is_blocked_sender(from_address: str, blocklist: dict) -> bool:
    """Return True if the sender should be rejected based on domain or pattern."""
    addr_lower = from_address.lower()
    for pattern in blocklist.get("patterns", []):
        if pattern.lower() in addr_lower:
            return True
    match = re.search(r"@([\w.\-]+)", addr_lower)
    if match:
        domain = match.group(1)
        for blocked in blocklist.get("domains", []):
            blocked_lower = blocked.lower()
            if domain == blocked_lower or domain.endswith("." + blocked_lower):
                return True
    return False


def _score_themes(text: str, themes: dict) -> tuple:
    """Return (theme_name, theme_day_int) for the best match, or (None, None)."""
    if not text.strip() or not themes:
        return None, None
    text_lower = text.lower()
    scores = {
        int(day): sum(1 for kw in theme.get("keywords", []) if kw.lower() in text_lower)
        for day, theme in themes.items()
    }
    best_day = max(scores, key=scores.get)
    if scores[best_day] == 0:
        return None, None
    return themes[str(best_day)]["name"], best_day


# ---------------------------------------------------------------------------
# Email parsing helpers
# ---------------------------------------------------------------------------

def _decode_header_value(value: str) -> str:
    if not value:
        return ""
    parts = email.header.decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def _get_plain_and_html(msg) -> tuple:
    plain_parts, html_parts = [], []
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if "attachment" in part.get("Content-Disposition", ""):
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                text = payload.decode(charset, errors="replace")
            except Exception:
                continue
            if ct == "text/plain":
                plain_parts.append(text)
            elif ct == "text/html":
                html_parts.append(text)
    else:
        charset = msg.get_content_charset() or "utf-8"
        try:
            payload = msg.get_payload(decode=True)
            text = payload.decode(charset, errors="replace") if payload else ""
        except Exception:
            text = ""
        if msg.get_content_type() == "text/html":
            html_parts.append(text)
        else:
            plain_parts.append(text)
    return "\n".join(plain_parts), "\n".join(html_parts)


# ---------------------------------------------------------------------------
# Gmail API auth
# ---------------------------------------------------------------------------

def _build_gmail_service():
    """Build an authenticated Gmail API service using OAuth2 refresh token."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        print(
            "❌ Gmail API libraries not installed.\n"
            "   Run: pip install google-auth google-auth-oauthlib google-api-python-client",
            file=sys.stderr,
        )
        sys.exit(1)

    client_id = os.environ.get("GMAIL_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN", "").strip()

    if not all([client_id, client_secret, refresh_token]):
        print(
            "❌ GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, and GMAIL_REFRESH_TOKEN must all be set.\n"
            "   Run 'python email_ingest.py --auth' once locally to obtain a refresh token.",
            file=sys.stderr,
        )
        sys.exit(1)

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri=TOKEN_URI,
        scopes=GMAIL_SCOPES,
    )
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def run_auth_flow() -> None:
    """One-time interactive OAuth flow to obtain a refresh token.

    Run this locally once:
        python email_ingest.py --auth

    You'll be prompted to authorize access in a browser.  The resulting
    refresh token is printed — store it as the GMAIL_REFRESH_TOKEN secret.
    """
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print(
            "❌ Run: pip install google-auth-oauthlib google-api-python-client",
            file=sys.stderr,
        )
        sys.exit(1)

    client_id = os.environ.get("GMAIL_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        print("❌ Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET before running --auth.", file=sys.stderr)
        sys.exit(1)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": TOKEN_URI,
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, GMAIL_SCOPES)
    creds = flow.run_local_server(port=0)
    print("\n✅ Authorization successful!")
    print(f"\nGMAIL_REFRESH_TOKEN={creds.refresh_token}")
    print("\nStore this value as a GitHub Secret named GMAIL_REFRESH_TOKEN.")


# ---------------------------------------------------------------------------
# Queue I/O
# ---------------------------------------------------------------------------

def _load_queue() -> dict:
    if QUEUE_FILE.exists():
        try:
            with open(QUEUE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"version": 1, "items": []}


def _save_queue(data: dict) -> None:
    QUEUE_FILE.parent.mkdir(exist_ok=True)
    with open(QUEUE_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Main ingestion
# ---------------------------------------------------------------------------

def ingest(dry_run: bool = False) -> int:
    """Fetch unread labelled emails and append them to the queue.

    Returns the count of new items added.
    """
    label = os.environ.get("GMAIL_LABEL", "INBOX").strip()
    themes = _load_themes()
    sender_blocklist = _load_email_sender_blocklist()
    subject_blocklist = _load_subject_blocklist()

    print(f"📧 Connecting to Gmail API (label: {label!r})...")
    service = _build_gmail_service()

    queue = _load_queue()
    seen_message_ids = {
        item["message_id"]
        for item in queue.get("items", [])
        if item.get("message_id")
    }

    # Build Gmail search query: all messages in the target label.
    # Deduplication is handled via seen_message_ids; dropping is:unread ensures
    # messages manually marked read in Gmail are still picked up.
    query = f"label:{label}" if label.upper() != "INBOX" else "in:inbox"

    try:
        result = service.users().messages().list(
            userId="me", q=query, maxResults=50
        ).execute()
    except Exception as e:
        print(f"❌ Gmail API list failed: {e}", file=sys.stderr)
        sys.exit(1)

    messages = result.get("messages", [])
    if not messages:
        print("  No new messages.")
        return 0

    print(f"  Found {len(messages)} unread message(s).")
    added = 0

    for msg_stub in messages:
        msg_id = msg_stub["id"]

        try:
            raw_msg = service.users().messages().get(
                userId="me", id=msg_id, format="raw"
            ).execute()
        except Exception as e:
            print(f"  ⚠️  Could not fetch message {msg_id}: {e}")
            continue

        raw_bytes = base64.urlsafe_b64decode(raw_msg["raw"] + "==")
        try:
            msg = email.message_from_bytes(raw_bytes)
        except Exception as e:
            print(f"  ⚠️  Could not parse message {msg_id}: {e}")
            continue

        message_id_header = (msg.get("Message-ID") or msg_id).strip()

        if message_id_header in seen_message_ids:
            print(f"  ⏭  Duplicate, skipping: {message_id_header[:60]}")
            if not dry_run:
                _mark_read(service, msg_id)
            continue

        # Classify: newsletter = has List-Unsubscribe header
        is_newsletter = bool(msg.get("List-Unsubscribe"))
        item_type = "newsletter" if is_newsletter else "feedback"

        subject = _decode_header_value(msg.get("Subject", ""))
        from_address = _decode_header_value(msg.get("From", ""))

        if _is_blocked_sender(from_address, sender_blocklist):
            print(f"  ⏭  Blocked sender, skipping: \"{from_address[:60]}\"")
            if not dry_run:
                _mark_read(service, msg_id)
            continue

        if _is_blocked_subject(subject, subject_blocklist):
            print(f"  ⏭  Blocked subject, skipping: \"{subject[:60]}\"")
            if not dry_run:
                _mark_read(service, msg_id)
            continue

        date_str = msg.get("Date", "")
        try:
            received_at = email.utils.parsedate_to_datetime(date_str).isoformat()
        except Exception:
            received_at = datetime.now(timezone.utc).isoformat()

        plain_body, html_body = _get_plain_and_html(msg)
        extracted_urls = _extract_urls(plain_body, html_body)

        max_chars = NEWSLETTER_MAX_CHARS if is_newsletter else FEEDBACK_MAX_CHARS
        raw_text = plain_body if plain_body.strip() else _strip_html(html_body)
        body_text = _sanitize(raw_text, max_chars)

        theme_tag, theme_day = _score_themes(f"{from_address} {subject} {body_text}", themes)

        if theme_tag is None:
            print(f"  ⏭  No theme match, skipping: \"{subject[:60]}\"")
            if not dry_run:
                _mark_read(service, msg_id)
            continue

        item = {
            "id": uuid.uuid4().hex[:8],
            "type": item_type,
            "message_id": message_id_header,
            "from_address": _mask_email(from_address),  # masked — never store full address
            "subject": subject[:200],
            "received_at": received_at,
            "body_text": body_text,
            "extracted_urls": extracted_urls,
            "theme_tag": theme_tag,
            "theme_day": theme_day,
            "status": "pending",
            "used_at": None,
        }

        queue["items"].append(item)
        seen_message_ids.add(message_id_header)
        added += 1

        theme_label = theme_tag or "no strong theme match (will not be used)"
        print(f"  ✅ [{item_type}] \"{subject[:60]}\" → {theme_label} ({len(extracted_urls)} URL(s))")

        if not dry_run:
            _mark_read(service, msg_id)

    if added and not dry_run:
        _save_queue(queue)
        print(f"\n📧 Added {added} new item(s) to email queue.")
    elif added and dry_run:
        print(f"\n📧 [DRY RUN] Would have added {added} item(s) — queue not written.")
    else:
        print("  No new items added.")

    return added


def _mark_read(service, gmail_msg_id: str) -> None:
    """Remove the UNREAD label so we don't re-fetch on the next run."""
    try:
        service.users().messages().modify(
            userId="me",
            id=gmail_msg_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
    except Exception as e:
        print(f"  ⚠️  Could not mark message {gmail_msg_id} as read: {e}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Ingest unread Gmail messages into podcasts/email_queue.json"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print items but do not mark emails as read or write the queue",
    )
    parser.add_argument(
        "--auth",
        action="store_true",
        help="Run one-time OAuth flow to obtain GMAIL_REFRESH_TOKEN (local use only)",
    )
    args = parser.parse_args()

    if args.auth:
        run_auth_flow()
    else:
        ingest(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
