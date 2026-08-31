# Cariboo Signals scheduler

A Cloudflare Worker whose only job is to start GitHub Actions workflows on time.

## Why this exists

GitHub's cron is best-effort. It delays scheduled workflows under load and drops
the tick outright once the delay passes the next window. The daily episode has a
hard deadline — a trigger that arrives after the 6:30 AM Pacific listener wakeup
has missed the day — and the podcast's three-cron ladder was absorbing that
unreliability rather than fixing it.

The pipeline itself is untouched and still runs on GitHub Actions: it needs
ffmpeg, a 40-minute render, the commit-between-stages state model and ~20 repo
secrets. Workers cannot host it (128 MB, no subprocesses, no ffmpeg; audio
assembly alone peaks at 300–600 MB). **Only the trigger moved.**

## Rollout status: not yet firing

**This commit changes no schedule.** The Worker's code and its deploy workflow are
on `main`, GitHub's crons still run both shows, and the `run_slot` inputs the
Worker sends are accepted and unused. Land it, then run **Deploy Cloudflare
Scheduler** with `dry_run: true`: it typechecks, builds the bundle against the
real `wrangler.jsonc`, and probes the PAT against both repositories — without
touching Cloudflare. That is the safe order, since `deploy-scheduler.yml` has to
be on `main` before the Actions tab will offer it at all.

The follow-up commit on this branch hands the ladders over: it demotes each
workflow to the single backstop cron described below and moves the podcast's
`review` gate from `github.event.schedule` onto `run_slot`. Deploy for real at
that point — `push_secret: true`, `dry_run: false` — and delete this section.

**The two schedulers are deliberately never run in parallel.** It is tempting, and
for the podcast it is a duplicate-render trap: `check-episode` reads only the
*published* Pages feed, which lags the run that wrote it by minutes, while the
`daily-podcast` concurrency group starts a queued run the moment the previous one
ends. Two triggers in the same minute put the second squarely inside that window,
and it re-renders the episode — 40 minutes and a full day of API spend. The
backstop crons cover the handover instead, and they still ship the day before the
6:30 AM wakeup.

## What it does not do

It starts runs. It does not decide whether a run is needed.

Every workflow it dispatches already owns that judgement — `check-episode` scans
the live RSS feed for today's episode, `preflight` asks the Actions API whether
today is already covered — and those checks live next to the pipeline they
guard. Duplicating them here would create a second source of truth for "did
today ship?", whose failure mode when the two drift is a silently skipped day:
the exact outcome this migration exists to prevent. What the duplication would
save is about 20 seconds of a free runner.

## The schedule

Workers Free allows **5 Cron Triggers per account**, and all five are spent here,
on the two ladders where a late trigger costs the day.

| Cron (UTC) | Repo | Workflow | `run_slot` | Alerts |
|---|---|---|---|---|
| `0 4 * * *` | super-rss-feed | `generate-feed.yml` | 1 | — |
| `0 7 * * *` | super-rss-feed | `generate-feed.yml` | 2 | yes |
| `5 8 * * *` | curated-podcast-generator | `daily-podcast.yml` | 1 | — |
| `5 9 * * *` | curated-podcast-generator | `daily-podcast.yml` | 2 | — |
| `5 10 * * *` | curated-podcast-generator | `daily-podcast.yml` | 3 | yes |

Ordering is causal: super-rss-feed publishes the scored article pool the podcast
reads at 08:05 UTC, so its ladder runs first. BC is permanent UTC-7, so none of
these shift with the seasons.

**The ladders are not redundant with this Worker.** They were always doing two
jobs, and only one of them was "the trigger did not fire". The other is "the
first run failed and the second ships the day", which no scheduler fixes.

Everything else stays on GitHub's cron — `cleanup-branches`, `harvest-twit`,
`ingest-emails`, `periodic-review`, `weekly-maintenance` — because arriving an
hour late costs those nothing. A sixth trigger needs Workers Paid.

**`wrangler.jsonc` and the `SCHEDULE` table in `src/index.ts` must be edited
together.** Wrangler replaces the deployed crons wholesale with the `crons`
array, and a cron with no `SCHEDULE` entry fires into a logged configuration
error rather than a dispatch.

## Both schedulers still exist

Each workflow keeps one GitHub cron as a backstop, three hours behind the last
Worker slot — `5 11 * * *` for the podcast, `0 10 * * *` for the feed. On a
normal night the backstop costs about 20 seconds: it starts, the workflow's own
idempotency check sees the day is covered, and it exits.

That is deliberate. It is what covers a Cloudflare outage, a lapsed PAT, or a
deploy that removed the crons, and it means the schedule does not depend on one
vendor. Do not remove it to tidy up.

## Failure and alerting

The last rung of each ladder raises a GitHub issue on the affected repo if its
own dispatch fails — reusing the alerting channel `periodic-review.yml` and
`harvest-episode.yml` already use, rather than adding a service to keep alive.
Earlier rungs only log, because the next rung recovers them.

**The gap this cannot cover:** an expired or revoked PAT fails the dispatch *and*
the issue, since both use the same token. Nothing inside the Worker can report
that. The GitHub backstop cron is what ships the day if it happens.

An expired PAT presents exactly like the outage this Worker prevents — nothing
starts, and nothing says why. So record the expiry here when the token is
issued or rotated:

> **`GITHUB_TOKEN` expires:** _(not yet issued — fill this in)_

## Deploying

There is no local checkout of this repo on a workstation, so `wrangler login`
(interactive OAuth) and a local `wrangler deploy` are both out, and credentials
must never be pasted into a chat. Deployment runs from GitHub Actions instead:
**Actions → Deploy Cloudflare Scheduler → Run workflow**.

Set `push_secret: true` the first time, and thereafter only when rotating the
PAT — it is gated so an ordinary redeploy does not rewrite a working credential.
The deploy verifies the PAT against both repos before it pushes it, so a token
with the wrong scopes fails the workflow instead of failing at 1 AM.

### One-time operator setup

1. **Fine-grained GitHub PAT.** Scope it to `zirnhelt/curated-podcast-generator`
   *and* `zirnhelt/super-rss-feed`, with `Actions: Read and write` and
   `Issues: Read and write`. Choose the longest expiry offered and record it
   above.
2. **Cloudflare API token** with `Workers Scripts: Edit` on the account that
   holds the `cariboo-signals` R2 bucket.
3. Add both as repository secrets on `curated-podcast-generator`:
   `SCHEDULER_GITHUB_TOKEN` and `CLOUDFLARE_API_TOKEN`. `CF_ACCOUNT_ID` already
   exists — the R2 sync uses it.
4. Run the deploy workflow with `push_secret: true`.
5. Watch the Worker's **Cron Events** table for three or four nights before
   trusting it.

## Rolling back

Restore the removed `schedule:` entries in the two workflows and redeploy this
Worker with `"crons": []`. The `run_slot` inputs and the widened job conditions
are backward compatible and can stay. There is no state, no data migration and
no credential to rotate.

## Local development

```bash
npm ci
npm run typecheck                       # tsc --noEmit
npx wrangler deploy --dry-run           # parse config, build the bundle
```
