import Link from "next/link";
import { Fragment } from "react";
import { logPage } from "@/lib/perf";
import { getSnapshot, getStatus } from "@/lib/queries";
import HealthDot from "../HealthDot";
import StatusStrip from "../StatusStrip";

export const dynamic = "force-dynamic";

const SOURCE_LABEL: Record<string, string> = { ats: "Company boards", indeed: "Indeed", "alert-email": "Alert emails" };

function when(iso: string | null | undefined): string {
  if (!iso) return "never recorded";
  return new Date(iso).toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "numeric", minute: "2-digit",
  });
}

/**
 * HEALTH (spec 002 US5): the operational view, kept off the main screens.
 * Internals (commands, providers, boards) are allowed here and only here.
 */
export default async function HealthPage({ params }: { params: Promise<{ secret: string }> }) {
  const { secret } = await params;
  const base = `/d/${secret}`;
  const [snapshot, status] = await Promise.all([getSnapshot(), getStatus()]);
  const d = snapshot.data;
  const sources = Object.entries(d.new_24h.by_source ?? {}).sort((a, b) => b[1] - a[1]);
  const ops = d.ops ?? { canary: null, backup: null };

  logPage("health");
  return (
    <>
      <section className="sec" aria-labelledby="h-now">
        <div className="sec-head"><h1 id="h-now">Health</h1>
          <span className="sec-note">updated {Math.round(snapshot.ageMinutes)} min ago</span></div>
        <HealthDot snapshot={snapshot} base={base} />
        {d.health.reasons.length > 0 && (
          <ul style={{ margin: 0, paddingLeft: 20, fontSize: 14 }}>
            {d.health.reasons.map((r) => <li key={r}>{r}</li>)}
          </ul>
        )}
        <StatusStrip status={status} />
      </section>

      <div className="cols">
      <section className="sec" aria-labelledby="h-numbers">
        <div className="sec-head"><h2 id="h-numbers">Last 24 hours</h2></div>
        <dl className="kv">
          <dt>Checks for new jobs</dt><dd>{d.runs_24h}</dd>
          {sources.map(([src, n]) => (
            <Fragment key={src}><dt>New postings · {SOURCE_LABEL[src] ?? src}</dt><dd>{n}</dd></Fragment>
          ))}
          <dt>Open postings tracked</dt><dd>{d.open_jobs}</dd>
          <dt>Waiting to be scored</dt><dd>{d.unscored}</dd>
          <dt>Could not be scored</dt><dd>{d.stranded}</dd>
          <dt>Scoring models working</dt><dd>{d.health.live_providers_24h}</dd>
        </dl>
        {d.stranded > 0 && (
          <p className="sec-note" style={{ margin: 0 }}>
            Recover with <code>python -m score.healthcheck</code>, then{" "}
            <code>python -m score.run --retry-failed</code>.
          </p>
        )}
      </section>

      <section className="sec" aria-labelledby="h-ops">
        <div className="sec-head"><h2 id="h-ops">Safety nets</h2></div>
        <dl className="kv">
          <dt>Daily self-check</dt>
          <dd>{ops.canary ? `${ops.canary.ok ? "Passed" : "Failed"} · ${when(ops.canary.at)}` : "Not recorded yet"}</dd>
          <dt>Last backup</dt>
          <dd>{ops.backup ? `${ops.backup.ok ? "OK" : "Failed"} · ${when(ops.backup.at)}` : "Not recorded yet"}</dd>
          <dt>Boards switched off</dt><dd>{d.inactive_boards.length}</dd>
        </dl>
      </section>

      <section className="sec" aria-labelledby="h-more">
        <div className="sec-head"><h2 id="h-more">More</h2></div>
        <div className="list">
          <Link className="list-row" href={`${base}/review`}>
            <span className="list-main"><span className="list-title">Application emails to check</span>
              <span className="list-sub">{d.review_pending} waiting</span></span>
          </Link>
          <Link className="list-row" href={`${base}/companies`}>
            <span className="list-main"><span className="list-title">Boards</span>
              <span className="list-sub">Every company board polled, and its state</span></span>
          </Link>
          <Link className="list-row" href={`${base}/funnel`}>
            <span className="list-main"><span className="list-title">Detailed stats</span>
              <span className="list-sub">Latency, calibration, skill gaps</span></span>
          </Link>
        </div>
      </section>
      </div>
    </>
  );
}
