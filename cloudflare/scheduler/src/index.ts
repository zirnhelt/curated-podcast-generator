/**
 * Cariboo Signals scheduler.
 *
 * GitHub's cron is best-effort: it delays scheduled workflows under load and
 * drops the tick outright once the delay passes the next window. For this show
 * a trigger that lands after the 6:30 AM Pacific listener wakeup has missed the
 * day, so delivery of the trigger is worth moving to a scheduler that commits
 * to a time. Cloudflare Cron Triggers do.
 *
 * SCOPE: this Worker starts runs. It does not decide whether a run is needed.
 * Every workflow it dispatches already owns that judgement — `check-episode`
 * scans the live RSS feed for today's episode, `preflight` asks the Actions API
 * whether today is already covered — and those checks live next to the pipeline
 * they guard, where they are changed and tested alongside it. Re-implementing
 * them here would make a second source of truth for "did today ship?", whose
 * failure mode when the two drift is a silently skipped day: the exact outcome
 * this migration exists to prevent. What it saves is ~20 s of a free runner.
 *
 * Workers Free allows 5 Cron Triggers per account and all 5 are spent here, on
 * the two ladders where a late trigger costs the day. Every other cron in both
 * repos (cleanup-branches, harvest-twit, ingest-emails, periodic-review,
 * weekly-maintenance) stays on GitHub's scheduler, where arriving an hour late
 * costs nothing. Adding a sixth needs Workers Paid.
 */

export interface Env {
  /**
   * Fine-grained GitHub PAT, pushed by `.github/workflows/deploy-scheduler.yml`.
   * Needs `Actions: Read and write` (to dispatch) and `Issues: Read and write`
   * (to raise the alert below) on BOTH repos named in SCHEDULE.
   *
   * Its expiry is the failure mode to watch: an expired PAT presents exactly
   * like the outage this Worker prevents — nothing starts, and nothing says
   * why. The expiry date is recorded in README.md; the GitHub Actions backstop
   * crons are what cover the day if it lapses.
   */
  GITHUB_TOKEN: string;
}

interface Slot {
  /** "owner/repo" */
  repo: string;
  /** Workflow file name, as the Actions API addresses it. */
  workflow: string;
  /** Git ref to dispatch against. */
  ref: string;
  /** Passed as the workflow's `run_slot` input, which replaces the
   *  `github.event.schedule` string the workflows used to branch on. */
  runSlot: string;
  /** Human label for logs and for the alert issue. */
  label: string;
  /** True on the last rung of a ladder: nothing follows it but the GitHub
   *  backstop cron, so a failure there is worth waking someone for. An earlier
   *  rung failing is recovered by the next one and stays in the logs. */
  alertOnFailure: boolean;
}

const PODCAST_REPO = 'zirnhelt/curated-podcast-generator';
const FEED_REPO = 'zirnhelt/super-rss-feed';

/**
 * Keyed by the exact cron expression in wrangler.jsonc — `controller.cron`
 * returns that string verbatim. The two must be edited together; a cron with no
 * entry here is logged as a configuration error rather than silently ignored.
 *
 * Ordering is causal, not cosmetic: super-rss-feed publishes the scored article
 * pool that the podcast reads at 08:05 UTC, so its ladder runs first.
 */
const SCHEDULE: Record<string, Slot> = {
  // 04:00 UTC — 8:00 PM Pacific the previous evening.
  '0 4 * * *': {
    repo: FEED_REPO,
    workflow: 'generate-feed.yml',
    ref: 'main',
    runSlot: '1',
    label: 'RSS feed — primary (04:00 UTC)',
    alertOnFailure: false,
  },
  // 07:00 UTC — backup tick. The workflow's own preflight stands it down if
  // 04:00 already covered the day, so a double dispatch costs ~20 s, not spend.
  '0 7 * * *': {
    repo: FEED_REPO,
    workflow: 'generate-feed.yml',
    ref: 'main',
    runSlot: '2',
    label: 'RSS feed — backup (07:00 UTC)',
    alertOnFailure: true,
  },

  // BC is permanent UTC-7; these three never shift.
  '5 8 * * *': {
    repo: PODCAST_REPO,
    workflow: 'daily-podcast.yml',
    ref: 'main',
    runSlot: '1',
    label: 'Daily podcast — primary (1:05 AM Pacific)',
    alertOnFailure: false,
  },
  '5 9 * * *': {
    repo: PODCAST_REPO,
    workflow: 'daily-podcast.yml',
    ref: 'main',
    runSlot: '2',
    label: 'Daily podcast — fallback 1 (2:05 AM Pacific)',
    alertOnFailure: false,
  },
  '5 10 * * *': {
    repo: PODCAST_REPO,
    workflow: 'daily-podcast.yml',
    ref: 'main',
    runSlot: '3',
    label: 'Daily podcast — fallback 2 (3:05 AM Pacific)',
    alertOnFailure: true,
  },
};

const API = 'https://api.github.com';
const USER_AGENT = 'cariboo-signals-scheduler';
const ALERT_LABEL = 'scheduler-alert';

/** Three attempts, because the failure worth retrying is a transient one and a
 *  second transient in the same 4 s is rare. Backoff is per retry, not attempt. */
const MAX_ATTEMPTS = 3;
const BACKOFF_MS = [1_000, 3_000];

const sleep = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

function githubHeaders(token: string): Record<string, string> {
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': USER_AGENT,
    'Content-Type': 'application/json',
  };
}

/**
 * POST the workflow_dispatch. Resolves on GitHub's 204; throws with the last
 * failure otherwise.
 *
 * `run_slot` is the only input sent. `force` is deliberately left unset so it
 * takes its declared default — see the comment on that input in
 * daily-podcast.yml, which explains why its comparison must stay a boolean one.
 */
async function dispatch(slot: Slot, env: Env): Promise<void> {
  const url = `${API}/repos/${slot.repo}/actions/workflows/${slot.workflow}/dispatches`;
  const body = JSON.stringify({ ref: slot.ref, inputs: { run_slot: slot.runSlot } });

  let last = '';

  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    if (attempt > 1) await sleep(BACKOFF_MS[attempt - 2]);

    let response: Response;
    try {
      response = await fetch(url, {
        method: 'POST',
        headers: githubHeaders(env.GITHUB_TOKEN),
        body,
      });
    } catch (error) {
      last = `network error: ${error instanceof Error ? error.message : String(error)}`;
      console.warn(`${slot.label}: attempt ${attempt}/${MAX_ATTEMPTS} — ${last}`);
      continue;
    }

    if (response.status === 204) {
      console.log(
        `${slot.label}: dispatched ${slot.workflow} on ${slot.repo}@${slot.ref} ` +
          `(run_slot=${slot.runSlot}, attempt ${attempt})`,
      );
      return;
    }

    last = `HTTP ${response.status} — ${(await response.text()).slice(0, 500)}`;

    // A 4xx that is not a throttle is a verdict on the request itself: an
    // expired token, a workflow renamed, a ref that no longer exists. Asking
    // twice more spends the ladder's time on an answer that will not change.
    if (response.status < 500 && response.status !== 429) {
      console.error(`${slot.label}: ${last} (not retryable)`);
      break;
    }

    console.warn(`${slot.label}: attempt ${attempt}/${MAX_ATTEMPTS} — ${last}`);
  }

  throw new Error(last || 'dispatch failed without a response');
}

/**
 * Raise a GitHub issue on the affected repo. Both repos already use issues as
 * their alerting channel (periodic-review.yml, harvest-episode.yml), so this
 * reaches wherever those already reach rather than adding a service to keep
 * alive.
 *
 * Note the gap this cannot cover: an expired or revoked token fails the
 * dispatch AND this issue, since both use it. That case is covered by the
 * GitHub Actions backstop cron each workflow keeps, not from here.
 */
async function raiseAlert(slot: Slot, reason: string, env: Env): Promise<void> {
  const stamp = new Date().toISOString().slice(0, 10);
  const title = `Scheduler: ${slot.label} did not dispatch (${stamp})`;
  const body = [
    `The Cloudflare scheduler could not start \`${slot.workflow}\`.`,
    '',
    `- **Slot:** ${slot.label} (\`run_slot=${slot.runSlot}\`)`,
    `- **Target:** \`${slot.repo}\` @ \`${slot.ref}\``,
    `- **Last error:** \`${reason}\``,
    '',
    'This is the last rung of its ladder, so nothing else will start the run',
    'today except the GitHub Actions backstop cron in the workflow itself.',
    '',
    'Most likely causes, in the order worth checking:',
    '',
    '1. The `GITHUB_TOKEN` secret on the Worker has expired — see',
    '   `cloudflare/scheduler/README.md` for the recorded expiry date.',
    '2. The token lost `Actions: Read and write` on this repository.',
    '3. The workflow file was renamed, or `main` no longer carries it.',
    '',
    'To ship today by hand, run the workflow from the Actions tab with the',
    'matching `run_slot` input.',
  ].join('\n');

  const url = `${API}/repos/${slot.repo}/issues`;
  const post = (labels: string[]): Promise<Response> =>
    fetch(url, {
      method: 'POST',
      headers: githubHeaders(env.GITHUB_TOKEN),
      body: JSON.stringify({ title, body, labels }),
    });

  // The API rejects the whole issue if the label does not exist yet, and an
  // alert nobody receives is worse than an unlabelled one.
  let response = await post([ALERT_LABEL]);
  if (response.status === 422) response = await post([]);

  if (!response.ok) {
    console.error(
      `${slot.label}: could not raise the alert issue — ` +
        `HTTP ${response.status} ${(await response.text()).slice(0, 300)}`,
    );
    return;
  }

  console.log(`${slot.label}: raised alert issue on ${slot.repo}`);
}

export default {
  async scheduled(controller: ScheduledController, env: Env): Promise<void> {
    const slot = SCHEDULE[controller.cron];

    if (!slot) {
      console.error(
        `No slot registered for cron "${controller.cron}". ` +
          'The crons in wrangler.jsonc and the SCHEDULE table have drifted apart.',
      );
      return;
    }

    try {
      await dispatch(slot, env);
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error);
      console.error(`${slot.label}: dispatch failed — ${reason}`);

      if (slot.alertOnFailure) {
        await raiseAlert(slot, reason, env).catch((alertError: unknown) => {
          console.error(`${slot.label}: alert path also failed — ${String(alertError)}`);
        });
      }

      // Rethrow so the failure is an exception in the Worker's Cron Events
      // table, not just a line in the logs.
      throw error;
    }
  },
} satisfies ExportedHandler<Env>;
