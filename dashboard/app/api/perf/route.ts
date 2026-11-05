import { NextResponse } from "next/server";
import { measure, serverTiming } from "@/lib/perf";
import { getCompanies, getContacts, getFunnel, getJob, getQueue, getQueueMeta, getStatus } from "@/lib/queries";

export const dynamic = "force-dynamic";

/**
 * Server-Timing for one dashboard page (specs/001-dashboard-speed-retention,
 * contracts/snapshot.schema.md). Runs the same loaders a real view of that page
 * runs -- the layout's and the page's, concurrently, as Next renders them --
 * and reports every database request. Authenticated by x-dashboard-secret in
 * proxy.ts like every /api route.
 */
const PAGES: Record<string, (id: number) => Promise<unknown>> = {
  queue: () => Promise.all([getQueueMeta(), getQueue(), getQueueMeta(), getStatus()]),
  funnel: () => Promise.all([getQueueMeta(), getFunnel()]),
  contacts: () => Promise.all([getQueueMeta(), getContacts()]),
  companies: () => Promise.all([getQueueMeta(), getCompanies()]),
  job: (id) => Promise.all([getQueueMeta(), getJob(id)]),
};

export async function GET(request: Request) {
  const url = new URL(request.url);
  const page = url.searchParams.get("page") ?? "queue";
  const run = PAGES[page];
  if (!run) return NextResponse.json({ error: `pages: ${Object.keys(PAGES).join(", ")}` }, { status: 400 });
  const id = Number(url.searchParams.get("id") ?? 0);
  const { total, queries } = await measure(() => run(id));
  return NextResponse.json(
    { page, total_ms: Math.round(total), queries: queries.map((q) => ({ ...q, ms: Math.round(q.ms), started: Math.round(q.started) })) },
    { headers: { "Server-Timing": serverTiming(total, queries), "Cache-Control": "no-store" } },
  );
}
