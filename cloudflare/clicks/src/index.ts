/**
 * Click capture for super-rss-feed's "Also in the news" digest.
 *
 * The digest lists the widely covered stories no feed carried. The reader's clicks
 * on it are the evidence for whether the interest ranking misses stories they want,
 * so the nightly signs each digest link to point here:
 *
 *   GET /go?u=<article>&c=<Kagi category>&d=<digest date>&s=<signature>
 *     checks the signature, records {u, c, d} and the time in KV (no IP, no device,
 *     no headers), and redirects to the article. A link this Worker did not sign is
 *     refused, so it is never an open redirect.
 *
 *   GET /clicks?since=<ISO time>   (Authorization: Bearer <CLICKS_KEY>)
 *     returns the clicks after `since`, oldest first, for the nightly to store
 *     beside the ratings.
 *
 * It is a Worker of its own, apart from the scheduler, which is cron-only and has
 * no public URL on purpose: nothing here can touch the crons that start the show.
 */

export interface Env {
  /** KV namespace for the clicks; its id comes from the CLICKS_KV_ID repo variable at deploy. */
  CLICKS: KVNamespace;
  /** Shared with super-rss-feed's nightly: signs the links and authorises reads. */
  CLICKS_KEY: string;
}

/** Clicks are kept 60 days; the nightly copies them out daily. */
const KEEP_SECONDS = 60 * 60 * 24 * 60;
/** KV metadata is capped at 1024 bytes, and the article URL rides in it. */
const MAX_URL_CHARS = 900;
/** Rows one read returns; the nightly asks again from the last one it got. */
const MAX_READ = 1000;
const PREFIX = 'click/';

/** HMAC-SHA256 of url, category and date, hex, first 32 characters. super-rss-feed signs the same way. */
export async function sign(key: string, url: string, category: string, day: string): Promise<string> {
  const enc = new TextEncoder();
  const k = await crypto.subtle.importKey('raw', enc.encode(key), { name: 'HMAC', hash: 'SHA-256' },
                                          false, ['sign']);
  const mac = await crypto.subtle.sign('HMAC', k, enc.encode(`${url}\n${category}\n${day}`));
  return [...new Uint8Array(mac)].map((b) => b.toString(16).padStart(2, '0')).join('').slice(0, 32);
}

/** Compare without leaking how much of a guess was right. */
function sameText(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/** A browser or reader app fetching ahead of a click is not a click. */
function isPrefetch(req: Request): boolean {
  const purpose = (req.headers.get('Sec-Purpose') || req.headers.get('Purpose') || '').toLowerCase();
  return req.method === 'HEAD' || purpose.includes('prefetch') || purpose.includes('prerender');
}

async function go(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  const q = new URL(req.url).searchParams;
  const u = q.get('u') || '';
  const c = (q.get('c') || '').slice(0, 80);
  const d = q.get('d') || '';
  const s = q.get('s') || '';
  const wellFormed = /^https:\/\//i.test(u) && u.length <= MAX_URL_CHARS && /^\d{4}-\d{2}-\d{2}$/.test(d);
  if (!wellFormed || !env.CLICKS_KEY || !sameText(s, await sign(env.CLICKS_KEY, u, c, d))) {
    return new Response('Not a link this site made.', { status: 400 });
  }
  if (!isPrefetch(req)) {
    const at = new Date().toISOString();
    ctx.waitUntil(env.CLICKS.put(`${PREFIX}${at}/${crypto.randomUUID().slice(0, 8)}`, '',
                                 { expirationTtl: KEEP_SECONDS, metadata: { u, c, d } }));
  }
  return new Response(null, {
    status: 302,
    headers: { Location: u, 'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer' },
  });
}

interface Click { at: string; u: string; c: string; d: string }

async function clicks(req: Request, env: Env): Promise<Response> {
  if (!env.CLICKS_KEY || !sameText(req.headers.get('Authorization') || '', `Bearer ${env.CLICKS_KEY}`)) {
    return new Response('', { status: 401 });
  }
  const since = new URL(req.url).searchParams.get('since') || '';
  const rows: Click[] = [];
  let cursor: string | undefined;
  let more = false;
  // Keys sort by their ISO time, so this reads oldest first.
  do {
    const page = await env.CLICKS.list<{ u: string; c: string; d: string }>({ prefix: PREFIX, cursor });
    for (const key of page.keys) {
      const at = key.name.slice(PREFIX.length).split('/')[0];
      if (at <= since || !key.metadata) continue;
      if (rows.length >= MAX_READ) { more = true; break; }
      rows.push({ at, ...key.metadata });
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor && !more);
  return Response.json({ clicks: rows, more }, { headers: { 'Cache-Control': 'no-store' } });
}

export default {
  async fetch(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const path = new URL(req.url).pathname;
    if (path === '/go' && (req.method === 'GET' || req.method === 'HEAD')) return go(req, env, ctx);
    if (path === '/clicks' && req.method === 'GET') return clicks(req, env);
    return new Response('Not found', { status: 404 });
  },
} satisfies ExportedHandler<Env>;
