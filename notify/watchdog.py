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
from notify.alerts import (_since, alert_ingest_errors,  # noqa: E402
                           check_pipeline_dry, send_alert)


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


def retired_on_purpose() -> set[tuple[str, str]]:
    """(ats, token) of boards retired deliberately, with a reason recorded in
    seeds/companies_retired.yaml. Those are not news; repeating them in every
    alert (as on 2026-10-16) buries a board that died by itself."""
    import yaml
    path = Path(__file__).resolve().parents[1] / "seeds" / "companies_retired.yaml"
    try:
        groups = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return set()
    return {(str(r.get("ats")), str(r.get("token")).lower())
            for rows in groups.values() if isinstance(rows, list)
            for r in rows if isinstance(r, dict)}


def deactivated_boards() -> list[str]:
    """Boards switched off by the failure counter, not by a decision."""
    base, headers = db()
    rows = requests.get(f"{base}/companies?select=name,ats,board_token&active=is.false",
                        headers=headers, timeout=30).json()
    retired = retired_on_purpose()
    return [r["name"] for r in rows
            if (r.get("ats"), str(r.get("board_token")).lower()) not in retired]


RULES_SHARE_LIMIT = 0.30        # PLAN 7.6
LLM_SILENCE = timedelta(hours=2)


def llm_health(now: datetime | None = None) -> list[str]:
    """PLAN 7.6: is scoring quietly running on the rules fallback?

    Two findings. (1) Work is being scored by rules -- so there were jobs to
    score -- yet no LLM has succeeded for LLM_SILENCE: an outage, not a quiet
    spell. (2) The rules fallback produced over RULES_SHARE_LIMIT of today's
    scores: the queue is mostly guesses. Both read only what scoring recorded
    (job_scores.provider, provider_usage); neither makes an LLM call.
    """
    now = now or datetime.now(timezone.utc)
    base, headers = db()
    day = now.date().isoformat()
    findings: list[str] = []
    try:
        today = requests.get(
            f"{base}/job_scores?select=provider,scored_at&fit_score=not.is.null"
            f"&scored_at=gte.{day}T00:00:00Z", headers=headers, timeout=30).json()
        usage = requests.get(f"{base}/provider_usage?select=provider,last_success_at",
                             headers=headers, timeout=30).json()
    except Exception as exc:
        return [f"could not read scoring health ({type(exc).__name__})"]
    if not isinstance(today, list) or not isinstance(usage, list):
        return []                       # tables predate 0014
    rules = [r for r in today if r.get("provider") == "rules"]
    cutoff = now - LLM_SILENCE
    recent_rules = [r for r in rules
                    if datetime.fromisoformat(r["scored_at"].replace("Z", "+00:00")) >= cutoff]
    successes = [datetime.fromisoformat(u["last_success_at"].replace("Z", "+00:00"))
                 for u in usage if u.get("last_success_at")]
    last_llm = max(successes) if successes else None
    if recent_rules and (last_llm is None or last_llm < cutoff):
        since = f"since {last_llm:%Y-%m-%d %H:%M} UTC" if last_llm else "in the ledger at all"
        findings.append(f"no LLM provider has scored {since}, and {len(recent_rules)} "
                        f"job(s) in the last 2h were scored provisionally by rules")
    if today and len(rules) / len(today) > RULES_SHARE_LIMIT:
        findings.append(f"{len(rules)} of {len(today)} scores today "
                        f"({len(rules) / len(today):.0%}) came from the rules fallback "
                        f"(limit {RULES_SHARE_LIMIT:.0%})")
    return findings


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
        resp = requests.get(f"{base}/jobs?select=id&first_seen_at=gte.{_since(72)}&limit=1",
                            headers=headers, timeout=30)
        resp.raise_for_status()
        if not resp.json():
            findings.append("no new postings in 72h")

    # Scoring health (PLAN 7.6): an outage the rules fallback is covering for
    # looks healthy everywhere else, because jobs keep flowing.
    llm_findings = llm_health()
    for f in llm_findings:
        print(f"scoring: {f}")
    if llm_findings:
        findings.extend(llm_findings)
        if not args.dry_run:
            send_alert("llm_degraded", "scoring is running on the rules fallback",
                       "\n".join(llm_findings) + "\n\n"
                       "Jobs still reach the queue and pings, marked provisional, and are "
                       "re-scored automatically once a provider works. If this persists:\n"
                       "  python -m score.healthcheck --deep\n"
                       "and check provider_usage for cooldowns and last errors.\n")

    # Dispatch token (Phase 2.6). Both external triggers share it, so its
    # expiry is a single point of failure for the whole trigger plane.
    from notify import token_watch
    token = token_watch.check()
    print(f"dispatch token: {token.state} — {token.detail}")
    if token.state in ("expiring", "invalid"):
        findings.append(token.detail)
        if not args.dry_run:
            send_alert("token_expiry",
                       ("dispatch token REJECTED" if token.state == "invalid"
                        else f"dispatch token expires in {token.days_left:.0f} days"),
                       token_watch.alert_body(token))

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
