import Link from "next/link";
import { cookies } from "next/headers";
import { logPage } from "@/lib/perf";
import { getSnapshot } from "@/lib/queries";
import { getInProgress, getNewSince } from "@/lib/screens";
import { CITY_LABEL } from "@/lib/settings.generated";
import HealthDot from "./HealthDot";
import InProgressCard from "./InProgressCard";
import MarkVisit from "./MarkVisit";

export const dynamic = "force-dynamic";

function sinceLabel(iso: string, firstVisit: boolean): string {
  if (firstVisit) return "in the last 24 hours";
  const d = new Date(iso);
  const opts = { timeZone: "Asia/Kolkata" } as const;
  const today = new Date().toLocaleDateString("en-IN", opts);
  const time = d.toLocaleTimeString("en-IN", { ...opts, hour: "numeric", minute: "2-digit" });
  return d.toLocaleDateString("en-IN", opts) === today
    ? `since ${time} today`
    : `since ${d.toLocaleDateString("en-IN", { ...opts, weekday: "short" })} ${time}`;
}

/**
 * TODAY (spec 002 US1): what needs Harsh now, top to bottom: processes in
 * flight, new matches since the last visit, this week, and the health dot.
 */
export default async function TodayPage({ params }: { params: Promise<{ secret: string }> }) {
  const { secret } = await params;
  const base = `/d/${secret}`;
  const raw = (await cookies()).get("seen_at")?.value;
  const seen = raw ? new Date(decodeURIComponent(raw)) : null;
  const firstVisit = !seen || Number.isNaN(seen.getTime());
  const since = firstVisit ? new Date(Date.now() - 86_400_000).toISOString() : seen!.toISOString();

  const [snapshot, inProgress, fresh] = await Promise.all([
    getSnapshot(), getInProgress(), getNewSince(since),
  ]);
  const week = snapshot.data.week;

  logPage("today");
  return (
    <>
      <MarkVisit />
      <div className="today">

      <section className="sec today-progress" aria-labelledby="inprog">
        <div className="sec-head">
          <h2 id="inprog">In progress</h2>
          <span className="sec-note">{inProgress.length} live</span>
        </div>
        {inProgress.length === 0 ? (
          <div className="empty-line">Nothing in progress. Replies and interviews show up here.</div>
        ) : (
          inProgress.map((item) => <InProgressCard key={item.job_id} item={item} secret={secret} />)
        )}
      </section>

      <section className="sec today-new" aria-labelledby="newforyou">
        <div className="sec-head">
          <h2 id="newforyou">New for you</h2>
          <span className="sec-note">{sinceLabel(since, firstVisit)}</span>
        </div>
        {fresh.count === 0 ? (
          <div className="empty-line">Nothing new since your last visit.</div>
        ) : (
          <>
            <div className="big-count">
              <b>{fresh.count === 50 ? "50+" : fresh.count}</b>
              <span>new {fresh.count === 1 ? "match" : "matches"} in {CITY_LABEL},<br />core and secondary roles</span>
            </div>
            <div className="list">
              {fresh.items.map((j) => (
                <Link key={j.job_id} className="list-row" href={`${base}/j/${j.job_id}`}>
                  <span className="score" data-high={(j.score ?? 0) >= 8}>{j.score?.toFixed(1)}</span>
                  <span className="list-main">
                    <span className="list-title">{j.title}</span>
                    <span className="list-sub">{j.company} · {j.ageLabel}</span>
                  </span>
                </Link>
              ))}
            </div>
            <Link href={`${base}/jobs`} className="sec-note" style={{ fontWeight: 600, color: "var(--accent-text)", minHeight: 44, display: "flex", alignItems: "center" }}>
              See all jobs →
            </Link>
          </>
        )}
      </section>

      <section className="sec today-week" aria-labelledby="week">
        <div className="sec-head">
          <h2 id="week">This week</h2>
          <span className="sec-note">last 7 days</span>
        </div>
        <div className="grid3">
          <div className="tile"><b>{week.applied}</b><span>Applied</span></div>
          <div className="tile"><b>{week.responses}</b><span>Responses</span></div>
          <div className="tile"><b>{week.referral_asks}</b><span>Referral asks</span></div>
        </div>
      </section>

      <div className="today-health"><HealthDot snapshot={snapshot} base={base} /></div>
      </div>
    </>
  );
}
