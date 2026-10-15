import { NextResponse } from "next/server";
import { insertIgnoringDuplicates } from "@/lib/db";

export const dynamic = "force-dynamic";

/**
 * Job-alert email intake (PLAN 6.7, decided 2026-10-15). apps_script/outcomes.gs
 * forwards every mail under the Gmail label job/alerts -- LinkedIn, Naukri and
 * Indeed alerts -- authenticated by x-dashboard-secret in middleware.ts.
 *
 * Stores only. Parsing happens in Python (ingest/alert_parse.py) on the next
 * ingest run, where a template change is fixed by a tested commit rather than
 * an Apps Script redeploy. message_id is unique, so re-posting is harmless.
 *
 * Body: { alerts: [{ message_id, received_at, sender, subject, body }] }
 */

const MAX_ALERTS = 50;
// Alert mails are HTML and long; 400k chars covers the largest seen templates
// with room, and caps what a stray post can store.
const MAX_BODY = 400_000;
const clip = (v: unknown, n: number) =>
  typeof v === "string" ? v.slice(0, n) : null;

export async function POST(request: Request) {
  let payload: any;
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }
  const alerts: any[] = Array.isArray(payload?.alerts) ? payload.alerts : [];
  if (alerts.length === 0 || alerts.length > MAX_ALERTS) {
    return NextResponse.json({ error: `send 1..${MAX_ALERTS} alerts` }, { status: 400 });
  }
  const rows = [];
  for (const a of alerts) {
    const messageId = clip(a?.message_id, 200);
    const received = new Date(String(a?.received_at ?? ""));
    if (!messageId || Number.isNaN(received.getTime())) {
      return NextResponse.json(
        { error: "every alert needs message_id and a valid received_at" }, { status: 400 });
    }
    rows.push({
      message_id: messageId,
      received_at: received.toISOString(),
      sender: clip(a?.sender, 300),
      subject: clip(a?.subject, 300),
      body: clip(a?.body, MAX_BODY),
    });
  }
  const stored = await insertIgnoringDuplicates("alert_inbox", rows, "message_id");
  return NextResponse.json({
    ok: true, received: rows.length, stored, duplicates: rows.length - stored,
  });
}
