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
