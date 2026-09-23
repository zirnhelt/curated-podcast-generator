// Run with `npm test` (Node's built-in runner; Node strips the types itself).
import { test } from 'node:test';
import assert from 'node:assert/strict';

import worker, {
  HttpError,
  decodeBase64Utf8,
  encodeBase64Utf8,
  keyMatches,
  mergeRatings,
  parseRequest,
  writeFeedback,
} from '../src/index.ts';

const NOW = new Date('2026-09-23T20:00:00Z');
const ENV = { GITHUB_TOKEN: 'gh-token', REVIEW_KEY: 'correct horse' };
const ORIGIN = 'https://zirnhelt.github.io';

const rating = (url: string, extra: Record<string, unknown> = {}) => ({ url, rating: 'good', ...extra });
const body = (entries: unknown[], date = '2026-09-23') => JSON.stringify({ date, entries });

function rejects(fn: () => unknown, status: number) {
  assert.throws(fn, (e: unknown) => e instanceof HttpError && e.status === status);
}

// ── parseRequest ─────────────────────────────────────────────────────────────

test('accepts a well-formed request', () => {
  const parsed = parseRequest(body([rating('https://a.example/1')]), NOW);
  assert.equal(parsed.date, '2026-09-23');
  assert.equal(parsed.entries.length, 1);
});

test('accepts the Pacific date a day behind UTC', () => {
  parseRequest(body([rating('https://a.example/1')], '2026-09-22'), NOW);
});

test('refuses malformed, impossible and far-off dates', () => {
  rejects(() => parseRequest(body([rating('https://a.example/1')], '23-09-2026'), NOW), 400);
  rejects(() => parseRequest(body([rating('https://a.example/1')], '2026-02-30'), NOW), 400);
  rejects(() => parseRequest(body([rating('https://a.example/1')], '2026-06-01'), NOW), 400);
});

test('refuses bad entries', () => {
  rejects(() => parseRequest('not json', NOW), 400);
  rejects(() => parseRequest(body([]), NOW), 400);
  rejects(() => parseRequest(body(['https://a.example/1']), NOW), 400);
  rejects(() => parseRequest(body([rating('javascript:alert(1)')]), NOW), 400);
  rejects(() => parseRequest(body([{ url: 'https://a.example/1', rating: 'meh' }]), NOW), 400);
  rejects(() => parseRequest(body([rating('https://a.example/1', { note: 'x'.repeat(9000) })]), NOW), 413);
  const many = Array.from({ length: 201 }, (_, i) => rating(`https://a.example/${i}`));
  rejects(() => parseRequest(body(many), NOW), 413);
});

// ── mergeRatings + encoding ──────────────────────────────────────────────────

test('non-ASCII survives the base64 round trip', () => {
  const text = 'Secwépemc — Tŝilhqot’in 988’s 🏔️';
  assert.equal(decodeBase64Utf8(encodeBase64Utf8(text)), text);
});

test('merges by url, replacing in place and keeping url-less entries', () => {
  const existing = {
    date: '2026-09-23',
    ratings: [rating('https://a.example/1'), { rating: 'skip' }, rating('https://a.example/2')],
  };
  const request = parseRequest(body([rating('https://a.example/1', { rating: 'bad' }), rating('https://a.example/3')]), NOW);
  const { content, total } = mergeRatings(existing, request, NOW);
  const out = JSON.parse(content);
  assert.equal(total, 4);
  assert.deepEqual(out.ratings.map((r: { url?: string; rating: string }) => [r.url, r.rating]), [
    ['https://a.example/1', 'bad'],
    [undefined, 'skip'],
    ['https://a.example/2', 'good'],
    ['https://a.example/3', 'good'],
  ]);
  assert.equal(out.submitted_at, NOW.toISOString());
  assert.equal(content, JSON.stringify(out, null, 2));
});

test('refuses to overwrite a file with no ratings list', () => {
  const request = parseRequest(body([rating('https://a.example/1')]), NOW);
  rejects(() => mergeRatings({ something: 'else' }, request, NOW), 502);
});

// ── writeFeedback against a fake GitHub ──────────────────────────────────────

type Call = { url: string; method: string; body?: Record<string, unknown> };

function fakeGitHub(responses: Response[]) {
  const calls: Call[] = [];
  const impl = async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const next = responses.shift();
    if (!next) throw new Error('unexpected request');
    return next;
  };
  return { calls, impl };
}

const json = (status: number, value: unknown) => new Response(JSON.stringify(value), { status });
const fileResponse = (value: unknown, sha = 'sha-1') =>
  json(200, { sha, encoding: 'base64', content: encodeBase64Utf8(JSON.stringify(value, null, 2)) });

test('creates the day file when none exists, at a path the caller cannot choose', async () => {
  const gh = fakeGitHub([json(404, {}), json(201, {})]);
  const request = parseRequest(body([rating('https://a.example/1')]), NOW);
  assert.equal(await writeFeedback(ENV, request, NOW, gh.impl), 1);
  assert.equal(gh.calls.length, 2);
  assert.match(gh.calls[1]!.url, /\/repos\/zirnhelt\/super-rss-feed\/contents\/feedback\/2026-09-23\.json$/);
  assert.equal(gh.calls[1]!.method, 'PUT');
  assert.equal(gh.calls[1]!.body!.sha, undefined);
  assert.equal(gh.calls[1]!.body!.message, 'Add review feedback 2026-09-23 [skip ci]');
});

test('merges into an existing file without mangling its non-ASCII titles', async () => {
  const existing = { date: '2026-09-23', ratings: [rating('https://a.example/1', { title: '988’s hotline — Secwépemc' })] };
  const gh = fakeGitHub([fileResponse(existing), json(200, {})]);
  const request = parseRequest(body([rating('https://a.example/2')]), NOW);
  assert.equal(await writeFeedback(ENV, request, NOW, gh.impl), 2);
  const put = gh.calls[1]!.body!;
  assert.equal(put.sha, 'sha-1');
  const written = JSON.parse(decodeBase64Utf8(put.content as string));
  assert.equal(written.ratings[0].title, '988’s hotline — Secwépemc');
});

test('re-reads and retries when another save lands first', async () => {
  const gh = fakeGitHub([
    fileResponse({ date: '2026-09-23', ratings: [] }, 'sha-1'),
    json(409, {}),
    fileResponse({ date: '2026-09-23', ratings: [rating('https://a.example/9')] }, 'sha-2'),
    json(200, {}),
  ]);
  const request = parseRequest(body([rating('https://a.example/1')]), NOW);
  assert.equal(await writeFeedback(ENV, request, NOW, gh.impl), 2);
  assert.equal(gh.calls[3]!.body!.sha, 'sha-2');
});

test('never writes when the existing file cannot be read', async () => {
  const request = parseRequest(body([rating('https://a.example/1')]), NOW);
  for (const response of [
    json(200, { sha: 'sha-1', encoding: 'none', content: '' }), // > 1 MB file
    json(200, { sha: 'sha-1', encoding: 'base64', content: encodeBase64Utf8('{not json') }),
    json(500, {}),
  ]) {
    const gh = fakeGitHub([response]);
    await assert.rejects(writeFeedback(ENV, request, NOW, gh.impl), (e: unknown) => e instanceof HttpError && e.status === 502);
    assert.equal(gh.calls.filter((c) => c.method === 'PUT').length, 0);
  }
});

// ── the fetch handler ────────────────────────────────────────────────────────

test('passphrase comparison', async () => {
  assert.equal(await keyMatches('correct horse', 'correct horse'), true);
  assert.equal(await keyMatches('correct horsf', 'correct horse'), false);
  assert.equal(await keyMatches(null, 'correct horse'), false);
  assert.equal(await keyMatches('anything', ''), false);
});

function post(key: string | null, payload: string, origin: string | null = ORIGIN) {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (key !== null) headers['X-Review-Key'] = key;
  if (origin !== null) headers.Origin = origin;
  return new Request('https://cariboo-feedback.example.workers.dev/feedback', { method: 'POST', headers, body: payload });
}

test('handler: preflight, origin, path, method and configuration gates', async () => {
  const preflight = await worker.fetch(
    new Request('https://x.workers.dev/feedback', { method: 'OPTIONS', headers: { Origin: ORIGIN } }),
    ENV,
  );
  assert.equal(preflight.status, 204);
  assert.equal(preflight.headers.get('Access-Control-Allow-Origin'), ORIGIN);
  assert.match(preflight.headers.get('Access-Control-Allow-Headers') ?? '', /X-Review-Key/);

  assert.equal((await worker.fetch(post('correct horse', '{}', 'https://evil.example'), ENV)).status, 403);
  assert.equal((await worker.fetch(new Request('https://x.workers.dev/other', { method: 'POST' }), ENV)).status, 404);
  assert.equal((await worker.fetch(new Request('https://x.workers.dev/feedback'), ENV)).status, 405);
  assert.equal((await worker.fetch(post('correct horse', '{}'), { GITHUB_TOKEN: 't', REVIEW_KEY: '' })).status, 500);
});

test('handler: a wrong passphrase is refused before GitHub is touched, readably by the page', async () => {
  const original = globalThis.fetch;
  let touched = false;
  globalThis.fetch = (async () => { touched = true; return json(500, {}); }) as typeof fetch;
  try {
    const res = await worker.fetch(post('wrong', body([rating('https://a.example/1')])), ENV);
    assert.equal(res.status, 401);
    assert.equal(res.headers.get('Access-Control-Allow-Origin'), ORIGIN);
    assert.equal(touched, false);
  } finally {
    globalThis.fetch = original;
  }
});

test('handler: a valid save reaches GitHub and reports counts', async () => {
  const original = globalThis.fetch;
  const today = new Date().toLocaleDateString('en-CA', { timeZone: 'America/Vancouver' });
  const responses = [json(404, {}), json(201, {})];
  globalThis.fetch = (async () => responses.shift()!) as typeof fetch;
  try {
    const res = await worker.fetch(post('correct horse', body([rating('https://a.example/1')], today)), ENV);
    assert.equal(res.status, 200);
    assert.deepEqual(await res.json(), { saved: 1, total: 1 });
  } finally {
    globalThis.fetch = original;
  }
});
