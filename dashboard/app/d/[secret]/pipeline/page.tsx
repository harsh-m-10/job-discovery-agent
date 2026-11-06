import Link from "next/link";
import { logPage } from "@/lib/perf";
import { getPipeline } from "@/lib/screens";
import StageSelect from "./StageSelect";

export const dynamic = "force-dynamic";

const SHOW = 25;

function shortDate(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" });
}

function pct(n: number, d: number): string {
  return d ? `${Math.round((n / d) * 100)}%` : "–";
}

/**
 * PIPELINE (spec 002 US3): every application by stage, a one-tap stage
 * change, and conversion by channel, family and tier at the bottom.
 * Replaces Funnel, which stays reachable from Health as detailed stats.
 */
export default async function PipelinePage({
  params, searchParams,
}: {
  params: Promise<{ secret: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { secret } = await params;
  const base = `/d/${secret}`;
  const sp = await searchParams;
  const expanded = String(sp.all ?? "");
  const data = await getPipeline();

  const conversion = [
    { title: "By channel", rows: data.byChannel },
    { title: "By role family", rows: data.byFamily },
    { title: "By company tier", rows: data.byTier },
  ];

  logPage("pipeline");
  return (
    <>
      <div className="cols">
      {data.stages.map((stage) => {
        const rows = expanded === stage.id ? stage.rows : stage.rows.slice(0, SHOW);
        return (
          <section className="sec" key={stage.id} aria-labelledby={`st-${stage.id}`}>
            <div className="sec-head">
              <h2 id={`st-${stage.id}`}>{stage.label}</h2>
              <span className="sec-note">{stage.rows.length}</span>
            </div>
            {stage.rows.length === 0 ? (
              <div className="empty-line">None.</div>
            ) : (
              <div className="list">
                {rows.map((r) => (
                  <div className="list-row" key={r.job_id}>
                    <span className="list-main">
                      <Link className="list-title" href={`${base}/j/${r.job_id}`} style={{ color: "var(--text)" }}>
                        {r.company}
                      </Link>
                      <span className="list-sub">
                        {r.role}{r.date ? ` · ${shortDate(r.date)}` : ""}{r.next_step ? ` · ${r.next_step}` : ""}
                      </span>
                    </span>
                    <StageSelect jobId={r.job_id} status={r.status} secret={secret} label={r.company} />
                  </div>
                ))}
              </div>
            )}
            {stage.rows.length > SHOW && expanded !== stage.id && (
              <Link className="sec-note" href={`${base}/pipeline?all=${stage.id}#st-${stage.id}`}
                    style={{ fontWeight: 600, color: "var(--accent-text)", minHeight: 44, display: "flex", alignItems: "center" }}>
                Show all {stage.rows.length}
              </Link>
            )}
          </section>
        );
      })}
      </div>

      <section className="sec" aria-labelledby="conv">
        <div className="sec-head">
          <h2 id="conv">What converts</h2>
          <span className="sec-note">all applications</span>
        </div>
        {conversion.map((block) => (
          <div key={block.title} className="table-scroll">
            <table>
              <thead>
                <tr><th scope="col">{block.title}</th><th scope="col">Applied</th>
                  <th scope="col">Any reply</th><th scope="col">Moved on</th></tr>
              </thead>
              <tbody>
                {block.rows.map((r) => (
                  <tr key={r.label}>
                    <td>{r.label}</td><td>{r.applied}</td>
                    <td>{r.replied} <span className="sec-note">({pct(r.replied, r.applied)})</span></td>
                    <td>{r.moved} <span className="sec-note">({pct(r.moved, r.applied)})</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
        <p className="sec-note" style={{ margin: 0 }}>
          “Moved on” = reached a screen, assessment, interview or offer.
        </p>
      </section>
    </>
  );
}
