import { getQueue, getQueueMeta } from "@/lib/queries";
import { effectivePostedAt, formatAge, hoursSince } from "@/lib/db";
import QueueList from "./QueueList";
import type { CardJob } from "./JobCard";

export const dynamic = "force-dynamic";

export default async function QueuePage({
  params,
}: {
  params: Promise<{ secret: string }>;
}) {
  const { secret } = await params;
  const [rows, meta] = await Promise.all([getQueue(), getQueueMeta()]);

  const jobs: CardJob[] = rows.map((row) => {
    const posted = effectivePostedAt(row);
    const ageHours = hoursSince(posted);
    return {
      job_id: row.job_id,
      title: row.title,
      company: row.company,
      location: row.location,
      absolute_url: row.absolute_url,
      compensation: row.compensation,
      headcountBand: row.headcount_band,
      fit_score: row.fit_score,
      min_years: row.min_years,
      max_years: row.max_years,
      matched_skills: row.matched_skills,
      gap_skills: row.gap_skills,
      reasoning: row.reasoning,
      ageHours,
      ageLabel: formatAge(ageHours),
      datedFromSighting: !row.posted_at,
      referrals: row.referrals,
    };
  });

  return (
    <>
      {/* An empty queue and an unfinished scoring pass look identical without
          this. The distinction matters daily: one means "nothing today", the
          other means "the pipeline stalled and you are flying blind". */}
      {meta.unscored > 0 && (
        <div className="banner">
          <svg className="banner-icon" width="15" height="15" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 8v5M12 16.5v.01" />
          </svg>
          <div>
            <b>{meta.unscored} of {meta.openJobs} postings are not scored yet</b> — this
            queue is incomplete, not empty. Groq&apos;s free tier allows 100,000
            tokens per day per model; run <code>python -m score.run</code> once it
            resets, or pass <code>--model openai/gpt-oss-120b --batch-size 2</code> to
            use a different model&apos;s separate daily budget.
          </div>
        </div>
      )}

      {jobs.length === 0 ? (
        <div className="empty">
          <div className="big">◦</div>
          {meta.unscored > 0
            ? "Nothing scored above threshold yet — finish the scoring pass above."
            : "Queue is clear. Every scored posting has been actioned."}
        </div>
      ) : (
        <QueueList jobs={jobs} secret={secret} />
      )}
    </>
  );
}
