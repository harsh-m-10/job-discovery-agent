import { NextResponse } from "next/server";
import { measure, serverTiming } from "@/lib/perf";
import { getContacts, getFunnel, getJob, getSnapshot, getStatus } from "@/lib/queries";
import { getAskStats, getInProgress, getJobs, getNewSince, getPipeline } from "@/lib/screens";

export const dynamic = "force-dynamic";

/**
 * Server-Timing for one dashboard page (specs/001-dashboard-speed-retention,
 * contracts/snapshot.schema.md). Runs the same loaders a real view of that page
 * runs -- the layout's and the page's, concurrently, as Next renders them --
 * and reports every database request. Authenticated by x-dashboard-secret in
 * proxy.ts like every /api route. Pages as of spec 002.
 */
const DAY = () => new Date(Date.now() - 86_400_000).toISOString();
const PAGES: Record<string, (id: number) => Promise<unknown>> = {
  today: () => Promise.all([getSnapshot(), getInProgress(), getNewSince(DAY())]),
  jobs: () => Promise.all([getSnapshot(), getJobs({ fam: ["core", "secondary"], src: [], tier: [], fresh: null })]),
  pipeline: () => Promise.all([getSnapshot(), getPipeline()]),
  people: () => Promise.all([getSnapshot(), getContacts(1), getAskStats()]),
  health: () => Promise.all([getSnapshot(), getStatus()]),
  funnel: () => Promise.all([getSnapshot(), getFunnel()]),
  job: (id) => Promise.all([getSnapshot(), getJob(id)]),
};

export async function GET(request: Request) {
  const url = new URL(request.url);
  const page = url.searchParams.get("page") ?? "today";
  const run = PAGES[page];
  if (!run) return NextResponse.json({ error: `pages: ${Object.keys(PAGES).join(", ")}` }, { status: 400 });
  const id = Number(url.searchParams.get("id") ?? 0);
  const { total, queries } = await measure(() => run(id));
  return NextResponse.json(
    { page, total_ms: Math.round(total), queries: queries.map((q) => ({ ...q, ms: Math.round(q.ms), started: Math.round(q.started) })) },
    { headers: { "Server-Timing": serverTiming(total, queries), "Cache-Control": "no-store" } },
  );
}
