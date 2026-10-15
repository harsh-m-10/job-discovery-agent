"""LAYER 3. Dead-man's switch — tell an external monitor the run finished.

    python -m notify.heartbeat --ok
    python -m notify.heartbeat --fail --detail "score.run exited 1"

Phase 0.3 of docs/PLAN.md. Every alert this project sends travels through the
pipeline that would be broken: `notify.watchdog` runs as the last step of the
same workflow it is watching, over the same network, against the same database,
using the same email transport. If the workflow stops being triggered at all —
which is the single most likely failure, given that GitHub auto-disables
scheduled workflows on repos with 60 days of no activity and throttles them
hard in between — nothing runs, so nothing complains. The system goes quiet and
looks exactly like a slow week.

A heartbeat inverts that. An external service (healthchecks.io, free) expects a
ping on a schedule and alerts when one does not arrive. The absence of our
signal is the signal, so it survives every failure mode inside the pipeline,
including the pipeline never starting.

Design constraints:

  * **Never raise, never fail the run.** This is monitoring. A successful
    ingest that cannot reach the monitor is still a successful ingest, and
    turning it red would invert the meaning of the colour. Every path here
    returns an exit code of 0.
  * **No database writes.** Recording 96 heartbeats a day in `notifications`
    would reproduce the unbounded-growth problem that table already has (G7).
    healthchecks.io keeps the log.
  * **No-op loudly when unconfigured.** HC_PING_URL arrives only once the
    account exists, so until then this prints what it would have done rather
    than failing or, worse, pretending to have pinged.
"""

from __future__ import annotations

import argparse
import os
import sys

import requests

# A hung monitoring host must not hold a workflow lane. Short on purpose: this
# call is advisory, and a missed ping costs one false alert, not data.
TIMEOUT_S = 10

# healthchecks.io truncates the body it stores; sending more wastes a round trip
# and buries the useful first line of a traceback.
MAX_DETAIL = 2000


def ping(ok: bool, detail: str = "") -> bool:
    """Signal success or failure to the external monitor. Never raises.

    -> True if the monitor acknowledged. False means unconfigured or
    unreachable, both of which are non-fatal by design.
    """
    base = (os.environ.get("HC_PING_URL") or "").strip().rstrip("/")
    if not base:
        state = "ok" if ok else "fail"
        print(f"  heartbeat: HC_PING_URL not set — would have pinged '{state}'. "
              f"Create a check at healthchecks.io (period 15m, grace 30m) and "
              f"add its ping URL as the HC_PING_URL secret.")
        return False

    url = base if ok else f"{base}/fail"
    body = (detail or "")[:MAX_DETAIL]
    try:
        resp = requests.post(url, data=body.encode("utf-8"), timeout=TIMEOUT_S)
        if resp.status_code >= 300:
            print(f"  heartbeat: monitor answered {resp.status_code} "
                  f"{resp.text[:200]!r} — not fatal, continuing")
            return False
        print(f"  heartbeat: pinged {'ok' if ok else 'fail'}")
        return True
    except Exception as exc:
        # Including a DNS failure, a proxy error, a timeout: all non-fatal.
        print(f"  heartbeat: could not reach the monitor ({type(exc).__name__}: "
              f"{exc}) — not fatal, continuing")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--ok", action="store_true",
                       help="the run completed successfully")
    group.add_argument("--fail", action="store_true",
                       help="the run failed; alert immediately")
    ap.add_argument("--detail", default="",
                    help="short context stored alongside a failure ping")
    args = ap.parse_args()

    ping(ok=args.ok, detail=args.detail)
    # Always 0. See the module docstring: monitoring must not change the verdict
    # of the thing it monitors.
    return 0


if __name__ == "__main__":
    sys.exit(main())
