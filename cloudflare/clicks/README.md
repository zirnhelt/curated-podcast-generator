# Cariboo Signals clicks

A Cloudflare Worker that records which stories the reader opens in super-rss-feed's
"Also in the news" digest, then sends them on to the article.

The digest lists the widely covered stories no feed carried. Whether the reader opens
them is the evidence for the audit's `headline-misses` question: is the interest ranking
missing stories they want, and from which outlets and subjects? (Producer-approved
2026-11-20, from the Proposals tab.)

## What it records, and what it doesn't

`GET /go?u=<article>&c=<Kagi category>&d=<digest date>&s=<signature>` stores the article
URL, the category, the digest date and the time. No IP address, device, cookie or header.
Entries expire after 60 days; the nightly copies them into super-rss-feed's
`feedback/clicks/` long before that.

A link is followed only when its signature checks out (HMAC-SHA256 with `CLICKS_KEY`, the
same key super-rss-feed signs with), so the Worker is never an open redirect. A `HEAD`
request or a browser prefetch (`Sec-Purpose: prefetch`) is redirected but not counted.

`GET /clicks?since=<ISO time>` with `Authorization: Bearer <CLICKS_KEY>` returns the
clicks after that time, oldest first, at most 1000 at a time (`more: true` says ask again).

## Why it is not part of the scheduler

The scheduler is cron-only with no public URL, on purpose: it starts the show every night.
This Worker needs a public URL. Keeping them apart means a deploy or a bug here cannot touch
the crons. It has no triggers of its own (the free plan's five crons are all the scheduler's).

## Setting it up (once)

1. **KV namespace.** Cloudflare dashboard → Storage & Databases → KV → Create, name it
   `cariboo-signals-clicks`. Copy its id into this repo's **variable** `CLICKS_KV_ID`
   (Settings → Secrets and variables → Actions → Variables).
2. **Key.** Generate 32+ random characters (`openssl rand -hex 32`). Save the same value as
   the **secret** `CLICKS_KEY` in this repo and in super-rss-feed. Both sides strip whitespace,
   so a newline picked up in a phone paste does no harm; letters and digits only.
3. **Deploy.** Actions → Deploy Cloudflare Clicks, with *push_secret* ticked. The run summary
   prints the Worker URL (`https://cariboo-signals-clicks.<your subdomain>.workers.dev`).
4. **Turn it on.** Set super-rss-feed's `config/system.json` → `clicks.base_url` to that URL.
   From the next nightly, digest links go through the Worker and the day's clicks are saved.

The deploy reuses the scheduler's `CLOUDFLARE_API_TOKEN` and `CF_ACCOUNT_ID` secrets.
If the account has never used a `workers.dev` address, the first deploy fails asking for one:
register the subdomain once under Workers & Pages → Overview, then run it again. A dry run
(*dry_run* ticked) checks the build without touching Cloudflare.

## The signature contract

`sign(key, url, category, day)` is the first 32 hex characters of HMAC-SHA256 over
`url + "\n" + category + "\n" + day`. super-rss-feed's `_click_signature` computes the same
thing; a test there pins the format against this file.
