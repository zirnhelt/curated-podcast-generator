#!/usr/bin/env python3
"""
Email ingest for the Cariboo Signals podcast generator.

Connects to an IMAP mailbox, fetches unseen messages, classifies them as
newsletter or listener feedback, sanitizes body text against prompt injection,
auto-assigns a podcast theme via keyword scoring, and appends items to
podcasts/email_queue.json for pickup by the daily generation run.

Usage:
  python email_ingest.py

Required environment variables:
  EMAIL_HOST   IMAP hostname (e.g. imap.gmail.com)
  EMAIL_USER   Email address
  EMAIL_PASS   Password or app-specific password

Optional:
  EMAIL_PORT   IMAP port (default: 993)
  EMAIL_FOLDER Folder/label to poll (default: INBOX)

Security note:
  All body text is sanitized (HTML stripped, prompt-injection chars removed,
  truncated) before being stored.  Newsletter body text is NOT passed to Claude;
  only fetched URL metadata is used.  Feedback body text is wrapped in explicit
  untrusted-content delimiters when injected into Claude prompts.
"""

import email
import email.header
import imaplib
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

# Feedback bodies are truncated to this many chars before storage/prompt injection
FEEDBACK_MAX_CHARS = 500
# Newsletter preview is only used for theme scoring, kept short
NEWSLETTER_MAX_CHARS = 200
# URLs below this length after validation are skipped
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
        # Fallback: remove tags with regex
        return re.sub(r"<[^>]+>", " ", html)


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------

# Characters / sequences that could be used for prompt injection
_INJECTION_PATTERN = re.compile(
    r"<[^>]*>"           # any remaining HTML/XML tags
    r"|{{.*?}}"          # Jinja/template delimiters
    r"|\[\[.*?\]\]"      # wiki-style brackets
    r"|\{%.*?%\}"        # template tags
    r"|`{3,}"            # triple backtick fences
    r"|\bsystem\b\s*:"   # "system:" role markers
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
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


# ---------------------------------------------------------------------------
# URL extraction and validation
# ---------------------------------------------------------------------------

_URL_PATTERN = re.compile(r"https?://[^\s<>\"')\]]+")

_BLOCKED_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "169.254.169.254",  # AWS metadata endpoint
}


def _is_safe_url(url: str) -> bool:
    """Return True only for safe, public http(s) URLs."""
    try:
        parts = urlparse(url)
    except Exception:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = parts.netloc.split(":")[0].lower()
    if not host or host in _BLOCKED_HOSTS:
        return False
    # Block private/internal IP ranges
    ip_match = re.match(r"^(\d+)\.(\d+)\.", host)
    if ip_match:
        a, b = int(ip_match.group(1)), int(ip_match.group(2))
        if a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168):
            return False
    if len(url) < URL_MIN_LEN:
        return False
    return True


def _extract_urls(plain: str, html: str) -> list:
    """Extract, validate, and deduplicate URLs from plain text and raw HTML."""
    raw = _URL_PATTERN.findall(plain + " " + html)
    seen = set()
    result = []
    for url in raw:
        # Strip trailing punctuation that often attaches in email text
        url = url.rstrip(".,;:!?\"'")
        if url in seen:
            continue
        seen.add(url)
        if _is_safe_url(url):
            result.append(url)
        if len(result) >= 10:
            break
    return result


# ---------------------------------------------------------------------------
# Theme scoring (mirrors podcast_generator._score_text_against_themes)
# ---------------------------------------------------------------------------

def _load_themes() -> dict:
    try:
        with open(THEMES_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"  ⚠️  Could not load themes config: {e}", file=sys.stderr)
        return {}


def _score_themes(text: str, themes: dict) -> tuple:
    """Return (theme_name, theme_day_int) for the best-matching theme, or (None, None)."""
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
    """Decode RFC 2047-encoded email header value to a plain string."""
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
    """Walk a parsed email message and return (plain_text, html_text)."""
    plain_parts = []
    html_parts = []
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = part.get("Content-Disposition", "")
            if "attachment" in cd:
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

def ingest() -> int:
    """Fetch unseen emails and append them to the queue. Returns count added."""
    host = os.environ.get("EMAIL_HOST", "").strip()
    user = os.environ.get("EMAIL_USER", "").strip()
    password = os.environ.get("EMAIL_PASS", "").strip()
    port = int(os.environ.get("EMAIL_PORT", "993"))
    folder = os.environ.get("EMAIL_FOLDER", "INBOX")

    if not host or not user or not password:
        print("❌ EMAIL_HOST, EMAIL_USER, and EMAIL_PASS must all be set.", file=sys.stderr)
        sys.exit(1)

    queue = _load_queue()
    seen_message_ids = {item["message_id"] for item in queue.get("items", []) if item.get("message_id")}
    themes = _load_themes()

    print(f"📧 Connecting to {host}:{port} as {user}...")
    try:
        conn = imaplib.IMAP4_SSL(host, port)
        conn.login(user, password)
    except Exception as e:
        print(f"❌ IMAP connection failed: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        conn.select(folder)
        _, data = conn.search(None, "UNSEEN")
        msg_ids = data[0].split() if data and data[0] else []
    except Exception as e:
        print(f"❌ Could not search mailbox: {e}", file=sys.stderr)
        conn.logout()
        sys.exit(1)

    if not msg_ids:
        print("  No new messages.")
        conn.logout()
        return 0

    print(f"  Found {len(msg_ids)} unseen message(s).")
    added = 0

    for imap_id in msg_ids:
        try:
            _, raw = conn.fetch(imap_id, "(RFC822)")
        except Exception as e:
            print(f"  ⚠️  Could not fetch message {imap_id}: {e}")
            continue

        raw_bytes = raw[0][1] if raw and raw[0] else b""
        try:
            msg = email.message_from_bytes(raw_bytes)
        except Exception as e:
            print(f"  ⚠️  Could not parse message {imap_id}: {e}")
            continue

        message_id = (msg.get("Message-ID") or "").strip()
        if message_id and message_id in seen_message_ids:
            print(f"  ⏭  Duplicate message-id, skipping: {message_id[:60]}")
            # Still mark SEEN so we don't re-fetch
            conn.store(imap_id, "+FLAGS", "\\Seen")
            continue

        # Classify: newsletter has a List-Unsubscribe header
        is_newsletter = bool(msg.get("List-Unsubscribe"))
        item_type = "newsletter" if is_newsletter else "feedback"

        subject = _decode_header_value(msg.get("Subject", ""))
        from_address = _decode_header_value(msg.get("From", ""))
        date_str = msg.get("Date", "")
        try:
            received_at = email.utils.parsedate_to_datetime(date_str).isoformat() if date_str else datetime.now(timezone.utc).isoformat()
        except Exception:
            received_at = datetime.now(timezone.utc).isoformat()

        plain_body, html_body = _get_plain_and_html(msg)

        # Extract URLs before sanitization (raw content has cleaner URLs)
        extracted_urls = _extract_urls(plain_body, html_body)

        # Sanitize body text — different limits for each type
        max_chars = NEWSLETTER_MAX_CHARS if is_newsletter else FEEDBACK_MAX_CHARS
        raw_text = plain_body if plain_body.strip() else _strip_html(html_body)
        body_text = _sanitize(raw_text, max_chars)

        # Auto-assign theme by scoring subject + body against theme keywords
        score_text = f"{subject} {body_text}"
        theme_tag, theme_day = _score_themes(score_text, themes)

        item = {
            "id": uuid.uuid4().hex[:8],
            "type": item_type,
            "message_id": message_id,
            "from_address": from_address,
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
        if message_id:
            seen_message_ids.add(message_id)
        added += 1

        theme_label = theme_tag or "no strong theme match (will not be used)"
        print(f"  ✅ [{item_type}] \"{subject[:60]}\" → {theme_label} ({len(extracted_urls)} URL(s))")

        # Mark as SEEN in IMAP so we don't re-fetch on next run
        conn.store(imap_id, "+FLAGS", "\\Seen")

    conn.logout()

    if added:
        _save_queue(queue)
        print(f"\n📧 Added {added} new item(s) to email queue.")
    else:
        print("  No new items added.")

    return added


if __name__ == "__main__":
    ingest()
