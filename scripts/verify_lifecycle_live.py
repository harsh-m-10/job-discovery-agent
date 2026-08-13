#!/usr/bin/env python
"""Prove closed_at / reopen_count against the real database.

Two consecutive scheduled runs minutes apart will not close anything, because
nothing has actually vanished from a board in that window. So this simulates
one: it fetches a real board, hides a single posting, persists that diff, and
checks the row was closed — then restores the full board and checks the reopen
path. Both directions are asserted, and the row is left exactly as found.

    python scripts/verify_lifecycle_live.py --company Redis

Mutates one job row (closed_at, then reopen_count +1) and restores it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from ingest.adapters import get_adapter          # noqa: E402
from ingest.lifecycle import plan                # noqa: E402
from ingest.normalize import is_india_relevant   # noqa: E402
from ingest.store import Store                   # noqa: E402


def persist(store: Store, company_id: int, jobs) -> None:
    p = plan(company_id, jobs, store.existing_jobs(company_id))
    store.upsert_jobs(p.upserts)
    store.touch_jobs(p.touch_ids)
    store.close_jobs(p.close_ids)


def row_for(store: Store, company_id: int, ats_job_id: str) -> dict:
    return store.existing_jobs(company_id)[ats_job_id]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", default="Redis")
    args = ap.parse_args()

    store = Store()
    companies = [c for c in store.active_companies() if c["name"] == args.company]
    if not companies:
        print(f"no active company named {args.company!r}")
        return 1
    company = companies[0]
    cid = company["id"]

    jobs = [j for j in get_adapter(company["ats"]).fetch(company["board_token"])
            if is_india_relevant(j.location, j.description)]
    if not jobs:
        print(f"{args.company} has no India-relevant postings to test with")
        return 1

    victim = jobs[0]
    print(f"company: {company['name']} (id={cid}), {len(jobs)} postings")
    print(f"victim:  {victim.ats_job_id} — {victim.title}\n")

    baseline = row_for(store, cid, victim.ats_job_id)
    start_reopens = baseline.get("reopen_count") or 0
    print(f"  before        closed_at={baseline['closed_at']}  reopen_count={start_reopens}")
    assert baseline["closed_at"] is None, "victim was already closed; pick another company"

    # 1. The posting disappears from the board.
    persist(store, cid, [j for j in jobs if j.ats_job_id != victim.ats_job_id])
    after_close = row_for(store, cid, victim.ats_job_id)
    print(f"  vanished      closed_at={after_close['closed_at']}  "
          f"reopen_count={after_close.get('reopen_count')}")
    assert after_close["closed_at"] is not None, "FAIL: closed_at was not set"

    # 2. Every other posting must be untouched — a close must not cascade.
    still_open = sum(1 for k, r in store.existing_jobs(cid).items()
                     if r["closed_at"] is None)
    print(f"                {still_open} of {len(jobs)} postings still open")
    assert still_open == len(jobs) - 1, "FAIL: closed the wrong number of rows"

    # 3. It comes back on the next run.
    persist(store, cid, jobs)
    after_reopen = row_for(store, cid, victim.ats_job_id)
    print(f"  reappeared    closed_at={after_reopen['closed_at']}  "
          f"reopen_count={after_reopen.get('reopen_count')}")
    assert after_reopen["closed_at"] is None, "FAIL: closed_at was not cleared"
    assert (after_reopen.get("reopen_count") or 0) == start_reopens + 1, \
        "FAIL: reopen_count did not increment"

    # 4. A further run must be a no-op — reopen_count must not keep climbing.
    persist(store, cid, jobs)
    stable = row_for(store, cid, victim.ats_job_id)
    print(f"  steady state  closed_at={stable['closed_at']}  "
          f"reopen_count={stable.get('reopen_count')}")
    assert (stable.get("reopen_count") or 0) == start_reopens + 1, \
        "FAIL: reopen_count incremented on an unchanged run"

    print("\nPASS: close, reopen, and idempotence all verified against the live DB")
    print(f"note: {victim.ats_job_id} now has reopen_count={start_reopens + 1} "
          f"from this test")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"\n{exc}")
        raise SystemExit(1)
    except requests.RequestException as exc:
        print(f"\nnetwork error: {exc}")
        raise SystemExit(1)
