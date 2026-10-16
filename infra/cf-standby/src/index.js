/**
 * The dispatcher for every scheduled workflow (decided 2026-10-15/16).
 *
 * Two crons:
 *
 *  - "7,22,37,52 * * * *": dispatch ingest.yml, unconditionally (source=cf).
 *    cron-job.org fires at :00/:15/:30/:45; while it is healthy these fires
 *    land inside the 8-minute run lease and collapse (trigger_log "lease"),
 *    and the moment it dies the pipeline keeps a 15-minute cadence.
 *
 *  - "41 * * * *": dispatch the other workflows by UTC hour (hourlyTargets).
 *    GitHub's own scheduler was measured skipping them on this repository:
 *    on 2026-10-16 the hourly Indeed search had not run for five hours and
 *    the daily canary and healthcheck not at all. Workflows that are safe to
 *    run twice keep their GitHub schedule as a backup; the daily and weekly
 *    emails rely on this alone so they cannot arrive twice.
 *
 * Budget, on the Cloudflare free plan: at most four subrequests per
 * invocation against 50, a few milliseconds of CPU against 10, and two of the
 * five cron triggers an account may have.
 *
 * Secret: GH_DISPATCH_PAT (wrangler secret put GH_DISPATCH_PAT). A 401 means
 * the token is dead; the watchdog (notify/token_watch.py) and the Apps Script
 * standby both alert on that.
 */

export const REPO = "harsh-m-10/job-discovery-agent";
export const INGEST_CRON = "7,22,37,52 * * * *";
export const HOURLY_CRON = "41 * * * *";

/**
 * Pure: which workflows the hourly tick should start at this moment (UTC).
 * Kept in step with the comments in each workflow file.
 */
export function hourlyTargets(date) {
  const h = date.getUTCHours();
  const dow = date.getUTCDay();          // 0 = Sunday
  const out = ["aggregate.yml"];         // Indeed, every hour
  if (h === 2) out.push("canary.yml");
  if (h === 3) out.push("healthcheck.yml");
  if (h === 3 && dow === 0) out.push("weekly.yml");
  if (h === 3 && dow === 1) out.push("keepalive.yml");
  if (h === 14) out.push("daily.yml");   // 20:11 IST
  if (h === 20) out.push("backup.yml");
  return out;
}

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    // GitHub rejects API requests without a User-Agent.
    "User-Agent": "job-agent-cf",
    "Content-Type": "application/json",
  };
}

async function dispatch(token, workflow, inputs, fetchImpl) {
  const resp = await fetchImpl(
    `https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`,
    {
      method: "POST",
      headers: headers(token),
      body: JSON.stringify(inputs ? { ref: "main", inputs } : { ref: "main" }),
    },
  );
  // GitHub answers 204 No Content on success.
  console.log(`${workflow}: HTTP ${resp.status}${resp.status === 401 ? " (token rejected)" : ""}`);
  return { workflow, dispatched: resp.status === 204, status: resp.status };
}

/** The ingest tick. Kept as tick() for the existing tests and callers. */
export async function tick(env, fetchImpl = fetch) {
  const token = env.GH_DISPATCH_PAT;
  if (!token) {
    console.log("GH_DISPATCH_PAT is not set; nothing to do");
    return { dispatched: false, reason: "unconfigured" };
  }
  const r = await dispatch(token, "ingest.yml", { source: "cf" }, fetchImpl);
  return { dispatched: r.dispatched, status: r.status };
}

export async function hourly(env, fetchImpl = fetch, now = new Date()) {
  const token = env.GH_DISPATCH_PAT;
  if (!token) {
    console.log("GH_DISPATCH_PAT is not set; nothing to do");
    return [];
  }
  const out = [];
  for (const wf of hourlyTargets(now)) {
    out.push(await dispatch(token, wf, null, fetchImpl));
  }
  return out;
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(event.cron === HOURLY_CRON ? hourly(env) : tick(env));
  },
  // No public surface: anything hitting the Worker's URL gets a 404.
  async fetch() {
    return new Response("not found", { status: 404 });
  },
};
