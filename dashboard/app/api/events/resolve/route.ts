import { NextResponse } from "next/server";
import { patch } from "@/lib/db";

export const dynamic = "force-dynamic";

/**
 * Resolve one outcome email from the review queue (docs/PLAN.md 4.3).
 *
 * Only records the operator's answer -- which application it was, or "not
 * mine". It does not change the application's status: outcomes/run.py applies
 * the effect on the next run, through the same rule as automatically matched
 * emails, so there is exactly one place that decides what an email means.
 *
 * Body: { event_id, application_id }  or  { event_id, ignore: true }
 */
export async function POST(request: Request) {
  let body: any;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }
  const eventId = Number(body?.event_id);
  if (!Number.isInteger(eventId) || eventId <= 0) {
    return NextResponse.json({ error: "event_id required" }, { status: 400 });
  }

  let update: Record<string, unknown>;
  if (body?.ignore === true) {
    update = { match_state: "ignored", application_id: null, resolved_by: "operator" };
  } else {
    const appId = Number(body?.application_id);
    if (!Number.isInteger(appId) || appId <= 0) {
      return NextResponse.json({ error: "application_id or ignore required" }, { status: 400 });
    }
    update = {
      match_state: "matched", application_id: appId, resolved_by: "operator",
      effect_applied: false,
    };
  }

  // Only rows still awaiting a decision can be resolved, so a double click or
  // a stale page cannot re-open something already settled.
  const saved = await patch(
    "application_events",
    `id=eq.${eventId}&match_state=in.(needs_review,unmatched)`,
    update,
  );
  if (!Array.isArray(saved) || saved.length === 0) {
    return NextResponse.json({ error: "not awaiting review" }, { status: 409 });
  }
  return NextResponse.json({ ok: true });
}
