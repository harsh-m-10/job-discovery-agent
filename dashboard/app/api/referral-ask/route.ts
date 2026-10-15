import { NextResponse } from "next/server";
import { insertIgnoringDuplicates, patch } from "@/lib/db";

export const dynamic = "force-dynamic";

/**
 * Log a referral request, or what came of it (docs/PLAN.md 5.4).
 *
 *   { job_id, contact_id }                     -> record that the ask was made
 *   { ask_id, outcome: referred|declined|no_reply }  -> record the answer
 *
 * Recording only. The message itself is copied to the operator's clipboard by
 * the page and sent by them; nothing is ever sent on their behalf.
 */
const OUTCOMES = new Set(["referred", "declined", "no_reply"]);

export async function POST(request: Request) {
  let body: any;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }

  if (body?.ask_id !== undefined) {
    const askId = Number(body.ask_id);
    const outcome = String(body?.outcome ?? "");
    if (!Number.isInteger(askId) || askId <= 0 || !OUTCOMES.has(outcome)) {
      return NextResponse.json({ error: "ask_id and a valid outcome required" }, { status: 400 });
    }
    const saved = await patch("referral_asks", `id=eq.${askId}`, {
      outcome,
      replied_at: outcome === "no_reply" ? null : new Date().toISOString(),
    });
    if (!Array.isArray(saved) || saved.length === 0) {
      return NextResponse.json({ error: "unknown ask" }, { status: 404 });
    }
    return NextResponse.json({ ok: true });
  }

  const jobId = Number(body?.job_id);
  const contactId = Number(body?.contact_id);
  if (!Number.isInteger(jobId) || jobId <= 0 || !Number.isInteger(contactId) || contactId <= 0) {
    return NextResponse.json({ error: "job_id and contact_id required" }, { status: 400 });
  }
  // Asking the same person twice about the same job is one ask.
  const stored = await insertIgnoringDuplicates(
    "referral_asks", [{ job_id: jobId, contact_id: contactId }], "contact_id,job_id");
  return NextResponse.json({ ok: true, new: stored === 1 });
}
