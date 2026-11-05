#!/usr/bin/env python
"""Record a non-ingest workflow's result in run_log (specs/002-dashboard-revamp).

    python scripts/log_worker.py --worker canary --status success
    python scripts/log_worker.py --worker backup --status failure

The dashboard's health dot and Health page read the latest row per worker
from the snapshot (db/migrations/0029). Called as the last step of canary.yml
and backup.yml with `if: always()` and `${{ job.status }}`. Never fails the
job: a status that cannot be recorded shows up as a stale row instead.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

import requests


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", required=True, choices=["canary", "backup"])
    ap.add_argument("--status", required=True)   # GitHub's job.status
    args = ap.parse_args()
    url, key = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        print("log_worker: no database configured; nothing recorded")
        return 0
    now = datetime.now(timezone.utc).isoformat()
    ok = args.status == "success"
    run = os.environ.get("GITHUB_RUN_ID", "")
    row = {"worker": args.worker, "started_at": now, "finished_at": now,
           "errors": [] if ok else [{"error": f"workflow {args.status}", "run_id": run}],
           "trigger_source": "schedule"}
    try:
        r = requests.post(f"{url.rstrip('/')}/rest/v1/run_log",
                          headers={"apikey": key, "Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json",
                                   "Prefer": "return=minimal"},
                          json=row, timeout=30)
        print(f"log_worker: {args.worker} {args.status} -> HTTP {r.status_code}")
    except requests.RequestException as exc:
        print(f"log_worker: not recorded ({exc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
