import { NextResponse } from "next/server";
import { select, upsert, effectivePostedAt, hoursSince } from "@/lib/db";

export const dynamic = "force-dynamic";

const ALLOWED = new Set([
  "queued", "applied", "skipped", "responded", "interviewing", "rejected", "offer",
]);
const RESPONDED = new Set(["responded", "interviewing", "offer"]);

/**
 * Status write-back (spec §9.2).
 *
 * The one thing this endpoint must get right is `hours_since_posted`: it is
 * snapshotted the first time a job is marked applied and never recomputed.
 * Recalculating it later would silently turn the latency edge — the entire
 * hypothesis under test — into a measurement of how long ago the job posted,
 * which is a different and useless number.
 */
export async function POST(request: Request) {
  let body: any;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }

  const jobId = Number(body?.job_id);
  const status = String(body?.status ?? "");
  if (!Number.isInteger(jobId) || jobId <= 0) {
    return NextResponse.json({ error: "job_id required" }, { status: 400 });
  }
  if (!ALLOWED.has(status)) {
    return NextResponse.json({ error: `bad status: ${status}` }, { status: 400 });
  }

  const [jobs, existing] = await Promise.all([
    select<{ id: number; posted_at: string | null; first_seen_at: string }>(
      "jobs",
      `select=id,posted_at,first_seen_at&id=eq.${jobId}`,
    ),
    select<{
      job_id: number; status: string; applied_at: string | null;
      hours_since_posted: number | null; responded_at: string | null;
      via_referral: boolean | null; notes: string | null;
    }>("applications", `select=*&job_id=eq.${jobId}`),
  ]);

  const job = jobs[0];
  if (!job) return NextResponse.json({ error: "unknown job" }, { status: 404 });
  const prior = existing[0] ?? null;
  const now = new Date().toISOString();

  const row: Record<string, unknown> = {
    job_id: jobId,
    status,
    updated_at: now,
    applied_at: prior?.applied_at ?? null,
    hours_since_posted: prior?.hours_since_posted ?? null,
    responded_at: prior?.responded_at ?? null,
    via_referral:
      body?.via_referral === undefined
        ? (prior?.via_referral ?? false)
        : Boolean(body.via_referral),
    notes: body?.notes === undefined ? (prior?.notes ?? null) : String(body.notes),
  };

  if (status === "applied" && !prior?.applied_at) {
    row.applied_at = now;
    row.hours_since_posted = Number(hoursSince(effectivePostedAt(job)).toFixed(2));
  }

  // Any outcome past "applied" implies a reply landed, including a rejection —
  // a rejection is a response, and treating it otherwise would inflate the
  // response rate by dropping the negative cases.
  if ((RESPONDED.has(status) || status === "rejected") && !prior?.responded_at) {
    row.responded_at = now;
  }

  const [saved] = await upsert("applications", [row], "job_id");
  return NextResponse.json({ ok: true, application: saved });
}
