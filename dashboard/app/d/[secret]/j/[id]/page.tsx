import Link from "next/link";
import { notFound } from "next/navigation";
import { getJob } from "@/lib/queries";
import { effectivePostedAt, formatAge, hoursSince } from "@/lib/db";
import StatusButtons from "../../StatusButtons";
import Copyable from "./Copyable";
import { screening as loadScreening } from "@/lib/screening";

export const dynamic = "force-dynamic";

function tierOf(score: number | null): "high" | "mid" | "low" {
  if (score === null) return "low";
  if (score >= 8) return "high";
  if (score >= 7) return "mid";
  return "low";
}

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

  const screening = loadScreening();
  const ageHours = hoursSince(effectivePostedAt(job));
  const heat = ageHours < 6 ? "hot" : ageHours < 24 ? "warm" : ageHours < 48 ? "cool" : "cold";
  const company = job.companies?.name ?? "";
  const tier = tierOf(score?.fit_score ?? null);

  return (
    <>
      <div style={{ display: "flex", gap: 14, alignItems: "flex-start", marginBottom: 16 }}>
        <div className="score" data-tier={tier}>
          {score?.fit_score?.toFixed(1) ?? "—"}
          <span>FIT</span>
        </div>
        <div style={{ minWidth: 0, flex: 1 }}>
          <h1 style={{ margin: "2px 0 4px", fontSize: 19, letterSpacing: "-0.02em", lineHeight: 1.25 }}>
            {job.title}
          </h1>
          <div className="company">{company}</div>
          <div className="meta">
            <span className="fresh" data-heat={heat}>
              {heat === "hot" && <span className="live-dot" />}
              {formatAge(ageHours)} old
            </span>
            {job.location && <span className="dot">{job.location}</span>}
            {job.compensation && <span className="dot">{job.compensation}</span>}
            {job.closed_at && (
              <span className="dot" style={{ color: "var(--bad)" }}>closed on the board</span>
            )}
          </div>
        </div>
      </div>

      <div style={{ display: "flex", gap: 8, marginBottom: 14, flexWrap: "wrap" }}>
        <Link href={`/d/${secret}`} className="btn" style={{ display: "inline-flex", alignItems: "center" }}>
          ← Queue
        </Link>
        <a className="btn" style={{ display: "inline-flex", alignItems: "center" }}
           href={job.absolute_url} target="_blank" rel="noreferrer noopener">
          Open posting ↗
        </a>
      </div>

      <div className="grid2">
        <div>
          <section className="panel">
            <div className="panel-title">Job description</div>
            <pre className="jd">{job.description ?? "(no description captured)"}</pre>
          </section>
        </div>

        <div>
          <section className="panel">
            <div className="panel-title">Assessment</div>
            {score ? (
              <>
                <p className="reason" style={{ marginTop: 0 }}>{score.reasoning}</p>
                <div className="chips">
                  {(score.matched_skills ?? []).map((s: string) => (
                    <span className="chip" key={`m${s}`}>{s}</span>
                  ))}
                  {(score.gap_skills ?? []).map((s: string) => (
                    <span className="chip" data-kind="gap" key={`g${s}`}>{s}</span>
                  ))}
                </div>
                <div className="note">
                  {score.min_years !== null && score.max_years !== null
                    ? `Asks ${score.min_years}-${score.max_years} years`
                    : score.min_years !== null
                      ? `Asks ${score.min_years}+ years`
                      : "Experience unstated"}
                  {score.model ? ` · scored by ${score.model}` : ""}
                </div>
              </>
            ) : (
              <div style={{ color: "var(--text-3)", fontSize: 13 }}>Not scored yet.</div>
            )}
          </section>

          <section className="panel">
            <div className="panel-title">Status · {application?.status ?? "queued"}</div>
            <StatusButtons
              jobId={job.id}
              secret={secret}
              current={application?.status ?? "queued"}
            />
            {application?.hours_since_posted !== null &&
              application?.hours_since_posted !== undefined && (
                <div className="note">
                  Applied {application.hours_since_posted.toFixed(1)}h after posting
                  {application.via_referral ? " · via referral" : ""}
                </div>
              )}
          </section>

          <section className="panel">
            <div className="panel-title">Referral contacts</div>
            {referrals.length === 0 ? (
              <div style={{ color: "var(--text-3)", fontSize: 13 }}>
                None at {company || "this company"} yet. Import your LinkedIn
                connections export to populate this.
              </div>
            ) : (
              <div className="referral" style={{ marginTop: 0 }}>
                {referrals.map((c: any) => (
                  <span className="referral-name" key={c.id}>
                    {c.full_name}
                    {c.title && <span className="role"> · {c.title}</span>}
                    {c.is_batchmate && <span className="batch-pill">batchmate</span>}
                  </span>
                ))}
              </div>
            )}
          </section>

          <section className="panel">
            <div className="panel-title">Screening answers</div>
            {screening ? (
              <>
                <Copyable label="NOTICE PERIOD" value={`${screening.notice_period_days} days`} />
                <Copyable label="CURRENT CTC" value={String(screening.current_ctc)} />
                <Copyable label="EXPECTED CTC" value={String(screening.expected_ctc)} />
                <Copyable label="LOCATION" value={String(screening.location_preference)} />
                <Copyable
                  label="TOTAL EXPERIENCE"
                  value={`${screening.years_experience_post_grad} years post-graduation (${screening.years_experience_incl_internship} including an 8-month full-time internship)`}
                />
                <div className="note">
                  Read verbatim from the candidate profile. Never LLM-generated.
                </div>
              </>
            ) : (
              <div className="note">
                Not configured. Run <code>python scripts/sync_screening.py</code> and set
                SCREENING_JSON in the deployment environment.
              </div>
            )}
          </section>
        </div>
      </div>
    </>
  );
}
