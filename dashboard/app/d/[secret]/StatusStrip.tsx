import { formatAge } from "@/lib/db";
import type { PipelineStatus } from "@/lib/queries";

// Staleness thresholds for the last ingest run. Set against today's reality,
// not the cron's promise: GitHub's throttled `schedule` trigger runs a median
// of ~5h apart (STATE.md G1), so anything tighter would sit permanently amber
// and teach the reader to ignore the colour. Once Phase 2's dispatch trigger
// lands at a 15-minute cadence these should drop to roughly 0.5h / 1h.
const STALE_WARN_HOURS = 6;
const STALE_BAD_HOURS = 12;

type Health = "ok" | "warn" | "bad";

function healthOf(s: PipelineStatus): Health {
  if (s.lastRunAgeHours === null || s.lastRunAgeHours > STALE_BAD_HOURS) return "bad";
  if (s.failingBoards.some((b) => !b.transient)) return "bad";
  if (s.lastRunAgeHours > STALE_WARN_HOURS) return "warn";
  if (s.failingBoards.length > 0) return "warn";
  return "ok";
}

/**
 * One line of pipeline health above the queue (docs/PLAN.md 1.2).
 *
 * Always rendered, because "the queue is empty" and "the pipeline stopped
 * hours ago" otherwise look identical. Kept quiet — muted text, a small dot —
 * and only coloured when something actually needs attention.
 *
 * Inactive boards are listed as a count but do not affect the colour: two are
 * retired permanently today (Chronosphere, Postman), and a strip that is amber
 * forever is a strip nobody reads.
 */
export default function StatusStrip({ status }: { status: PipelineStatus }) {
  const health = healthOf(status);
  const run = status.lastRun;

  if (!run) {
    return (
      <div className="status" data-health="bad" role="status">
        <span className="status-dot" aria-hidden />
        <b>No ingest run has ever been recorded.</b>
      </div>
    );
  }

  const mixTotal = status.providerMix.reduce((n, p) => n + p.count, 0);

  return (
    <div className="status" data-health={health} role="status">
      <span className="status-dot" aria-hidden />
      <span>
        Last run <b>{formatAge(status.lastRunAgeHours ?? 0)} ago</b>
        {run.trigger_source && <> · {run.trigger_source}</>}
      </span>
      <span>
        <span className="status-num">{run.jobs_seen ?? "–"}</span> seen,{" "}
        <span className="status-num">{run.jobs_new ?? "–"}</span> new,{" "}
        <span className="status-num">{run.jobs_closed ?? "–"}</span> closed
        {run.duration_s !== null && (
          <> in <span className="status-num">{Number(run.duration_s).toFixed(0)}s</span></>
        )}
      </span>
      <span>
        <span className="status-num">{status.runsLast24h}</span> runs in 24h
      </span>
      <span>
        LLM {status.providerMixDays}d:{" "}
        {mixTotal === 0
          ? <b>nothing scored</b>
          : status.providerMix.map((p, i) => (
              <span key={p.provider}>
                {i > 0 && ", "}
                {p.provider} <span className="status-num">{p.count}</span>
              </span>
            ))}
      </span>
      {status.inactiveBoards.length > 0 && (
        <span title={status.inactiveBoards.join(", ")}>
          <span className="status-num">{status.inactiveBoards.length}</span> boards inactive
        </span>
      )}

      {status.failingBoards.length > 0 && (
        <span className="status-detail">
          <b>Failed last run:</b>{" "}
          {status.failingBoards.map((b, i) => (
            <span key={b.label}>
              {i > 0 && "; "}
              {b.label}
              {b.transient ? " (transient)" : ""} — {b.error.slice(0, 90)}
            </span>
          ))}
        </span>
      )}
    </div>
  );
}
