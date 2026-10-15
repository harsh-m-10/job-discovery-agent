import { NextResponse } from "next/server";
import { insertIgnoringDuplicates } from "@/lib/db";

export const dynamic = "force-dynamic";

/**
 * Outcome email intake (docs/PLAN.md 4.3). Called by apps_script/outcomes.gs
 * from the operator's Gmail, authenticated by the x-dashboard-secret header in
 * proxy.ts like every /api route.
 *
 * Deliberately dumb: it validates and stores. Classification, matching and the
 * status change happen in Python (outcomes/run.py) on the next pipeline run,
 * so this route holds no business logic and the rules live where they are
 * tested. Re-posting the same message is harmless: message_id is unique and
 * duplicates are ignored, so the Apps Script can retry freely.
 *
 * Body: { events: [{ message_id, received_at, sender, subject, snippet }] }
 */

const MAX_EVENTS = 200;
const clip = (v: unknown, n: number) =>
  typeof v === "string" ? v.slice(0, n) : null;

export async function POST(request: Request) {
  let body: any;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }
  const events: any[] = Array.isArray(body?.events) ? body.events : [];
  if (events.length === 0 || events.length > MAX_EVENTS) {
    return NextResponse.json(
      { error: `send 1..${MAX_EVENTS} events` }, { status: 400 });
  }

  const rows = [];
  for (const e of events) {
    const messageId = clip(e?.message_id, 200);
    const received = new Date(String(e?.received_at ?? ""));
    if (!messageId || Number.isNaN(received.getTime())) {
      return NextResponse.json(
        { error: "every event needs message_id and a valid received_at" }, { status: 400 });
    }
    rows.push({
      message_id: messageId,
      received_at: received.toISOString(),
      sender: clip(e?.sender, 300),
      subject: clip(e?.subject, 300),
      // Gmail snippets are ~200 chars; the cap is against abuse, not content.
      snippet: clip(e?.snippet, 1000),
    });
  }

  const stored = await insertIgnoringDuplicates("application_events", rows, "message_id");
  return NextResponse.json({
    ok: true, received: rows.length, stored, duplicates: rows.length - stored,
  });
}
