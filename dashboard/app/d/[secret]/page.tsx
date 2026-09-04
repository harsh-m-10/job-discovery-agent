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

      {/* Same reasoning as the banner above: a queue that quietly shrank from
          62 to 12 reads as a broken pipeline. Say what was suppressed and how
          to see it. */}
      {meta.hiddenByAge > 0 && (
        <div className="banner">
          <svg className="banner-icon" width="15" height="15" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 7v5l3 2" />
          </svg>
          <div>
            <b>{meta.hiddenByAge} match(es) hidden — posted more than{" "}
            {meta.maxAgeDays} days ago.</b> They are still scored and stored, just
            kept out of the queue. Change <code>MAX_AGE_DAYS</code> in the
            dashboard environment to widen the window, or set it to{" "}
            <code>0</code> to show everything.
          </div>
        </div>
      )}

      {meta.stranded > 0 && (
        <div className="banner" data-kind="error">
          <svg className="banner-icon" width="15" height="15" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
            <path d="M12 9v4M12 17v.01" />
            <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
          </svg>
          <div>
            <b>{meta.stranded} job(s) failed to score — every LLM provider refused.</b>{" "}
            These are not in the queue and an ordinary run will not pick them up.
            Check provider health with <code>python -m score.healthcheck</code>,
            then recover with <code>python -m score.run --retry-failed</code>.
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
