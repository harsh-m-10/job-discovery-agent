#!/usr/bin/env python
"""LAYER 1 entrypoint — poll every due board, diff it, persist the result.

    python -m ingest.run                # full run against Supabase
    python -m ingest.run --dry-run      # fetch + normalize only, no DB, no creds
    python -m ingest.run --all          # ignore priority pacing, poll everything
    python -m ingest.run --only stripe,zeta

Design constraints this file exists to honour:
  * One dead board must never break a run — every company is isolated.
  * Runs are idempotent. GitHub Actions cron is best-effort and drifts by 10-20
    minutes or skips entirely, so nothing may depend on exact 15-minute spacing.
    Each run re-fetches whole boards and diffs; there is no "since last run"
    window to miss.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

# LLM output routinely contains en-dashes and non-breaking hyphens. The Windows
# console defaults to cp1252, which cannot encode them, and an unhandled
# UnicodeEncodeError at print time would fail a run whose work is already
# committed to the database.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


import yaml
from dotenv import load_dotenv

from .adapters import get_adapter
from .lifecycle import plan
from .models import BoardFetchError, RawJob
from .normalize import is_india_relevant
from .store import MissingCredentials, Store

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "config" / "settings.yaml"
VERIFIED = ROOT / "seeds" / "companies.verified.yaml"

# priority -> minimum minutes between polls (spec §5: 1 every run, 2 hourly, 3 daily)
CADENCE = {1: 0, 2: 55, 3: 23 * 60}


def load_settings() -> dict:
    with SETTINGS.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def is_due(company: dict, force: bool) -> bool:
    if force:
        return True
    gap = CADENCE.get(int(company.get("priority") or 2), 55)
    if gap == 0:
        return True
    last = company.get("last_ok_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
    except ValueError:
        return True
    return datetime.now(timezone.utc) - last_dt >= timedelta(minutes=gap)


def fetch_board(company: dict, keep_all_locations: bool = False) -> tuple[dict, list[RawJob] | None, str | None]:
    """-> (company, jobs, error). Never raises; the caller tallies failures."""
    try:
        jobs = get_adapter(company["ats"]).fetch(company["board_token"])
    except BoardFetchError as exc:
        return company, None, str(exc)
    except Exception as exc:  # an adapter bug must not take the run down
        return company, None, f"adapter error: {type(exc).__name__}: {exc}"
    if not keep_all_locations:
        jobs = [j for j in jobs if is_india_relevant(j.location, j.description)]
    return company, jobs, None


def companies_from_seeds() -> list[dict]:
    with VERIFIED.open(encoding="utf-8") as fh:
        rows = yaml.safe_load(fh) or []
    return [
        {"id": -(i + 1), "name": r["name"], "ats": r["ats"],
         "board_token": str(r["token"]), "category": r.get("category"),
         "priority": r.get("priority", 2), "last_ok_at": None,
         "consecutive_failures": 0}
        for i, r in enumerate(rows)
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and normalize only; no database, no credentials needed")
    ap.add_argument("--all", action="store_true", help="ignore priority pacing")
    ap.add_argument("--only", help="comma-separated board tokens")
    ap.add_argument("--keep-all-locations", action="store_true",
                    help="skip the India location gate (diagnostics only)")
    args = ap.parse_args()

    load_dotenv(ROOT / ".env")
    settings = load_settings()
    workers = int(settings.get("fetch_concurrency", 8))
    max_failures = int(settings.get("max_consecutive_failures", 5))

    store: Store | None = None
    if args.dry_run:
        companies = companies_from_seeds()
    else:
        try:
            store = Store()
        except MissingCredentials as exc:
            print(f"error: {exc}\n\nRun with --dry-run to test ingestion without a database.",
                  file=sys.stderr)
            return 2
        companies = store.active_companies()

    if args.only:
        wanted = {t.strip().lower() for t in args.only.split(",")}
        companies = [c for c in companies if c["board_token"].lower() in wanted]

    due = [c for c in companies if is_due(c, args.all or args.only is not None)]
    started = time.monotonic()
    run_id = store.start_run("ingest") if store else None
    print(f"{len(due)} of {len(companies)} boards due"
          f"{' (dry run)' if args.dry_run else ''}\n")

    totals = {"seen": 0, "new": 0, "changed": 0, "reopened": 0, "closed": 0}
    errors: list[dict] = []
    deactivated: list[str] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = pool.map(lambda c: fetch_board(c, args.keep_all_locations), due)

        # Fetches run concurrently; persistence stays serial so a single board's
        # write failure is attributable and cannot half-apply another's diff.
        for company, jobs, error in results:
            name = company["name"]
            if error is not None:
                failures = int(company.get("consecutive_failures") or 0) + 1
                note = ""
                if store:
                    if store.mark_company_failure(company["id"], failures, max_failures):
                        deactivated.append(name)
                        note = " -> DEACTIVATED"
                errors.append({"company": name, "ats": company["ats"],
                               "token": company["board_token"], "error": error,
                               "consecutive_failures": failures})
                print(f"  FAIL {name}: {error} (failure {failures}/{max_failures}){note}")
                continue

            totals["seen"] += len(jobs)
            if store is None:
                print(f"  ok   {name:<18} {len(jobs):>3} India-relevant postings")
                continue

            try:
                existing = store.existing_jobs(company["id"])
                p = plan(company["id"], jobs, existing)
                store.upsert_jobs(p.upserts)
                store.touch_jobs(p.touch_ids)
                store.close_jobs(p.close_ids)
                store.mark_company_ok(company["id"])
            except Exception as exc:
                errors.append({"company": name, "error": f"persist: {exc}"})
                print(f"  FAIL {name}: persist: {exc}")
                continue

            totals["new"] += p.n_new
            totals["changed"] += p.n_changed
            totals["reopened"] += p.n_reopened
            totals["closed"] += p.n_closed
            flags = "".join([
                f" +{p.n_new} new" if p.n_new else "",
                f" ~{p.n_changed} edited" if p.n_changed else "",
                f" ^{p.n_reopened} reopened" if p.n_reopened else "",
                f" -{p.n_closed} closed" if p.n_closed else "",
            ])
            print(f"  ok   {name:<18} {len(jobs):>3} seen{flags}")

    elapsed = time.monotonic() - started
    print(f"\n{'-' * 60}")
    print(f"seen {totals['seen']}  new {totals['new']}  edited {totals['changed']}"
          f"  reopened {totals['reopened']}  closed {totals['closed']}"
          f"  errors {len(errors)}  in {elapsed:.1f}s")
    if deactivated:
        print(f"deactivated after {max_failures} consecutive failures: "
              f"{', '.join(deactivated)}")

    if store:
        store.finish_run(run_id, jobs_seen=totals["seen"], jobs_new=totals["new"],
                         jobs_closed=totals["closed"], errors=errors)

    # A run that failed on some boards is still a successful run — exit non-zero
    # only if nothing at all got through, which means something systemic.
    if due and len(errors) == len(due):
        print("every board failed — check network or credentials", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
