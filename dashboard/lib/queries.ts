import "server-only";
import { select, effectivePostedAt, hoursSince, type QueueRow } from "./db";

const THRESHOLD = Number(process.env.PING_THRESHOLD ?? "6.5");

/**
 * Age ceiling for the queue, mirroring max_age_days in config/settings.yaml.
 *
 * The scorer's gate and this one do different jobs and neither replaces the
 * other. That gate stops old postings reaching a paid LLM call, but it only
 * ever applies to jobs being scored for the first time — anything already
 * scored keeps its score, deliberately, so that tightening the rule does not
 * erase work already paid for. The consequence is that old jobs scored under
 * the previous rule stay in the queue forever unless the read side filters
 * them too, which is exactly what was showing 101-day-old postings.
 *
 * 0 disables the filter.
 */
const MAX_AGE_DAYS = Number(process.env.MAX_AGE_DAYS ?? "5");

/** Company-name normalization mirroring score/referral.py (spec §7.3). */
function normalizeCompany(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9 ]/g, " ")
    .replace(
      /\b(india|global tech|technologies|technology|labs|inc|ltd|pvt|private|limited|llc|corp|solutions|services|gcc|r&d|development cent(er|re))\b/g,
      " ",
    )
    .replace(/\s+/g, " ")
    .trim();
}

type JobRow = {
  id: number;
  title: string;
  location: string | null;
  absolute_url: string;
  compensation: string | null;
  posted_at: string | null;
  first_seen_at: string;
  description?: string;
  companies: { name: string; category: string | null; headcount_band: string | null } | null;
};

type ScoreRow = {
  job_id: number;
  fit_score: number | null;
  verdict: string;
  min_years: number | null;
  max_years: number | null;
  matched_skills: string[] | null;
  gap_skills: string[] | null;
  reasoning: string | null;
};

export type Application = {
  job_id: number;
  status: string;
  applied_at: string | null;
  hours_since_posted: number | null;
  via_referral: boolean | null;
  responded_at: string | null;
  notes: string | null;
};

async function referralIndex() {
  const contacts = await select<{
    id: number; full_name: string; company_norm: string | null;
    title: string | null; is_batchmate: boolean | null;
  }>("connections", "select=id,full_name,company_norm,title,is_batchmate");

  const byCompany = new Map<string, typeof contacts>();
  for (const contact of contacts) {
    const key = contact.company_norm ?? "";
    if (!key) continue;
    if (!byCompany.has(key)) byCompany.set(key, []);
    byCompany.get(key)!.push(contact);
  }
  return byCompany;
}

/** Days since the ATS posting date, falling back to our first sighting. */
function ageDays(row: { posted_at: string | null; first_seen_at: string }): number {
  return hoursSince(effectivePostedAt(row)) / 24;
}

function tooOld(row: { posted_at: string | null; first_seen_at: string }): boolean {
  return MAX_AGE_DAYS > 0 && ageDays(row) > MAX_AGE_DAYS;
}

/**
 * The queue: everything scored at or above the ping threshold that has not been
 * actioned yet and is not stale. A job with no `applications` row counts as
 * queued — rows are only created when a button is pressed, so absence means
 * untouched.
 */
export async function getQueue(): Promise<QueueRow[]> {
  const [jobs, scores, apps, referrals] = await Promise.all([
    select<JobRow>(
      "jobs",
      "select=id,title,location,absolute_url,compensation,posted_at,first_seen_at," +
        "companies(name,category,headcount_band)&closed_at=is.null",
    ),
    select<ScoreRow>(
      "job_scores",
      "select=job_id,fit_score,verdict,min_years,max_years,matched_skills," +
        `gap_skills,reasoning&fit_score=gte.${THRESHOLD}`,
    ),
    select<Application>("applications", "select=job_id,status"),
    referralIndex(),
  ]);

  const jobById = new Map(jobs.map((j) => [j.id, j]));
  const statusByJob = new Map(apps.map((a) => [a.job_id, a.status]));

  const rows: QueueRow[] = [];
  for (const score of scores) {
    const job = jobById.get(score.job_id);
    if (!job) continue;                                   // closed since scoring
    const status = statusByJob.get(score.job_id) ?? "queued";
    if (status !== "queued") continue;
    if (tooOld(job)) continue;                            // stale, counted in meta

    const company = job.companies?.name ?? "";
    const contacts = referrals.get(normalizeCompany(company)) ?? [];
    rows.push({
      job_id: job.id,
      title: job.title,
      company,
      location: job.location,
      absolute_url: job.absolute_url,
      compensation: job.compensation,
      headcount_band: job.companies?.headcount_band ?? null,
      posted_at: job.posted_at,
      first_seen_at: job.first_seen_at,
      fit_score: score.fit_score,
      verdict: score.verdict,
      min_years: score.min_years,
      max_years: score.max_years,
      matched_skills: score.matched_skills,
      gap_skills: score.gap_skills,
      reasoning: score.reasoning,
      status,
      referrals: contacts
        .sort((a, b) => Number(b.is_batchmate) - Number(a.is_batchmate))
        .slice(0, 3)
        .map((c) => ({
          id: c.id, full_name: c.full_name, title: c.title,
          is_batchmate: Boolean(c.is_batchmate),
        })),
    });
  }

  rows.sort((a, b) => (b.fit_score ?? 0) - (a.fit_score ?? 0));
  return rows;
}

/**
 * Header numbers for the queue.
 *
 * `unscored` is the important one: an empty queue because nothing scored well
 * and an empty queue because scoring never finished look identical otherwise,
 * and the second silently looks like "no jobs today".
 */
export async function getQueueMeta() {
  const [jobs, scores, apps] = await Promise.all([
    select<{ id: number; posted_at: string | null; first_seen_at: string }>(
      "jobs", "select=id,posted_at,first_seen_at&closed_at=is.null",
    ),
    select<{ job_id: number; fit_score: number | null; reject_reason: string | null }>(
      "job_scores", "select=job_id,fit_score,reject_reason",
    ),
    select<{ applied_at: string | null }>(
      "applications", "select=applied_at&applied_at=not.is.null",
    ),
  ]);

  const scoredIds = new Set(scores.map((s) => s.job_id));
  const weekAgo = Date.now() - 7 * 86_400_000;

  // Above threshold but suppressed for age. Reported rather than dropped
  // silently: a queue that shrank from 62 to 12 with no explanation reads as a
  // broken pipeline, which is the same failure `unscored` exists to prevent.
  const overThreshold = new Set(
    scores.filter((s) => s.fit_score !== null && Number(s.fit_score) >= THRESHOLD)
      .map((s) => s.job_id),
  );
  const hiddenByAge = jobs.filter(
    (j) => overThreshold.has(j.id) && tooOld(j),
  ).length;

  return {
    openJobs: jobs.length,
    hiddenByAge,
    maxAgeDays: MAX_AGE_DAYS,
    unscored: jobs.filter((j) => !scoredIds.has(j.id)).length,
    // Jobs the LLM never got to because every provider refused. Distinct from
    // "not scored yet": these will not be retried by an ordinary run.
    stranded: scores.filter((s) =>
      (s.reject_reason ?? "").startsWith("scoring_failed")).length,
    llmScored: scores.filter((s) => s.fit_score !== null).length,
    appliedThisWeek: apps.filter(
      (a) => a.applied_at && new Date(a.applied_at).getTime() >= weekAgo,
    ).length,
    appliedTotal: apps.length,
  };
}

export async function getJob(jobId: number) {
  const [jobs, scores, apps, referrals] = await Promise.all([
    select<JobRow & {
      description: string | null; ats_job_id: string; closed_at: string | null;
    }>(
      "jobs",
      "select=id,ats_job_id,title,location,description,absolute_url,compensation," +
        `posted_at,first_seen_at,closed_at,companies(name,category,headcount_band)&id=eq.${jobId}`,
    ),
    select<ScoreRow & { model: string | null; reject_reason: string | null }>(
      "job_scores", `select=*&job_id=eq.${jobId}`,
    ),
    select<Application>("applications", `select=*&job_id=eq.${jobId}`),
    referralIndex(),
  ]);
  const job = jobs[0];
  if (!job) return null;
  const company = job.companies?.name ?? "";
  return {
    job,
    score: scores[0] ?? null,
    application: apps[0] ?? null,
    referrals: (referrals.get(normalizeCompany(company)) ?? []).slice(0, 5),
  };
}

// --- funnel -------------------------------------------------------------

export type Bucket = { label: string; applied: number; responded: number };

const RESPONDED = new Set(["responded", "interviewing", "offer"]);

function bucketise(
  rows: { applied: boolean; responded: boolean; key: string }[],
  order: string[],
): Bucket[] {
  return order.map((label) => {
    const matching = rows.filter((r) => r.key === label);
    return {
      label,
      applied: matching.filter((r) => r.applied).length,
      responded: matching.filter((r) => r.responded).length,
    };
  });
}

export async function getFunnel() {
  const [apps, scores, jobs] = await Promise.all([
    select<Application>("applications", "select=*"),
    select<ScoreRow & { scored_at: string }>(
      "job_scores",
      "select=job_id,fit_score,min_years,gap_skills,scored_at",
    ),
    select<{ id: number; posted_at: string | null; first_seen_at: string }>(
      "jobs",
      "select=id,posted_at,first_seen_at",
    ),
  ]);

  const scoreByJob = new Map(scores.map((s) => [s.job_id, s]));
  const applied = apps.filter((a) => a.applied_at !== null);
  const responded = applied.filter(
    (a) => a.responded_at !== null || RESPONDED.has(a.status),
  );

  const rows = applied.map((a) => {
    const score = scoreByJob.get(a.job_id);
    const minYears = score?.min_years ?? null;
    const hours = a.hours_since_posted;
    return {
      applied: true,
      responded: a.responded_at !== null || RESPONDED.has(a.status),
      referral: Boolean(a.via_referral),
      expKey:
        minYears === null ? "unstated" :
        minYears < 2 ? "0-2y" : minYears < 4 ? "2-4y" : "4y+",
      latencyKey:
        hours === null ? "unknown" :
        hours < 6 ? "<6h" : hours <= 48 ? "6-48h" : ">48h",
    };
  });

  const channel = bucketise(
    rows.map((r) => ({ ...r, key: r.referral ? "referral" : "cold" })),
    ["referral", "cold"],
  );
  const experience = bucketise(
    rows.map((r) => ({ ...r, key: r.expKey })),
    ["0-2y", "2-4y", "4y+", "unstated"],
  );
  const latency = bucketise(
    rows.map((r) => ({ ...r, key: r.latencyKey })),
    ["<6h", "6-48h", ">48h", "unknown"],
  );

  // Calibration: share of this week's scored jobs at 8+. Drifting above ~20%
  // means the prompt has stopped discriminating and the threshold is noise.
  const weekAgo = Date.now() - 7 * 86_400_000;
  const scoredThisWeek = scores.filter(
    (s) => s.fit_score !== null && new Date(s.scored_at).getTime() >= weekAgo,
  );
  const highThisWeek = scoredThisWeek.filter((s) => Number(s.fit_score) >= 8).length;

  // Skill gaps across everything scored 7+ in the last 30 days. The only view
  // here with compounding value: it says what to learn, not what to apply to.
  const monthAgo = Date.now() - 30 * 86_400_000;
  const gapCounts = new Map<string, number>();
  for (const score of scores) {
    if (score.fit_score === null || Number(score.fit_score) < 7) continue;
    if (new Date(score.scored_at).getTime() < monthAgo) continue;
    for (const gap of score.gap_skills ?? []) {
      const key = gap.trim().toLowerCase();
      if (key) gapCounts.set(key, (gapCounts.get(key) ?? 0) + 1);
    }
  }
  const skillGaps = [...gapCounts.entries()]
    .map(([skill, count]) => ({ skill, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 20);

  return {
    totals: {
      applied: applied.length,
      responded: responded.length,
      queued: apps.filter((a) => a.status === "queued").length,
      skipped: apps.filter((a) => a.status === "skipped").length,
      openJobs: jobs.length,
    },
    channel,
    experience,
    latency,
    calibration: {
      scored: scoredThisWeek.length,
      high: highThisWeek,
      pct: scoredThisWeek.length ? (highThisWeek / scoredThisWeek.length) * 100 : 0,
    },
    skillGaps,
  };
}

export async function getCompanies() {
  return select(
    "companies",
    "select=id,name,ats,board_token,category,priority,active,last_ok_at," +
      "consecutive_failures&order=active.desc,consecutive_failures.desc,name.asc",
  );
}
