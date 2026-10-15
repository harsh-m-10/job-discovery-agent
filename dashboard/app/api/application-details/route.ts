import { NextResponse } from "next/server";
import { patch } from "@/lib/db";

export const dynamic = "force-dynamic";

/**
 * What the application form asked, and what was answered (docs/PLAN.md 4.4).
 *
 * Notice period and CTC are common screen-outs for Indian roles, and the funnel
 * cannot test whether they are why applications die unless the answer given is
 * recorded per application. Every field is optional; blank clears it. Only an
 * application that exists (marked Applied) can carry details.
 *
 * Body: { job_id, resume_variant?, notice_period_days?, current_ctc_lpa?,
 *         expected_ctc_lpa?, yoe_stated?, relocation_ok? }
 */

type Rule = { min: number; max: number; integer?: boolean };
const NUMERIC: Record<string, Rule> = {
  notice_period_days: { min: 0, max: 365, integer: true },
  current_ctc_lpa: { min: 0, max: 1000 },
  expected_ctc_lpa: { min: 0, max: 1000 },
  yoe_stated: { min: 0, max: 50 },
};

export async function POST(request: Request) {
  let body: any;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }
  const jobId = Number(body?.job_id);
  if (!Number.isInteger(jobId) || jobId <= 0) {
    return NextResponse.json({ error: "job_id required" }, { status: 400 });
  }

  const update: Record<string, unknown> = {};
  for (const [field, rule] of Object.entries(NUMERIC)) {
    if (!(field in body)) continue;
    const raw = body[field];
    if (raw === null || raw === "") {
      update[field] = null;
      continue;
    }
    const n = Number(raw);
    if (!Number.isFinite(n) || n < rule.min || n > rule.max || (rule.integer && !Number.isInteger(n))) {
      return NextResponse.json({ error: `${field} out of range` }, { status: 400 });
    }
    update[field] = n;
  }
  if ("resume_variant" in body) {
    const v = typeof body.resume_variant === "string" ? body.resume_variant.trim().slice(0, 200) : "";
    update.resume_variant = v || null;
  }
  if ("relocation_ok" in body) {
    const v = body.relocation_ok;
    update.relocation_ok = v === true || v === false ? v : null;
  }
  if (Object.keys(update).length === 0) {
    return NextResponse.json({ error: "nothing to save" }, { status: 400 });
  }

  const saved = await patch("applications", `job_id=eq.${jobId}`, update);
  if (!Array.isArray(saved) || saved.length === 0) {
    return NextResponse.json({ error: "mark the job Applied first" }, { status: 409 });
  }
  return NextResponse.json({ ok: true });
}
