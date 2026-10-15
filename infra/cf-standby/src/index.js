/**
 * Second dispatcher for the ingest workflow (decided 2026-10-15).
 *
 * Four triggers start ingest.yml: cron-job.org at :00/:15/:30/:45 (primary),
 * this Worker at :07/:22/:37/:52, the Apps Script standby when the newest run
 * is >25 min old, and GitHub's own schedule. This Worker dispatches on every
 * tick, unconditionally: the offset means that while cron-job.org is healthy
 * its fires land inside the 8-minute run lease and collapse into a no-op
 * (recorded in trigger_log as "lease"), and the moment the primary dies the
 * pipeline still runs every 15 minutes with no detection delay at all.
 *
 * Every run it fires says so (inputs.source = "cf"), so trigger_log and the
 * weekly report show each trigger's share and which one has gone quiet.
 *
 * Budget, on the Cloudflare free plan: one subrequest per invocation against
 * a limit of 50, and a few milliseconds of CPU against 10. Cron triggers are
 * limited to 5 per account; this uses one.
 *
 * Secret: GH_DISPATCH_PAT (wrangler secret put GH_DISPATCH_PAT). A 401 here
 * means the token is dead; the watchdog's token check (notify/token_watch.py)
 * and the Apps Script both alert on that.
 */

export const REPO = "harsh-m-10/job-discovery-agent";
export const WORKFLOW = "ingest.yml";
export const SOURCE = "cf";

function headers(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    // GitHub rejects API requests without a User-Agent.
    "User-Agent": "job-agent-cf",
  };
}

export async function tick(env, fetchImpl = fetch) {
  const token = env.GH_DISPATCH_PAT;
  if (!token) {
    console.log("GH_DISPATCH_PAT is not set; nothing to do");
    return { dispatched: false, reason: "unconfigured" };
  }
  const resp = await fetchImpl(
    `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
    {
      method: "POST",
      headers: { ...headers(token), "Content-Type": "application/json" },
      body: JSON.stringify({ ref: "main", inputs: { source: SOURCE } }),
    },
  );
  // GitHub answers 204 No Content on success.
  console.log(`dispatch: HTTP ${resp.status}${resp.status === 401 ? " (token rejected)" : ""}`);
  return { dispatched: resp.status === 204, status: resp.status };
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
