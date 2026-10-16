// node --test infra/cf-standby/
// No network: tick() takes an injected fetch.

import { test } from "node:test";
import assert from "node:assert/strict";
import { tick } from "./index.js";

function fakeFetch(status = 204) {
  const calls = [];
  const impl = async (url, init = {}) => {
    calls.push({ url, init });
    return { ok: status < 300, status };
  };
  return { impl, calls };
}

test("every tick dispatches once, tagged cf, on main", async () => {
  const { impl, calls } = fakeFetch();
  const out = await tick({ GH_DISPATCH_PAT: "t" }, impl);
  assert.equal(out.dispatched, true);
  assert.equal(calls.length, 1, "one subrequest per invocation");
  assert.ok(calls[0].url.endsWith("/actions/workflows/ingest.yml/dispatches"));
  assert.equal(calls[0].init.method, "POST");
  assert.deepEqual(JSON.parse(calls[0].init.body), { ref: "main", inputs: { source: "cf" } });
  assert.ok(calls[0].init.headers["User-Agent"], "GitHub rejects requests without one");
});

test("a rejected token is reported, not retried", async () => {
  const { impl, calls } = fakeFetch(401);
  const out = await tick({ GH_DISPATCH_PAT: "t" }, impl);
  assert.equal(out.dispatched, false);
  assert.equal(out.status, 401);
  assert.equal(calls.length, 1);
});

test("without a token it does nothing at all", async () => {
  const { impl, calls } = fakeFetch();
  const out = await tick({}, impl);
  assert.equal(out.dispatched, false);
  assert.equal(calls.length, 0);
});

test("the dispatched source is one ingest.run accepts", async () => {
  // Must match TRIGGER_SOURCES in ingest/run.py, or every run it fires fails.
  const { impl, calls } = fakeFetch();
  await tick({ GH_DISPATCH_PAT: "t" }, impl);
  const source = JSON.parse(calls[0].init.body).inputs.source;
  assert.ok(["cronjob", "cf", "apps-script", "schedule", "manual"].includes(source));
});

import { hourly, hourlyTargets } from "./index.js";

const at = (iso) => new Date(iso);

test("Indeed runs every hour; the dailies at their hour", () => {
  assert.deepEqual(hourlyTargets(at("2026-10-16T05:41:00Z")), ["aggregate.yml"]);
  assert.deepEqual(hourlyTargets(at("2026-10-16T02:41:00Z")), ["aggregate.yml", "canary.yml"]);
  assert.deepEqual(hourlyTargets(at("2026-10-16T14:41:00Z")), ["aggregate.yml", "daily.yml"]);
  assert.deepEqual(hourlyTargets(at("2026-10-16T20:41:00Z")), ["aggregate.yml", "backup.yml"]);
});

test("weekly on Sunday 03 UTC, keepalive on Monday 03 UTC, healthcheck daily at 03", () => {
  assert.deepEqual(hourlyTargets(at("2026-10-18T03:41:00Z")),          // Sunday
    ["aggregate.yml", "healthcheck.yml", "weekly.yml"]);
  assert.deepEqual(hourlyTargets(at("2026-10-19T03:41:00Z")),          // Monday
    ["aggregate.yml", "healthcheck.yml", "keepalive.yml"]);
  assert.deepEqual(hourlyTargets(at("2026-10-16T03:41:00Z")),          // Friday
    ["aggregate.yml", "healthcheck.yml"]);
});

test("the hourly tick dispatches each target without inputs, within the subrequest budget", async () => {
  const calls = [];
  const impl = async (url, init) => { calls.push({ url, init }); return { status: 204 }; };
  const out = await hourly({ GH_DISPATCH_PAT: "t" }, impl, at("2026-10-18T03:41:00Z"));
  assert.equal(out.length, 3);
  assert.ok(calls.length <= 50);
  assert.ok(calls[0].url.endsWith("/workflows/aggregate.yml/dispatches"));
  assert.deepEqual(JSON.parse(calls[0].init.body), { ref: "main" });
});
