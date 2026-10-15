import "server-only";
import { select, effectivePostedAt, hoursSince, type QueueRow } from "./db";

const THRESHOLD = Number(process.env.PING_THRESHOLD ?? "6.5");

/**
 * Age ceiling for the queue, mirroring max_display_age_days in
 * config/settings.yaml — the looser of the two ceilings, not the scoring gate.
 *
 * Scoring is gated at 3 days so a stale req never costs an LLM call. This is 5,
 * and the gap is deliberate: scoring is not instant, so a job admitted at 2.9
 * days needs to stay visible long enough to actually be read. Setting this to
 * the scoring gate would hide jobs that were just paid for.
 *
 * The scorer's gate cannot replace this one either. It only ever applies to
 * jobs being scored for the first time — anything already scored keeps its
 * score, deliberately, so that tightening the rule does not erase work already
 * paid for. Old jobs scored under a previous rule therefore stay in the queue
 * forever unless the read side filters them too, which is exactly what was
 * showing 101-day-old postings.
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
  posted_at_precision: "timestamp" | "date" | null;
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
      "select=id,title,location,absolute_url,compensation,posted_at," +
        "posted_at_precision,first_seen_at," +
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
      posted_at_precision: job.posted_at_precision,
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

// --- pipeline status ----------------------------------------------------

type RunBoard = {
  company: string; ats: string; token: string;
  error?: string; transient?: boolean; suppressed_closes?: number;
};

type RunRow = {
  started_at: string;
  finished_at: string | null;
  jobs_seen: number | null;
  jobs_new: number | null;
  jobs_closed: number | null;
  trigger_source: string | null;
  duration_s: number | null;
  boards: RunBoard[] | null;
};

export type PipelineStatus = {
  lastRun: RunRow | null;
  lastRunAgeHours: number | null;
  runsLast24h: number;
  failingBoards: { label: string; error: string; transient: boolean }[];
  inactiveBoards: string[];
  providerMix: { provider: string; count: number }[];
  providerMixDays: number;
};

const PROVIDER_MIX_DAYS = 7;

/**
 * The status strip (docs/PLAN.md 1.2). Replaces a banner that gave advice for
 * a provider which has since been disabled and a `--model` flag that never
 * existed — the one piece of in-product operational guidance was wrong.
 *
 * Everything here is read from what the pipeline recorded, not inferred:
 * run_log for cadence and board failures, job_scores.provider for which LLM is
 * actually carrying the load. Before this, run_log.finished_at, jobs_closed and
 * job_scores.provider were written on every run and read by nothing (STATE.md
 * G12).
 *
 * Timestamps in query strings come from toISOString(), which ends in "Z". An
 * isoformat() "+00:00" would be decoded as a space and PostgREST would 400 —
 * the bug notify/alerts.py:_since already documents.
 */
export async function getStatus(): Promise<PipelineStatus> {
  const dayAgo = new Date(Date.now() - 86_400_000).toISOString();
  const mixSince = new Date(Date.now() - PROVIDER_MIX_DAYS * 86_400_000).toISOString();

  const [latest, recent, inactive, scored] = await Promise.all([
    select<RunRow>(
      "run_log",
      "select=started_at,finished_at,jobs_seen,jobs_new,jobs_closed," +
        "trigger_source,duration_s,boards&worker=eq.ingest" +
        "&order=started_at.desc&limit=1",
    ),
    select<{ started_at: string }>(
      "run_log", `select=started_at&worker=eq.ingest&started_at=gte.${dayAgo}`,
    ),
    select<{ name: string }>("companies", "select=name&active=is.false&order=name.asc"),
    select<{ provider: string | null }>(
      "job_scores",
      `select=provider&fit_score=not.is.null&scored_at=gte.${mixSince}`,
    ),
  ]);

  const lastRun = latest[0] ?? null;
  const failingBoards = (lastRun?.boards ?? [])
    .filter((b) => b.error)
    .map((b) => ({
      label: `${b.company} (${b.ats})`,
      error: b.error ?? "",
      transient: Boolean(b.transient),
    }));

  const mix = new Map<string, number>();
  for (const s of scored) {
    const key = s.provider ?? "unrecorded";
    mix.set(key, (mix.get(key) ?? 0) + 1);
  }

  return {
    lastRun,
    lastRunAgeHours: lastRun ? hoursSince(new Date(lastRun.started_at)) : null,
    runsLast24h: recent.length,
    failingBoards,
    inactiveBoards: inactive.map((c) => c.name),
    providerMix: [...mix.entries()]
      .map(([provider, count]) => ({ provider, count }))
      .sort((a, b) => b.count - a.count),
    providerMixDays: PROVIDER_MIX_DAYS,
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

function latencyOf(hours: number): string {
  return hours < 6 ? "<6h" : hours <= 48 ? "6-48h" : ">48h";
}

/**
 * Apply-latency bucket for the funnel (docs/PLAN.md 1.4, gap G8).
 *
 * `hours_since_posted` is frozen at apply time from posted_at. For a Workday
 * job posted_at is a calendar day stored as midnight UTC, so the real posting
 * happened somewhere in the 24 hours after it, and the true latency lies in
 * (hours - 24, hours]. Throwing those rows away would drop every Workday
 * application; trusting the raw number would misfile, say, a 3-hour apply as
 * 27. Instead: if the whole interval lands in one bucket the bucket is certain
 * and is used; only an interval straddling a boundary goes to
 * "day-precision", reported on its own rather than guessed.
 */
function latencyBucket(hours: number | null, dateOnly: boolean): string {
  if (hours === null) return "unknown";
  if (!dateOnly) return latencyOf(hours);
  const lo = latencyOf(Math.max(0, hours - 24) + 1e-9);   // lower end is open
  return lo === latencyOf(hours) ? lo : "day-precision";
}

export async function getFunnel() {
  const [apps, scores, jobs] = await Promise.all([
    select<Application>("applications", "select=*"),
    select<ScoreRow & { scored_at: string }>(
      "job_scores",
      "select=job_id,fit_score,min_years,gap_skills,scored_at",
    ),
    select<{ id: number; posted_at: string | null; first_seen_at: string;
             posted_at_precision: "timestamp" | "date" | null }>(
      "jobs",
      "select=id,posted_at,first_seen_at,posted_at_precision",
    ),
  ]);

  const scoreByJob = new Map(scores.map((s) => [s.job_id, s]));
  const dateOnlyJobs = new Set(
    jobs.filter((j) => j.posted_at_precision === "date").map((j) => j.id),
  );
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
      latencyKey: latencyBucket(hours, dateOnlyJobs.has(a.job_id)),
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
    ["<6h", "6-48h", ">48h", "day-precision", "unknown"],
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
