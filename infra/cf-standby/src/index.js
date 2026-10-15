/**
 * Standby dispatcher for the ingest workflow (docs/PLAN.md Phase 2.5).
 *
 * The primary trigger is an external pinger (cron-job.org) that dispatches
 * ingest.yml every 15 minutes. This Worker is the second, independent one: on
 * its own 15-minute cron it looks at the newest ingest run and dispatches only
 * if that run is older than STALE_MINUTES. While the primary is healthy it
 * does nothing but one GET; when the primary dies, it takes over within one
 * tick, and every run it fires says so (inputs.source = "cf-standby") so the
 * run_log shows the primary is down even while the pipeline stays current.
 *
 * Budget, on the Cloudflare free plan: two subrequests per invocation at most
 * (one GET, one POST) against a limit of 50, and a few milliseconds of CPU
 * against 10. Cron triggers are limited to 5 per account; this uses one.
 *
 * Secret: GH_DISPATCH_PAT (wrangler secret put GH_DISPATCH_PAT), a
 * fine-grained token with Actions: read & write on the one repository.
 */

export const REPO = "harsh-m-10/job-discovery-agent";
export const WORKFLOW = "ingest.yml";

// The primary fires every 15 minutes, so a newest run older than 25 means it
// has missed at least one tick. Shorter would race the primary; longer would
// let a dead primary cost two runs.
export const STALE_MINUTES = 25;

/**
 * Pure decision, tested in index.test.mjs. `latest` is the newest workflow run
 * as GitHub returns it, or undefined when there has never been one.
 *
 * Uses created_at, not updated_at or the run's conclusion: the question is
 * whether *a trigger* fired recently, and a queued or still-running run
 * answers yes. Whether runs are succeeding is the heartbeat's job.
 */
export function shouldDispatch(latest, nowMs, staleMinutes = STALE_MINUTES) {
  if (!latest || !latest.created_at) {
    return { dispatch: true, reason: "no previous run found" };
  }
  const created = Date.parse(latest.created_at);
  if (Number.isNaN(created)) {
    return { dispatch: true, reason: `unparseable created_at ${latest.created_at}` };
  }
  const ageMin = (nowMs - created) / 60_000;
  if (ageMin > staleMinutes) {
    return { dispatch: true, reason: `newest run is ${ageMin.toFixed(1)} min old` };
  }
  return { dispatch: false, reason: `newest run is ${ageMin.toFixed(1)} min old` };
}

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    // GitHub rejects API requests without a User-Agent.
    "User-Agent": "job-agent-cf-standby",
  };
}

export async function tick(env, fetchImpl = fetch, nowMs = Date.now()) {
  const token = env.GH_DISPATCH_PAT;
  if (!token) {
    console.log("GH_DISPATCH_PAT is not set; nothing to do");
    return { dispatched: false, reason: "unconfigured" };
  }
  const base = `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}`;

  const listing = await fetchImpl(`${base}/runs?per_page=1`, { headers: headers(token) });
  if (!listing.ok) {
    // Do not dispatch blind on an API error: a 401 here means the token is
    // dead, and the watchdog's token check is what reports that.
    console.log(`listing runs failed: HTTP ${listing.status}`);
    return { dispatched: false, reason: `list ${listing.status}` };
  }
  const body = await listing.json();
  const decision = shouldDispatch(body.workflow_runs?.[0], nowMs);
  console.log(`decision: ${decision.dispatch ? "dispatch" : "skip"} (${decision.reason})`);
  if (!decision.dispatch) return { dispatched: false, reason: decision.reason };

  const resp = await fetchImpl(`${base}/dispatches`, {
    method: "POST",
    headers: { ...headers(token), "Content-Type": "application/json" },
    body: JSON.stringify({ ref: "main", inputs: { source: "cf-standby" } }),
  });
  // GitHub answers 204 No Content on success.
  console.log(`dispatch: HTTP ${resp.status}`);
  return { dispatched: resp.status === 204, reason: decision.reason, status: resp.status };
}

export default {
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(tick(env));
  },
  // No public surface: anything hitting the Worker's URL gets a 404.
  async fetch() {
    return new Response("not found", { status: 404 });
  },
};
