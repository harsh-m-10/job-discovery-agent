import Link from "next/link";
import { getQueue } from "@/lib/queries";
import { effectivePostedAt, formatAge, hoursSince } from "@/lib/db";
import StatusButtons from "./StatusButtons";

export const dynamic = "force-dynamic";

function expLabel(min: number | null, max: number | null): string {
  if (min !== null && max !== null) return `asks ${min}-${max}y`;
  if (min !== null) return `asks ${min}y+`;
  return "exp unstated";
}

export default async function QueuePage({
  params,
}: {
  params: Promise<{ secret: string }>;
}) {
  const { secret } = await params;
  const rows = await getQueue();

  return (
    <>
      <div className="spread" style={{ marginBottom: 14 }}>
        <h2 style={{ margin: 0, fontSize: 16 }}>
          Queue <span className="muted">({rows.length})</span>
        </h2>
        <span className="muted small">
          scored at or above threshold, not yet actioned — highest fit first
        </span>
      </div>

      {rows.length === 0 && (
        <div className="card muted">
          Nothing queued. Run <code>python -m score.run</code> after an ingestion pass.
        </div>
      )}

      {rows.map((row) => {
        const posted = effectivePostedAt(row);
        const age = hoursSince(posted);
        return (
          <div className="card" key={row.job_id}>
            <div className="spread">
              <div className="row" style={{ gap: 14, alignItems: "baseline" }}>
                <span className={`score v-${row.verdict}`}>
                  {row.fit_score?.toFixed(1) ?? "—"}
                </span>
                <div>
                  <div style={{ fontWeight: 600 }}>
                    <Link href={`/d/${secret}/j/${row.job_id}`}>{row.title}</Link>
                  </div>
                  <div className="muted small">
                    {row.company}
                    {row.location ? ` · ${row.location}` : ""} · {formatAge(age)} old ·{" "}
                    {expLabel(row.min_years, row.max_years)}
                    {row.compensation ? ` · ${row.compensation}` : ""}
                    {!row.posted_at && (
                      <span title="ATS gave no posting date; age is measured from first sighting">
                        {" "}· age from first sighting
                      </span>
                    )}
                  </div>
                </div>
              </div>
              <a href={row.absolute_url} target="_blank" rel="noreferrer noopener">
                open ↗
              </a>
            </div>

            {row.reasoning && (
              <div className="small" style={{ margin: "8px 0" }}>{row.reasoning}</div>
            )}

            <div style={{ margin: "6px 0" }}>
              {(row.matched_skills ?? []).map((s) => (
                <span className="tag" key={`m${s}`}>{s}</span>
              ))}
              {(row.gap_skills ?? []).map((s) => (
                <span className="tag gap" key={`g${s}`}>{s}</span>
              ))}
            </div>

            {row.referrals.length > 0 && (
              <div className="small" style={{ marginBottom: 6 }}>
                <span className="muted">contacts here: </span>
                {row.referrals.map((c) => (
                  <span className={`tag${c.is_batchmate ? " batchmate" : ""}`} key={c.id}>
                    {c.full_name}
                    {c.title ? ` — ${c.title}` : ""}
                    {c.is_batchmate ? " (batchmate)" : ""}
                  </span>
                ))}
              </div>
            )}

            <StatusButtons jobId={row.job_id} secret={secret} current={row.status} />
          </div>
        );
      })}
    </>
  );
}
