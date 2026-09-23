/**
 * Cariboo Signals feedback writer.
 *
 * super-rss-feed's review.html is a public GitHub Pages page. From 2026-06-17 it
 * carried a GitHub token in its own source, reversed to slip past secret
 * scanning, and anyone who viewed the source could push code that the nightly
 * feed run would then execute with its API keys. The token was revoked on
 * 2026-09-23. This Worker holds the replacement, so nothing secret ships in the
 * page again.
 *
 * SCOPE: it merges ratings into `feedback/<date>.json` in super-rss-feed and
 * does nothing else. The caller never names a repo, a path or a commit message.
 * A leaked passphrase therefore buys junk ratings, not a code push.
 *
 * It also fixes two defects of the in-page writer it replaces:
 *  - The existing file was decoded with bare `atob()`, i.e. as Latin-1, so a
 *    second save on the same day re-encoded every earlier title as mojibake
 *    ("988âs LGBTQ+ hotline" in the September corpus).
 *  - A file that failed to parse fell back to an empty list and was then
 *    overwritten, silently dropping the day's earlier ratings. Here an
 *    unreadable file is refused, never overwritten.
 */

export interface Env {
  /**
   * Fine-grained GitHub PAT: zirnhelt/super-rss-feed only, `Contents: Read and
   * write`, nothing else. Pushed by `.github/workflows/deploy-feedback.yml`.
   * Its expiry is recorded in README.md; an expired token shows up as a
   * "GitHub read failed (HTTP 401)" on the review page, not as silence.
   */
  GITHUB_TOKEN: string;
  /** Passphrase the reader enters once per device. Pushed by the same workflow. */
  REVIEW_KEY: string;
}

const FEEDBACK_REPO = 'zirnhelt/super-rss-feed';
const ALLOWED_ORIGIN = 'https://zirnhelt.github.io';
const USER_AGENT = 'cariboo-feedback-worker';

const MAX_BODY_BYTES = 512 * 1024;
const MAX_ENTRIES = 200;
const MAX_ENTRY_BYTES = 8 * 1024;
const MAX_URL_LENGTH = 2048;
const RATINGS = new Set(['good', 'bad', 'interesting', 'exemplar', 'skip']);
/** The page keys each file by the Pacific calendar date. Two days of slack
 *  either side of UTC means a late-evening save is never refused, while a
 *  caller still cannot write into an arbitrary day's history. */
const DATE_SLACK_DAYS = 2;
/** A 409 means another save landed between our read and our write. */
const WRITE_ATTEMPTS = 3;

const CORS_HEADERS: Record<string, string> = {
  'Access-Control-Allow-Origin': ALLOWED_ORIGIN,
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, X-Review-Key',
  'Access-Control-Max-Age': '86400',
  Vary: 'Origin',
};

export class HttpError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type Entry = Record<string, unknown>;

export interface FeedbackRequest {
  date: string;
  entries: Entry[];
}

function isObject(value: unknown): value is Entry {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function byteLength(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** Validate the page's request. Everything the Worker writes is shaped here. */
export function parseRequest(raw: string, now: Date): FeedbackRequest {
  if (byteLength(raw) > MAX_BODY_BYTES) throw new HttpError(413, 'Request too large');

  let body: unknown;
  try {
    body = JSON.parse(raw);
  } catch {
    throw new HttpError(400, 'Body is not JSON');
  }
  if (!isObject(body)) throw new HttpError(400, 'Body must be a JSON object');

  const { date, entries } = body;
  if (typeof date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    throw new HttpError(400, 'date must be YYYY-MM-DD');
  }
  const day = new Date(`${date}T00:00:00Z`);
  // The round trip rejects dates like 2026-02-30 that Date would roll over.
  if (Number.isNaN(day.getTime()) || day.toISOString().slice(0, 10) !== date) {
    throw new HttpError(400, 'date is not a calendar date');
  }
  if (Math.abs(day.getTime() - now.getTime()) > DATE_SLACK_DAYS * 86_400_000) {
    throw new HttpError(400, 'date is not today');
  }

  if (!Array.isArray(entries) || entries.length === 0) {
    throw new HttpError(400, 'entries must be a non-empty array');
  }
  if (entries.length > MAX_ENTRIES) throw new HttpError(413, `At most ${MAX_ENTRIES} entries per save`);

  for (const entry of entries) {
    if (!isObject(entry)) throw new HttpError(400, 'Each entry must be an object');
    const { url, rating } = entry;
    if (typeof url !== 'string' || url.length > MAX_URL_LENGTH || !/^https?:\/\//i.test(url)) {
      throw new HttpError(400, 'Each entry needs an http(s) url');
    }
    if (typeof rating !== 'string' || !RATINGS.has(rating)) {
      throw new HttpError(400, `Unknown rating: ${String(rating).slice(0, 20)}`);
    }
    if (byteLength(JSON.stringify(entry)) > MAX_ENTRY_BYTES) {
      throw new HttpError(413, 'Entry too large');
    }
  }
  return { date, entries: entries as Entry[] };
}

/**
 * Merge new entries into the day's file by url, a later rating replacing an
 * earlier one in place, and serialize exactly as the page used to (two-space
 * JSON), so the downstream readers see no format change.
 *
 * `existing` is the parsed current file, or null when there is none yet.
 */
export function mergeRatings(existing: unknown, request: FeedbackRequest, now: Date): { content: string; total: number } {
  let prior: unknown[] = [];
  if (existing !== null) {
    if (!isObject(existing) || !Array.isArray(existing.ratings)) {
      throw new HttpError(502, 'Existing feedback file has no ratings list; refusing to overwrite it');
    }
    prior = existing.ratings;
  }

  const merged = new Map<string, unknown>();
  prior.forEach((rating, index) => {
    // Anything without a url is kept under its own key: never dropped.
    const key = isObject(rating) && typeof rating.url === 'string' ? rating.url : `\u0000${index}`;
    merged.set(key, rating);
  });
  for (const entry of request.entries) merged.set(entry.url as string, entry);

  const payload = {
    date: request.date,
    submitted_at: now.toISOString(),
    ratings: [...merged.values()],
  };
  return { content: JSON.stringify(payload, null, 2), total: merged.size };
}

export function encodeBase64Utf8(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

export function decodeBase64Utf8(b64: string): string {
  const binary = atob(b64.replace(/\s/g, ''));
  const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
  return new TextDecoder('utf-8', { fatal: true, ignoreBOM: false }).decode(bytes);
}

type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

/** Read-merge-write `feedback/<date>.json`, retrying when another save races us. */
export async function writeFeedback(
  env: Env,
  request: FeedbackRequest,
  now: Date,
  fetchImpl: Fetch = fetch,
): Promise<number> {
  const path = `feedback/${request.date}.json`;
  const url = `https://api.github.com/repos/${FEEDBACK_REPO}/contents/${path}`;
  const headers: Record<string, string> = {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': USER_AGENT,
  };

  for (let attempt = 1; attempt <= WRITE_ATTEMPTS; attempt++) {
    const current = await fetchImpl(url, { headers });
    let sha: string | undefined;
    let existing: unknown = null;

    if (current.status === 200) {
      const file = (await current.json()) as { sha?: string; content?: string; encoding?: string };
      // Files over 1 MB come back without inline content. Refuse rather than
      // treat that as empty, which is how the in-page writer lost ratings.
      if (!file.sha || file.encoding !== 'base64' || typeof file.content !== 'string') {
        throw new HttpError(502, 'Existing feedback file could not be read; refusing to overwrite it');
      }
      sha = file.sha;
      try {
        existing = JSON.parse(decodeBase64Utf8(file.content));
      } catch {
        throw new HttpError(502, 'Existing feedback file is not valid JSON; refusing to overwrite it');
      }
    } else if (current.status !== 404) {
      throw new HttpError(502, `GitHub read failed (HTTP ${current.status})`);
    }

    const { content, total } = mergeRatings(existing, request, now);
    const written = await fetchImpl(url, {
      method: 'PUT',
      headers: { ...headers, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: `Add review feedback ${request.date} [skip ci]`,
        content: encodeBase64Utf8(content),
        ...(sha ? { sha } : {}),
      }),
    });
    if (written.ok) return total;

    // 409: the sha moved under us. 422: the file appeared after we saw a 404.
    if ((written.status === 409 || written.status === 422) && attempt < WRITE_ATTEMPTS) continue;
    throw new HttpError(502, `GitHub write failed (HTTP ${written.status})`);
  }
  throw new HttpError(502, 'GitHub write kept conflicting');
}

/** Compare digests rather than strings, so the check takes the same time
 *  however many leading characters of a guess are right. */
export async function keyMatches(given: string | null, expected: string): Promise<boolean> {
  if (!given || !expected) return false;
  const encoder = new TextEncoder();
  const [a, b] = await Promise.all([
    crypto.subtle.digest('SHA-256', encoder.encode(given)),
    crypto.subtle.digest('SHA-256', encoder.encode(expected)),
  ]);
  const x = new Uint8Array(a);
  const y = new Uint8Array(b);
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= (x[i] as number) ^ (y[i] as number);
  return diff === 0;
}

function reply(status: number, body: unknown, cors: boolean): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...(cors ? CORS_HEADERS : {}) },
  });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    // A browser always sends Origin on a cross-origin POST; a missing one is a
    // non-browser client (the deploy smoke test), which the passphrase gates.
    const origin = request.headers.get('Origin');
    if (origin !== null && origin !== ALLOWED_ORIGIN) {
      return reply(403, { error: 'Origin not allowed' }, false);
    }
    const cors = origin !== null;

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }
    if (new URL(request.url).pathname !== '/feedback') {
      return reply(404, { error: 'Not found' }, cors);
    }
    if (request.method !== 'POST') return reply(405, { error: 'POST only' }, cors);

    // Never open by accident: a missing secret is a misconfiguration, not a
    // passphrase-free endpoint.
    if (!env.GITHUB_TOKEN || !env.REVIEW_KEY) {
      return reply(500, { error: 'Worker is not configured' }, cors);
    }
    if (!(await keyMatches(request.headers.get('X-Review-Key'), env.REVIEW_KEY))) {
      return reply(401, { error: 'Passphrase rejected' }, cors);
    }

    const declared = Number(request.headers.get('Content-Length') ?? '0');
    if (declared > MAX_BODY_BYTES) return reply(413, { error: 'Request too large' }, cors);

    try {
      const now = new Date();
      const parsed = parseRequest(await request.text(), now);
      const total = await writeFeedback(env, parsed, now);
      console.log(`saved ${parsed.entries.length} entries to feedback/${parsed.date}.json (${total} total)`);
      return reply(200, { saved: parsed.entries.length, total }, cors);
    } catch (error) {
      if (error instanceof HttpError) {
        console.warn(`refused: ${error.status} ${error.message}`);
        return reply(error.status, { error: error.message }, cors);
      }
      console.error('unexpected failure', error);
      return reply(500, { error: 'Unexpected error' }, cors);
    }
  },
} satisfies ExportedHandler<Env>;
