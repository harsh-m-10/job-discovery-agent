import { NextResponse } from "next/server";
import { patch } from "@/lib/db";

export const dynamic = "force-dynamic";

/**
 * Mark a contact warm, or not (db/migrations/0024). Warm contacts rank first
 * for referrals in pings and on the dashboard. Authenticated by
 * x-dashboard-secret in proxy.ts like every /api route.
 *
 * Body: { contact_id, warm: boolean }
 */
export async function POST(request: Request) {
  let body: any;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }
  const id = Number(body?.contact_id);
  if (!Number.isInteger(id) || id <= 0 || typeof body?.warm !== "boolean") {
    return NextResponse.json({ error: "contact_id and boolean warm required" }, { status: 400 });
  }
  const saved = await patch("connections", `id=eq.${id}`, {
    warm: body.warm,
    warm_set_at: new Date().toISOString(),
  });
  if (!Array.isArray(saved) || saved.length === 0) {
    return NextResponse.json({ error: "unknown contact" }, { status: 404 });
  }
  return NextResponse.json({ ok: true, warm: body.warm });
}
