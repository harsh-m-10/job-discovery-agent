import Link from "next/link";
import { logPage } from "@/lib/perf";
import { getJobs, type JobFilters } from "@/lib/screens";
import { CITY_LABEL } from "@/lib/settings.generated";
import JobItemCard from "../JobItemCard";
import Pager, { pageFrom } from "../Pager";

export const dynamic = "force-dynamic";

type SP = Record<string, string | string[] | undefined>;

function list(sp: SP, key: string, fallback: string[] = []): string[] {
  const raw = Array.isArray(sp[key]) ? sp[key]![0] : sp[key];
  if (raw === undefined) return fallback;
  return raw.split(",").map((s) => s.trim()).filter(Boolean);
}

const ROWS: { key: keyof JobFilters; label: string; chips: { id: string; label: string }[]; single?: boolean }[] = [
  { key: "fam", label: "Family", chips: [{ id: "core", label: "Core" }, { id: "secondary", label: "Secondary" }, { id: "other", label: "Other" }] },
  { key: "src", label: "Source", chips: [{ id: "direct", label: "Direct" }, { id: "indeed", label: "Indeed" }, { id: "alert", label: "Alerts" }] },
  { key: "tier", label: "Tier", chips: [{ id: "1", label: "Tier 1" }, { id: "2", label: "Tier 2" }, { id: "3", label: "Tier 3" }] },
  { key: "fresh", label: "Fresh", chips: [{ id: "1", label: "Today" }, { id: "3", label: "3 days" }], single: true },
];

/**
 * JOBS (spec 002 US2): jobs in the operator's city and target families by default, chip
 * filters as plain links (no client script), best match first, and anything
 * older than the display window in a collapsed section at the bottom.
 */
export default async function JobsPage({
  params, searchParams,
}: {
  params: Promise<{ secret: string }>;
  searchParams: Promise<SP>;
}) {
  const { secret } = await params;
  const base = `/d/${secret}`;
  const sp = await searchParams;
  const f: JobFilters = {
    fam: list(sp, "fam", ["core", "secondary"]),
    src: list(sp, "src"),
    tier: list(sp, "tier"),
    fresh: list(sp, "fresh")[0] ?? null,
  };
  const olderPage = pageFrom({ p: sp.op });
  const data = await getJobs(f, olderPage);

  const state: Record<string, string[]> = { fam: f.fam, src: f.src, tier: f.tier, fresh: f.fresh ? [f.fresh] : [] };
  function href(key: string, id: string, single?: boolean): string {
    const next: Record<string, string[]> = { ...state };
    const cur = next[key] ?? [];
    next[key] = cur.includes(id) ? cur.filter((x) => x !== id) : single ? [id] : [...cur, id];
    const qs = Object.entries(next)
      .filter(([k, v]) => v.length || k === "fam")
      .map(([k, v]) => `${k}=${encodeURIComponent(v.join(","))}`)
      .join("&");
    return `${base}/jobs${qs ? `?${qs}` : ""}`;
  }
  const keep = Object.entries(state).filter(([, v]) => v.length)
    .map(([k, v]) => `${k}=${encodeURIComponent(v.join(","))}`).join("&");

  const shownRecent = data.recent.length;
  const windowLabel = data.recentDays === 1 ? "today" : `last ${data.recentDays} days`;

  logPage("jobs");
  return (
    <>
      <div className="chips">
        {ROWS.map((row) => (
          <div className="chip-row" key={row.key}>
            <span className="chip-label">{row.label}</span>
            {row.chips.map((c) => (
              <Link key={c.id} className="chip" data-on={state[row.key]?.includes(c.id) ?? false}
                    aria-pressed={state[row.key]?.includes(c.id) ?? false}
                    href={href(row.key, c.id, row.single)} scroll={false}>
                {c.label}
              </Link>
            ))}
          </div>
        ))}
      </div>

      <p className="sec-note" style={{ margin: "8px 0 12px" }}>
        {shownRecent}{data.recentMore ? "+" : ""} {shownRecent === 1 ? "job" : "jobs"} · {CITY_LABEL} · {windowLabel} · best match first
      </p>

      <section className="sec cards" aria-label="Jobs">
        {shownRecent === 0 ? (
          <div className="empty-line">No jobs match these filters in the {windowLabel}.</div>
        ) : (
          data.recent.map((j) => <JobItemCard key={j.job_id} job={j} secret={secret} base={base} />)
        )}

        {data.olderCount > 0 && (
          <details className="older" open={olderPage > 1}>
            <summary>
              <span>Older than 5 days <span className="sec-note">({data.olderCount}{data.olderCount >= 500 ? "+" : ""})</span></span>
              <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   strokeWidth="2" strokeLinecap="round"><path d="M6 9l6 6 6-6" /></svg>
            </summary>
            <div className="older-body cards">
              {data.older.map((j) => <JobItemCard key={j.job_id} job={j} secret={secret} base={base} />)}
              <Pager page={olderPage} hasMore={data.olderMore}
                     href={(n) => `${base}/jobs?${keep}${keep ? "&" : ""}op=${n}`} />
            </div>
          </details>
        )}
      </section>
    </>
  );
}
