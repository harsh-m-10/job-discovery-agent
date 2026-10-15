import "server-only";

/**
 * PostgREST access. Server-side only.
 *
 * The service-role key bypasses row-level security, so it must never reach the
 * browser bundle. `server-only` makes an accidental client import a build
 * error rather than a silent leak.
 */

const URL_BASE = (process.env.SUPABASE_URL ?? "").replace(/\/$/, "");
const KEY = process.env.SUPABASE_SERVICE_KEY ?? "";

function headers(extra: Record<string, string> = {}): Record<string, string> {
  if (!URL_BASE || !KEY) {
    throw new Error("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set");
  }
  return {
    apikey: KEY,
    Authorization: `Bearer ${KEY}`,
    "Content-Type": "application/json",
    ...extra,
  };
}

/** PostgREST caps one response at 1000 rows, regardless of what Range asks for. */
const PAGE = 1000;

/** Guard against an unbounded loop if a filter ever stops narrowing. 50k rows
 *  is far past anything this dashboard should be rendering. */
const MAX_PAGES = 50;

/**
 * Paged SELECT.
 *
 * This used to be a single request, which silently returned the first 1000
 * rows and nothing else. Measured 2026-10-15: `jobs?closed_at=is.null` had
 * 2,265 rows, so the queue was assembled against 44% of the open corpus.
 * `getQueue` iterates scores and looks each job up in that map, so a job
 * outside the first page was indistinguishable from one closed since scoring
 * and was dropped — 29 scored postings were invisible, including a 9.5, two
 * 9.0s and three 8.5s. Every count in `getQueueMeta` was wrong the same way,
 * and `referralIndex` lost 118 of 1,118 contacts.
 */
/**
 * Unique key per table, appended to every paged query's ORDER BY. Mirrors
 * common/paging.py. LIMIT/OFFSET pages are only consistent under a unique
 * order; without one Postgres may return rows in a different order per page,
 * so pages overlap and other rows vanish. Bit the pipeline on 2026-10-15 once
 * reads exceeded one page; with 4,000+ open jobs every dashboard read of jobs
 * or job_scores is several pages.
 */
const PRIMARY_KEY: Record<string, string> = {
  jobs: "id", companies: "id", job_scores: "job_id", connections: "id",
  applications: "id", notifications: "id", run_log: "id",
  application_events: "id", referral_asks: "id", v_funnel: "application_id",
};

export function stableOrder(table: string, query: string): string {
  const pk = PRIMARY_KEY[table];
  if (!pk) return query;
  const parts = query ? query.split("&") : [];
  const i = parts.findIndex((p) => p.startsWith("order="));
  if (i === -1) return [...parts, `order=${pk}.asc`].join("&");
  const cols = parts[i].slice("order=".length).split(",").map((c) => c.split(".")[0]);
  if (!cols.includes(pk)) parts[i] = `${parts[i]},${pk}.asc`;
  return parts.join("&");
}

export async function select<T = any>(table: string, query: string): Promise<T[]> {
  query = stableOrder(table, query);
  const rows: T[] = [];
  for (let page = 0; page < MAX_PAGES; page++) {
    const offset = page * PAGE;
    const res = await fetch(`${URL_BASE}/rest/v1/${table}?${query}`, {
      headers: headers({
        "Range-Unit": "items",
        Range: `${offset}-${offset + PAGE - 1}`,
      }),
      cache: "no-store",
    });
    if (!res.ok) throw new Error(`select ${table}: ${res.status} ${await res.text()}`);
    const batch = (await res.json()) as T[];
    rows.push(...batch);
    if (batch.length < PAGE) return rows;
  }
  throw new Error(
    `select ${table}: still returning full pages after ${MAX_PAGES * PAGE} rows — ` +
      `the query is probably missing a filter`,
  );
}

export async function upsert(table: string, rows: unknown[], onConflict: string) {
  const res = await fetch(
    `${URL_BASE}/rest/v1/${table}?on_conflict=${onConflict}`,
    {
      method: "POST",
      headers: headers({ Prefer: "resolution=merge-duplicates,return=representation" }),
      body: JSON.stringify(rows),
    },
  );
  if (!res.ok) throw new Error(`upsert ${table}: ${res.status} ${await res.text()}`);
  return res.json();
}

/** INSERT ... ON CONFLICT DO NOTHING. -> number of rows actually inserted. */
export async function insertIgnoringDuplicates(
  table: string, rows: unknown[], onConflict: string,
): Promise<number> {
  const res = await fetch(
    `${URL_BASE}/rest/v1/${table}?on_conflict=${onConflict}`,
    {
      method: "POST",
      headers: headers({ Prefer: "resolution=ignore-duplicates,return=representation" }),
      body: JSON.stringify(rows),
    },
  );
  if (!res.ok) throw new Error(`insert ${table}: ${res.status} ${await res.text()}`);
  return ((await res.json()) as unknown[]).length;
}

export async function patch(table: string, query: string, body: unknown) {
  const res = await fetch(`${URL_BASE}/rest/v1/${table}?${query}`, {
    method: "PATCH",
    headers: headers({ Prefer: "return=representation" }),
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`patch ${table}: ${res.status} ${await res.text()}`);
  return res.json();
}

// --- shared shapes ------------------------------------------------------

export type QueueRow = {
  job_id: number;
  title: string;
  company: string;
  location: string | null;
  absolute_url: string;
  compensation: string | null;
  headcount_band: string | null;
  posted_at: string | null;
  posted_at_precision: "timestamp" | "date" | null;
  first_seen_at: string;
  fit_score: number | null;
  verdict: string;
  min_years: number | null;
  max_years: number | null;
  matched_skills: string[] | null;
  gap_skills: string[] | null;
  reasoning: string | null;
  status: string;
  referrals: { id: number; full_name: string; title: string | null; is_batchmate: boolean }[];
};

/** Effective posting time: the ATS date when it gave one, else our first sighting. */
export function effectivePostedAt(row: { posted_at: string | null; first_seen_at: string }): Date {
  return new Date(row.posted_at ?? row.first_seen_at);
}

export function hoursSince(date: Date): number {
  return (Date.now() - date.getTime()) / 3_600_000;
}

/** Age for a posted_at that is only a calendar day (Workday). The stored
 *  time is a midnight-UTC placeholder, so hours would be invented precision. */
export function formatDayAge(hours: number): string {
  const days = Math.floor(hours / 24);
  return days <= 0 ? "today" : `${days}d`;
}

export function formatAge(hours: number): string {
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))}m`;
  if (hours < 48) return `${Math.round(hours)}h`;
  return `${Math.round(hours / 24)}d`;
}
