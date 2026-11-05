import "server-only";
import { AsyncLocalStorage } from "node:async_hooks";

/**
 * Per-render query timing (specs/001-dashboard-speed-retention, FR-001).
 *
 * Every PostgREST call made through lib/db.ts records itself into the store of
 * the render it belongs to. Pages call logPage() at the end; /api/perf runs a
 * page's loaders inside measure() and returns the result as Server-Timing
 * (server components cannot set response headers).
 */

export type QueryTiming = {
  name: string;      // "<table>?<select…>" shortened
  ms: number;
  pages: number;
  rows: number;
  started: number;   // ms since the render began, shows sequential vs parallel
};

type Store = { t0: number; queries: QueryTiming[] };

const storage = new AsyncLocalStorage<Store>();

/** Run fn with a fresh collector; returns its result and the timings. */
export async function measure<T>(fn: () => Promise<T>): Promise<{ value: T; total: number; queries: QueryTiming[] }> {
  const store: Store = { t0: performance.now(), queries: [] };
  const value = await storage.run(store, fn);
  return { value, total: performance.now() - store.t0, queries: store.queries };
}

/** Called by lib/db.ts around every request. */
export function record(name: string, startedAt: number, pages: number, rows: number) {
  const store = storage.getStore() ?? fallback();
  store.queries.push({
    name: shorten(name),
    ms: performance.now() - startedAt,
    pages,
    rows,
    started: startedAt - store.t0,
  });
}

/** Pages without measure() around them (real page views) collect per request:
 *  the first record of a render starts a store on the current async context. */
function fallback(): Store {
  const store: Store = { t0: performance.now(), queries: [] };
  storage.enterWith(store);
  return store;
}

function shorten(name: string): string {
  // Drop ids and long in-lists so log lines stay readable and group well.
  return name.replace(/in\.\([^)]*\)/g, "in.(…)").replace(/eq\.\d+/g, "eq.N").slice(0, 120);
}

export function serverTiming(total: number, queries: QueryTiming[]): string {
  const parts = queries.map((q, i) =>
    `q${i};desc="${q.name.replace(/"/g, "'")} p${q.pages} r${q.rows}";dur=${q.ms.toFixed(1)}`);
  parts.push(`total;dur=${total.toFixed(1)}`);
  return parts.join(", ");
}

/** One log line per page render, read in Vercel runtime logs. */
export function logPage(page: string) {
  const store = storage.getStore();
  if (!store) return;
  const total = performance.now() - store.t0;
  const slow = [...store.queries].sort((a, b) => b.ms - a.ms).slice(0, 8)
    .map((q) => `${q.ms.toFixed(0)}ms@${q.started.toFixed(0)} p${q.pages} ${q.name}`).join(" | ");
  console.log(`[timing] page=${page} total=${total.toFixed(0)}ms queries=${store.queries.length} :: ${slow}`);
}
