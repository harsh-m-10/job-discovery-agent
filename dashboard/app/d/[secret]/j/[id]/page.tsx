import Link from "next/link";
import { notFound } from "next/navigation";
import { getJob } from "@/lib/queries";
import { effectivePostedAt, formatAge, hoursSince } from "@/lib/db";
import StatusButtons from "../../StatusButtons";
import Copyable from "./Copyable";
import screening from "@/lib/screening.generated.json";

export const dynamic = "force-dynamic";

export default async function JobPage({
  params,
}: {
  params: Promise<{ secret: string; id: string }>;
}) {
  const { secret, id } = await params;
  const jobId = Number(id);
  if (!Number.isInteger(jobId)) notFound();

  const data = await getJob(jobId);
  if (!data) notFound();
  const { job, score, application, referrals } = data;

  const posted = effectivePostedAt(job);
  const age = hoursSince(posted);
  const company = job.companies?.name ?? "";

  return (
    <>
      <div className="spread" style={{ marginBottom: 12 }}>
        <div>
          <h2 style={{ margin: "0 0 4px", fontSize: 18 }}>{job.title}</h2>
          <div className="muted small">
            {company}
            {job.location ? ` · ${job.location}` : ""} · {formatAge(age)} old
            {job.compensation ? ` · ${job.compensation}` : ""}
            {job.closed_at && (
              <span style={{ color: "var(--danger)" }}> · CLOSED on the board</span>
            )}
          </div>
        </div>
        <div className="row" style={{ gap: 10 }}>
          <Link href={`/d/${secret}`}>← queue</Link>
          <a href={job.absolute_url} target="_blank" rel="noreferrer noopener">
            apply ↗
          </a>
        </div>
      </div>

      <div className="grid2">
        <div>
          <div className="card">
            <div className="muted small" style={{ marginBottom: 6 }}>
              Job description
            </div>
            <pre className="jd">{job.description ?? "(no description captured)"}</pre>
          </div>
        </div>

        <div>
          <div className="card">
            {score ? (
              <>
                <div className="row" style={{ gap: 12, marginBottom: 6 }}>
                  <span className={`score v-${score.verdict}`}>
                    {score.fit_score?.toFixed(1) ?? "—"}
                  </span>
                  <span className="muted">{score.verdict}</span>
                </div>
                <div className="small">{score.reasoning}</div>
                <div style={{ marginTop: 8 }}>
                  {(score.matched_skills ?? []).map((s: string) => (
                    <span className="tag" key={`m${s}`}>{s}</span>
                  ))}
                  {(score.gap_skills ?? []).map((s: string) => (
                    <span className="tag gap" key={`g${s}`}>{s}</span>
                  ))}
                </div>
                <div className="muted small" style={{ marginTop: 8 }}>
                  stated experience:{" "}
                  {score.min_years !== null && score.max_years !== null
                    ? `${score.min_years}-${score.max_years}y`
                    : score.min_years !== null
                      ? `${score.min_years}y+`
                      : "unstated"}
                  {score.model ? ` · scored by ${score.model}` : ""}
                </div>
              </>
            ) : (
              <div className="muted small">Not scored yet.</div>
            )}
          </div>

          <div className="card">
            <div className="muted small" style={{ marginBottom: 8 }}>Status</div>
            <StatusButtons
              jobId={job.id}
              secret={secret}
              current={application?.status ?? "queued"}
            />
            <div className="muted small" style={{ marginTop: 8 }}>
              current: {application?.status ?? "queued"}
              {application?.hours_since_posted !== null &&
                application?.hours_since_posted !== undefined && (
                  <> · applied {application.hours_since_posted.toFixed(1)}h after posting</>
                )}
              {application?.via_referral ? " · via referral" : ""}
            </div>
          </div>

          <div className="card">
            <div className="muted small" style={{ marginBottom: 8 }}>
              Referral contacts
            </div>
            {referrals.length === 0 ? (
              <div className="muted small">
                None at {company || "this company"}. Import your LinkedIn
                connections export with{" "}
                <code>python scripts/import_connections.py</code> (phase 5).
              </div>
            ) : (
              referrals.map((c: any) => (
                <div key={c.id} style={{ marginBottom: 4 }}>
                  <span className={`tag${c.is_batchmate ? " batchmate" : ""}`}>
                    {c.full_name}
                  </span>
                  <span className="muted small">{c.title ?? ""}</span>
                </div>
              ))
            )}
          </div>

          <div className="card">
            <div className="muted small" style={{ marginBottom: 8 }}>
              Screening answers
            </div>
            <Copyable label="Notice period" value={`${screening.notice_period_days} days`} />
            <Copyable label="Current CTC" value={String(screening.current_ctc)} />
            <Copyable label="Expected CTC" value={String(screening.expected_ctc)} />
            <Copyable label="Location preference" value={String(screening.location_preference)} />
            <Copyable
              label="Total experience"
              value={`${screening.years_experience_post_grad} years post-graduation (${screening.years_experience_incl_internship} including an 8-month full-time internship)`}
            />
            <div className="muted small" style={{ marginTop: 6 }}>
              Read verbatim from config/candidate_profile.yaml. Never generated.
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
