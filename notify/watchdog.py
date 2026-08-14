#!/usr/bin/env python
"""Operational watchdog — the thing that notices when nothing is happening.

Runs as its own workflow step after ingestion and scoring, and reads state back
out of the database rather than being called from inside a run. That is not
incidental: `ingest/` is Layer 1 and may not import from `notify/`, and
scripts/check_boundaries.py fails CI if it does. Reading `run_log` after the
fact keeps the dependency pointing the right way.

    python -m notify.watchdog             # check and alert
    python -m notify.watchdog --dry-run   # report findings, send nothing
    python -m notify.watchdog --test      # force one alert through, to prove
                                          # the email path actually works

Conditions checked:
  1. the last ingestion run recorded board errors, or deactivated a board
  2. no posting has been seen for the first time in 72h
  3. jobs are sitting in scoring_failed because every provider refused
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from notify import email as email_channel          # noqa: E402
from notify.alerts import (alert_ingest_errors, check_pipeline_dry,  # noqa: E402
                           send_alert)


def db():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
    return f"{url}/rest/v1", {"apikey": key, "Authorization": f"Bearer {key}",
                              "Content-Type": "application/json"}


def latest_run() -> dict | None:
    base, headers = db()
    rows = requests.get(
        f"{base}/run_log?select=id,worker,started_at,finished_at,jobs_seen,"
        f"jobs_new,jobs_closed,errors&worker=eq.ingest"
        f"&order=started_at.desc&limit=1", headers=headers, timeout=30).json()
    return rows[0] if rows else None


def deactivated_boards() -> list[str]:
    base, headers = db()
    rows = requests.get(f"{base}/companies?select=name&active=is.false",
                        headers=headers, timeout=30).json()
    return [r["name"] for r in rows]


def stranded_jobs() -> int:
    base, headers = db()
    rows = requests.get(
        f"{base}/job_scores?select=job_id"
        f"&reject_reason=like.scoring_failed:providers_exhausted*",
        headers=headers, timeout=30).json()
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test", action="store_true",
                    help="force a test alert to prove the email path works")
    args = ap.parse_args()

    transport = email_channel.transport()
    print(f"email transport: {transport or 'NONE CONFIGURED'}")
    if not transport:
        print("  WARNING: no email transport is configured, so every alert "
              "below would be logged but never delivered.\n"
              "  Set RESEND_API_KEY + ALERT_EMAIL_TO, or "
              "SMTP_USER + SMTP_PASS + ALERT_EMAIL_TO.")

    if args.test:
        ok = send_alert("ingest_errors", "watchdog test alert",
                        "This is a test of the job-agent alerting path. "
                        "If you are reading it, alerts work.", force=True)
        print(f"test alert delivered: {ok}")
        return 0 if ok else 1

    findings: list[str] = []

    run = latest_run()
    if run is None:
        findings.append("no ingestion run has ever been recorded")
    else:
        errors = run.get("errors") or []
        age = datetime.now(timezone.utc) - datetime.fromisoformat(
            run["started_at"].replace("Z", "+00:00"))
        print(f"last ingest run: {run['started_at']} ({age.total_seconds()/3600:.1f}h ago), "
              f"seen={run.get('jobs_seen')} new={run.get('jobs_new')} "
              f"errors={len(errors)}")
        if age > timedelta(hours=6):
            findings.append(f"last ingestion run was {age.total_seconds()/3600:.0f}h ago")
        if errors:
            findings.append(f"{len(errors)} board error(s) in the last run")
            if not args.dry_run:
                alert_ingest_errors(errors, deactivated_boards())

    dead = deactivated_boards()
    if dead:
        print(f"deactivated boards: {', '.join(dead)}")

    stranded = stranded_jobs()
    if stranded:
        findings.append(f"{stranded} job(s) stranded in scoring_failed")
        if not args.dry_run:
            send_alert("providers_exhausted",
                       f"{stranded} job(s) stranded — scoring did not complete",
                       f"{stranded} job(s) carry "
                       f"reject_reason=scoring_failed:providers_exhausted.\n\n"
                       "Recover with:\n  python -m score.run --retry-failed\n\n"
                       "Check provider health with:\n"
                       "  python -m score.healthcheck\n")

    if not args.dry_run:
        if check_pipeline_dry():
            findings.append("no new postings in 72h")
    else:
        base, headers = db()
        since = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        fresh = requests.get(f"{base}/jobs?select=id&first_seen_at=gte.{since}&limit=1",
                             headers=headers, timeout=30).json()
        if not fresh:
            findings.append("no new postings in 72h")

    print()
    if findings:
        print("FINDINGS:")
        for f in findings:
            print(f"  - {f}")
    else:
        print("all clear: recent run, no board errors, postings still flowing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
