import { NextResponse } from "next/server";
import { select } from "@/lib/db";

export const dynamic = "force-dynamic";

/**
 * Pipeline liveness for the independent watchdog in apps_script/outcomes.gs
 * (docs/PLAN.md 9.2). Authenticated by x-dashboard-secret in proxy.ts.
 *
 * Reports the age of the newest *automated* ingest run: a manual spot check
 * with --only polls a handful of boards and must not make a dead pipeline look
 * alive. Any database failure is a 503, which the watchdog treats as a problem
 * too -- a paused Supabase project is exactly what it should notice.
 */
export async function GET() {
  try {
    const rows = await select<{ started_at: string; trigger_source: string | null }>(
      "run_log",
      "select=started_at,trigger_source&worker=eq.ingest&trigger_source=neq.manual" +
        "&order=started_at.desc&limit=1",
    );
    const last = rows[0] ?? null;
    return NextResponse.json({
      ok: true,
      last_run_at: last?.started_at ?? null,
      last_run_source: last?.trigger_source ?? null,
      last_run_age_minutes: last
        ? (Date.now() - new Date(last.started_at).getTime()) / 60_000
        : null,
    });
  } catch (err) {
    return NextResponse.json(
      { ok: false, error: err instanceof Error ? err.message : "unknown" },
      { status: 503 },
    );
  }
}
