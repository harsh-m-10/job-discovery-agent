// node --test infra/cf-standby/
// No network: tick() takes an injected fetch.

import { test } from "node:test";
import assert from "node:assert/strict";
import { shouldDispatch, tick, STALE_MINUTES } from "./index.js";

const NOW = Date.parse("2026-10-15T12:00:00Z");
const ago = (min) => ({ created_at: new Date(NOW - min * 60_000).toISOString() });

test("a run inside the stale window means the primary is alive", () => {
  assert.equal(shouldDispatch(ago(14), NOW).dispatch, false);
  assert.equal(shouldDispatch(ago(STALE_MINUTES), NOW).dispatch, false);
});

test("a run older than the window means the primary missed a tick", () => {
  assert.equal(shouldDispatch(ago(STALE_MINUTES + 1), NOW).dispatch, true);
  assert.equal(shouldDispatch(ago(300), NOW).dispatch, true);
});

test("no history, or a malformed timestamp, dispatches rather than waits", () => {
  assert.equal(shouldDispatch(undefined, NOW).dispatch, true);
  assert.equal(shouldDispatch({}, NOW).dispatch, true);
  assert.equal(shouldDispatch({ created_at: "garbage" }, NOW).dispatch, true);
});

function fakeFetch(listStatus, runs, dispatchStatus = 204) {
  const calls = [];
  const impl = async (url, init = {}) => {
    calls.push({ url, init });
    if (url.endsWith("/dispatches")) return { ok: dispatchStatus < 300, status: dispatchStatus };
    return { ok: listStatus < 300, status: listStatus, json: async () => ({ workflow_runs: runs }) };
  };
  return { impl, calls };
}

test("a fresh run costs one GET and no dispatch", async () => {
  const { impl, calls } = fakeFetch(200, [ago(5)]);
  const out = await tick({ GH_DISPATCH_PAT: "t" }, impl, NOW);
  assert.equal(out.dispatched, false);
  assert.equal(calls.length, 1);
});

test("a stale run dispatches once, tagged cf-standby, on main", async () => {
  const { impl, calls } = fakeFetch(200, [ago(90)]);
  const out = await tick({ GH_DISPATCH_PAT: "t" }, impl, NOW);
  assert.equal(out.dispatched, true);
  assert.equal(calls.length, 2, "at most two subrequests per invocation");
  const body = JSON.parse(calls[1].init.body);
  assert.deepEqual(body, { ref: "main", inputs: { source: "cf-standby" } });
  assert.equal(calls[1].init.method, "POST");
  assert.ok(calls[1].init.headers["User-Agent"], "GitHub rejects requests without one");
});

test("an API error never triggers a blind dispatch", async () => {
  const { impl, calls } = fakeFetch(401, []);
  const out = await tick({ GH_DISPATCH_PAT: "t" }, impl, NOW);
  assert.equal(out.dispatched, false);
  assert.equal(calls.length, 1);
});

test("without a token it does nothing at all", async () => {
  const { impl, calls } = fakeFetch(200, []);
  const out = await tick({}, impl, NOW);
  assert.equal(out.dispatched, false);
  assert.equal(calls.length, 0);
});

test("the dispatched source is one ingest.run accepts", async () => {
  // Must match TRIGGER_SOURCES in ingest/run.py, or every standby run fails.
  const { impl, calls } = fakeFetch(200, [ago(90)]);
  await tick({ GH_DISPATCH_PAT: "t" }, impl, NOW);
  const source = JSON.parse(calls[1].init.body).inputs.source;
  assert.ok(["cronjob", "cf-standby", "schedule", "manual"].includes(source));
});
