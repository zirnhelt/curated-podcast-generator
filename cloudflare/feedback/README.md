# Cariboo Signals feedback writer

A Cloudflare Worker that saves `review.html` ratings into
`super-rss-feed/feedback/<date>.json`, so the page itself carries no GitHub
token.

## Why this exists

`review.html` is served from super-rss-feed's public GitHub Pages site. From
2026-06-17 it carried a fine-grained PAT (Contents read/write on super-rss-feed)
in its own source, reversed to get past secret scanning. Anyone viewing the
source could have pushed code that the nightly feed run executes with its API
keys. The token was revoked on 2026-09-23; no commit made with it was anything
other than ratings and reader state.

The rule this replaces it with: **a credential never ships in a page**. The page
sends ratings plus a passphrase; this Worker holds the token and decides where
the ratings go.

## What it does and does not do

- `POST /feedback` with `{date, entries}` and an `X-Review-Key` header. It merges
  entries by `url` into that day's file and commits
  `Add review feedback <date> [skip ci]`, the message the page always used.
- The caller never names a repo, path or commit message, and the date must be
  within two days of now. **A leaked passphrase buys junk ratings, not a code
  push.**
- Requests from any browser origin other than `https://zirnhelt.github.io` are
  refused. Requests with no `Origin` header (curl) still need the passphrase.
- An existing day file it cannot read or parse is **refused, never
  overwritten**. The in-page writer fell back to an empty list and overwrote it.
- UTF-8 is decoded properly. The in-page writer used bare `atob()`, so a second
  save on the same day turned earlier titles into mojibake.

It is separate from `cloudflare/scheduler/` on purpose: a bug here must not be
able to touch the triggers that start the nightly runs.

## Credentials

> **`FEEDBACK_GITHUB_TOKEN`** — issued _(fill in)_, expires _(fill in)_.

Record the dates whenever the token is issued or rotated. An expired token shows
up on the review page as `GitHub read failed (HTTP 401)` when you save, so it
is loud. But nothing warns ahead of time.

## One-time setup

1. **Fine-grained GitHub PAT.** Repository access: `zirnhelt/super-rss-feed`
   *only*. Permissions: `Contents: Read and write`, nothing else. Pick an
   expiry and record it above.
2. **Passphrase.** Anything you can type on your phone once; four random words
   is plenty. It only gates writes to the ratings files.
3. Add two repository **secrets** on `curated-podcast-generator`:
   `FEEDBACK_GITHUB_TOKEN` and `FEEDBACK_REVIEW_KEY`. `CLOUDFLARE_API_TOKEN` and
   `CF_ACCOUNT_ID` already exist from the scheduler.
4. **Actions → Deploy Feedback Worker → Run workflow** with
   `push_secrets: true`. The job summary prints the endpoint and a three-probe
   smoke test (preflight 204, no passphrase 401, passphrase with empty body
   400). None of the probes writes anything.
5. In **super-rss-feed**, add the repository **variable** (not a secret — it is
   a public URL) `FEEDBACK_ENDPOINT` = the endpoint from step 4. Then run
   **Deploy Static Files** there, or wait for the nightly run.
6. Open the review page, rate something and save. It asks for the passphrase
   once per device and remembers it.

If step 4 fails with no `workers.dev` URL, the Cloudflare account has no
workers.dev subdomain yet. Register one under **Workers & Pages** and re-run.

## Rotating

- **Token:** issue a new one, update the secret, run the deploy with
  `push_secrets: true`, then revoke the old one.
- **Passphrase:** same, then clear it on each device. The page asks again after
  a 401.

## Rolling back

Delete the Worker in the Cloudflare dashboard and revoke the token. The review
page then shows a save error. **Do not** go back to baking a token into the
page.

## Local development

```bash
cd cloudflare/feedback
npm ci
npm run typecheck
npm test        # Node's built-in runner against a fake GitHub; no network
```
