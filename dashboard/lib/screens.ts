import "server-only";
import { select, selectPage, inList, effectivePostedAt, hoursSince, formatAge, formatDayAge } from "./db";
import { CORE_FAMILIES, MAX_DISPLAY_AGE_DAYS, PING_THRESHOLD, SECONDARY_FAMILIES } from "./settings.generated";

/**
 * Loaders for the spec 002 screens. Every list is lean (card fields only) and
 * bounded; headline numbers come from the snapshot (lib/queries.ts).
 */

// --- stages --------------------------------------------------------------

export const STAGES = [
  { id: "applied", label: "Applied" },
  { id: "screen", label: "Screen" },
  { id: "assessment", label: "Assessment" },
  { id: "interviewing", label: "Interview" },
  { id: "offer", label: "Offer" },
  { id: "rejected", label: "Rejected" },
] as const;

/** "responded" predates 0029; it is a screen. */
export function stageOf(status: string): string {
  return status === "responded" ? "screen" : status;
}

export function stageLabel(status: string): string {
  return STAGES.find((s) => s.id === stageOf(status))?.label ?? status;
}

const LIVE = ["screen", "responded", "assessment", "interviewing", "offer"];

// --- TODAY ----------------------------------------------------------------

export type InProgress = {
  job_id: number; company: string; role: string; status: string; stage: string;
  next_step: string | null; next_step_at: string | null; updated_at: string | null;
};

export async function getInProgress(): Promise<InProgress[]> {
  const rows = await select<{
    job_id: number; status: string; next_step: string | null; next_step_at: string | null;
    updated_at: string | null; jobs: { title: string; companies: { name: string } | null } | null;
  }>(
    "applications",
    "select=job_id,status,next_step,next_step_at,updated_at,jobs(title,companies(name))" +
      `&status=in.(${LIVE.join(",")})&order=next_step_at.asc.nullslast,updated_at.desc`,
  );
  return rows.map((r) => ({
    job_id: r.job_id,
    company: r.jobs?.companies?.name ?? "?",
    role: r.jobs?.title ?? "?",
    status: r.status,
    stage: stageLabel(r.status),
    next_step: r.next_step,
    next_step_at: r.next_step_at,
    updated_at: r.updated_at,
  }));
}

// --- jobs (shared by TODAY's "New for you" and JOBS) ------------------------

export type JobItem = {
  job_id: number; title: string; company: string; absolute_url: string;
  tier: number | null; source: string; sourceLabel: string; ageLabel: string; ageDays: number;
  score: number | null; reason: string; family: string;
  contacts: number; warm: string | null;
};

type Row = {
  job_id: number; title: string; company: string; company_key: string | null; absolute_url: string;
  posted_at: string | null; posted_at_precision: string | null; first_seen_at: string;
  role_family: string | null; fit_score: number | null; reasoning: string | null;
  source: string | null; company_tier: number | null; age_days: number;
};

const FIELDS =
  "job_id,title,company,company_key,absolute_url,posted_at,posted_at_precision,first_seen_at," +
  "role_family,fit_score,reasoning,source,company_tier,age_days";

const SOURCE_LABEL: Record<string, string> = { ats: "Direct", indeed: "Indeed", "alert-email": "Alert" };

/** First sentence of the scorer's reasoning: the card's one-line reason. */
function oneLine(text: string | null): string {
  const t = (text ?? "").trim();
  const end = t.search(/[.!?](\s|$)/);
  return end > 0 ? t.slice(0, end + 1) : t;
}

async function contactsFor(keys: string[]) {
  const wanted = [...new Set(keys.filter(Boolean))];
  const out = new Map<string, { n: number; warm: string | null }>();
  if (!wanted.length) return out;
  const rows = await select<{ company_norm: string; full_name: string; warm: boolean | null }>(
    "connections",
    `select=company_norm,full_name,warm&company_norm=in.(${inList(wanted)})&order=warm.desc`,
  );
  for (const r of rows) {
    const cur = out.get(r.company_norm) ?? { n: 0, warm: null };
    cur.n += 1;
    if (r.warm && !cur.warm) cur.warm = r.full_name;
    out.set(r.company_norm, cur);
  }
  return out;
}

async function toItems(rows: Row[]): Promise<JobItem[]> {
  const people = await contactsFor(rows.map((r) => r.company_key ?? ""));
  return rows.map((r) => {
    const hours = hoursSince(effectivePostedAt(r));
    const c = people.get(r.company_key ?? "");
    const src = r.source ?? "ats";
    return {
      job_id: r.job_id, title: r.title, company: r.company, absolute_url: r.absolute_url,
      tier: r.company_tier, source: src, sourceLabel: SOURCE_LABEL[src] ?? src,
      ageLabel: r.posted_at_precision === "date" ? formatDayAge(hours) : formatAge(hours),
      ageDays: Number(r.age_days), score: r.fit_score === null ? null : Number(r.fit_score),
      reason: oneLine(r.reasoning), family: r.role_family ?? "other",
      contacts: c?.n ?? 0, warm: c?.warm ?? null,
    };
  });
}

const BASE = `&fit_score=gte.${PING_THRESHOLD}&status=eq.queued&location_ok=is.true`;

/** Core + secondary matches first seen after `since` (TODAY, FR-003). */
export async function getNewSince(sinceIso: string): Promise<{ count: number; items: JobItem[] }> {
  const fams = [...CORE_FAMILIES, ...SECONDARY_FAMILIES].join(",");
  const { rows, hasMore } = await selectPage<Row>(
    "v_queue",
    `select=${FIELDS}${BASE}&role_family=in.(${fams})&first_seen_at=gt.${sinceIso}` +
      "&order=fit_score.desc,job_id.asc",
    0, 50,
  );
  return { count: hasMore ? 50 : rows.length, items: await toItems(rows.slice(0, 5)) };
}

export type JobFilters = { fam: string[]; src: string[]; tier: string[]; fresh: string | null };

export const FAMILY_GROUPS: Record<string, readonly string[]> = {
  core: CORE_FAMILIES,
  secondary: SECONDARY_FAMILIES,
  other: ["security", "other"],
};
const SOURCE_PARAM: Record<string, string> = { direct: "ats", indeed: "indeed", alert: "alert-email" };

function filterQuery(f: JobFilters): string {
  const families = f.fam.flatMap((g) => FAMILY_GROUPS[g] ?? []);
  let q = BASE;
  if (families.length) q += `&role_family=in.(${families.join(",")})`;
  const sources = f.src.map((s) => SOURCE_PARAM[s]).filter(Boolean);
  if (sources.length) q += `&source=in.(${sources.join(",")})`;
  const tiers = f.tier.map(Number).filter((n) => [1, 2, 3].includes(n));
  if (tiers.length) q += `&company_tier=in.(${tiers.join(",")})`;
  return q;
}

/** JOBS: the recent list (<= max_display_age_days, or the freshness chip) and
 *  the first page of older ones, with counts for both. */
export async function getJobs(f: JobFilters, olderPage = 1) {
  const q = filterQuery(f);
  const recentDays = f.fresh === "1" ? 1 : f.fresh === "3" ? 3 : MAX_DISPLAY_AGE_DAYS;
  const [recent, older] = await Promise.all([
    selectPage<Row>("v_queue", `select=${FIELDS}${q}&age_days=lte.${recentDays}` +
      "&order=fit_score.desc,job_id.asc", 0, 100),
    selectPage<Row>("v_queue", `select=${FIELDS}${q}&age_days=gt.${MAX_DISPLAY_AGE_DAYS}` +
      "&order=fit_score.desc,job_id.asc", (olderPage - 1) * 25, 25),
  ]);
  // Count older ones cheaply: ids only, bounded.
  const olderCount = older.hasMore
    ? (await selectPage<{ job_id: number }>("v_queue",
        `select=job_id${q}&age_days=gt.${MAX_DISPLAY_AGE_DAYS}`, 0, 500)).rows.length
    : (olderPage - 1) * 25 + older.rows.length;
  const [recentItems, olderItems] = await Promise.all([toItems(recent.rows), toItems(older.rows)]);
  return { recent: recentItems, recentMore: recent.hasMore, older: olderItems,
           olderMore: older.hasMore, olderCount, recentDays };
}

// --- PIPELINE ---------------------------------------------------------------

export type PipelineRow = {
  job_id: number; company: string; role: string; status: string; date: string | null;
  next_step: string | null; channel: string | null;
};

export async function getPipeline() {
  const [apps, funnel] = await Promise.all([
    select<{
      job_id: number; status: string; applied_at: string | null; updated_at: string | null;
      next_step: string | null; channel: string | null;
      jobs: { title: string; companies: { name: string } | null } | null;
    }>(
      "applications",
      "select=job_id,status,applied_at,updated_at,next_step,channel,jobs(title,companies(name))" +
        "&status=not.in.(queued,skipped)&order=updated_at.desc.nullslast",
    ),
    select<{
      status: string; applied_at: string | null; responded_at: string | null; channel: string | null;
      role_family: string | null; company_tier: number | null; interview_at: string | null;
      assessment_at: string | null;
    }>(
      "v_funnel",
      "select=status,applied_at,responded_at,channel,role_family,company_tier,interview_at,assessment_at" +
        "&applied_at=not.is.null",
    ),
  ]);

  const groups = new Map<string, PipelineRow[]>();
  for (const s of STAGES) groups.set(s.id, []);
  for (const a of apps) {
    const stage = stageOf(a.status);
    groups.get(stage)?.push({
      job_id: a.job_id, company: a.jobs?.companies?.name ?? "?", role: a.jobs?.title ?? "?",
      status: a.status, date: a.updated_at ?? a.applied_at, next_step: a.next_step, channel: a.channel,
    });
  }

  type Conv = { label: string; applied: number; replied: number; moved: number };
  const conv = (key: (r: (typeof funnel)[number]) => string) => {
    const m = new Map<string, Conv>();
    for (const r of funnel) {
      const k = key(r);
      const c = m.get(k) ?? { label: k, applied: 0, replied: 0, moved: 0 };
      c.applied += 1;
      if (r.responded_at || ["screen", "responded", "assessment", "interviewing", "offer", "rejected"]
        .includes(r.status)) c.replied += 1;
      if (r.interview_at || r.assessment_at || ["screen", "responded", "assessment", "interviewing", "offer"]
        .includes(r.status)) c.moved += 1;
      m.set(k, c);
    }
    return [...m.values()].sort((a, b) => b.applied - a.applied);
  };
  const CHANNEL: Record<string, string> = { direct: "Company site", indeed: "Indeed", linkedin: "LinkedIn", naukri: "Naukri" };

  return {
    stages: STAGES.map((s) => ({ ...s, rows: groups.get(s.id) ?? [] })),
    byChannel: conv((r) => CHANNEL[r.channel ?? ""] ?? "Unrecorded"),
    byFamily: conv((r) => r.role_family ?? "other"),
    byTier: conv((r) => (r.company_tier ? `Tier ${r.company_tier}` : "Untiered")),
  };
}

// --- PEOPLE -----------------------------------------------------------------

export async function getAskStats() {
  const asks = await select<{ outcome: string | null; replied_at: string | null }>(
    "referral_asks", "select=outcome,replied_at").catch(() => []);
  return {
    sent: asks.length,
    replied: asks.filter((a) => a.replied_at || a.outcome === "referred" || a.outcome === "declined").length,
    referred: asks.filter((a) => a.outcome === "referred").length,
  };
}
