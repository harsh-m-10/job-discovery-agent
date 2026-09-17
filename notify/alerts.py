"""Operational alerting.

Silent death is the main long-term risk in this system: nothing about a stopped
cron, an exhausted provider chain or a dry pipeline is visible unless someone
opens the dashboard. Every condition here is one that produces *no* symptom
other than an empty queue, which is indistinguishable from a quiet week.

Alerts are deduplicated through the `notifications` table using a synthetic
negative job_id per alert kind, so a recurring failure emails once per cooldown
rather than every fifteen minutes.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

from . import email as email_channel

log = logging.getLogger("notify.alerts")

# Synthetic job_ids. Real jobs are positive, so these can never collide, and
# reusing `notifications` avoids another table just for alert bookkeeping.
ALERT_KINDS = {
    "ingest_errors": -1,
    "pipeline_dry": -2,
    "providers_exhausted": -3,
}
COOLDOWN_HOURS = {"ingest_errors": 6, "pipeline_dry": 24, "providers_exhausted": 6}
DRY_THRESHOLD_HOURS = 72


def _db():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
    return f"{url}/rest/v1", {"apikey": key, "Authorization": f"Bearer {key}",
                              "Content-Type": "application/json"}


def _since(hours: float) -> str:
    """An ISO timestamp `hours` ago, safe to place in a PostgREST query string.

    `isoformat()` ends in "+00:00", and a bare "+" in a URL query is decoded as
    a space. PostgREST then hands Postgres "2026-09-17T02:19:35 00:00", which is
    a 400 — and the error body is a JSON *object*, which is truthy. Unquoted,
    every cooldown check read "already alerted" and every alert was suppressed;
    the pipeline-dry check read "a job was seen" and never fired. Found
    2026-09-17; the table held one alert row in its whole history.
    """
    return quote((datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat())


def _recently_alerted(kind: str) -> bool:
    base, headers = _db()
    since = _since(COOLDOWN_HOURS.get(kind, 6))
    resp = requests.get(
        f"{base}/notifications?select=id&channel=eq.alert:{kind}"
        f"&ok=is.true&sent_at=gte.{since}", headers=headers, timeout=30)
    # A rejected query must not masquerade as a prior send. Fail loudly instead.
    resp.raise_for_status()
    return bool(resp.json())


def _record(kind: str, ok: bool, error: str | None) -> None:
    base, headers = _db()
    requests.post(f"{base}/notifications",
                  headers={**headers, "Prefer": "return=minimal"},
                  json={"job_id": None, "channel": f"alert:{kind}", "ok": ok,
                        "error": (error or "")[:500] or None}, timeout=30)


def send_alert(kind: str, subject: str, body: str, force: bool = False) -> bool:
    """-> True if an email actually went out."""
    if kind not in ALERT_KINDS:
        raise ValueError(f"unknown alert kind {kind!r}")

    log.error("ALERT [%s] %s", kind, subject)
    if not force and _recently_alerted(kind):
        print(f"  alert '{kind}' suppressed — already sent within "
              f"{COOLDOWN_HOURS.get(kind, 6)}h")
        return False

    try:
        transport = email_channel.send(f"[job agent] {subject}", body)
        _record(kind, True, None)
        print(f"  alert '{kind}' emailed via {transport}")
        return True
    except Exception as exc:
        # An un-sendable alert is itself worth recording: it means the alerting
        # path is broken, which is exactly the failure this module exists for.
        _record(kind, False, str(exc))
        log.error("alert '%s' could not be delivered: %s", kind, exc)
        print(f"  alert '{kind}' NOT DELIVERED: {exc}")
        return False


# --- individual conditions ----------------------------------------------

def alert_ingest_errors(errors: list[dict], deactivated: list[str]) -> None:
    if not errors and not deactivated:
        return
    lines = [f"{len(errors)} board(s) failed during the last ingestion run.", ""]
    for e in errors[:25]:
        lines.append(f"  {e.get('company', '?')} ({e.get('ats', '?')}/"
                     f"{e.get('token', '?')}): {e.get('error', '')[:160]}")
    if deactivated:
        lines += ["", "DEACTIVATED after repeated failures — the token has "
                      "probably changed:", "  " + ", ".join(deactivated),
                  "", "Fix with: python scripts/verify_boards.py"]
    send_alert("ingest_errors",
               f"{len(errors)} board failure(s)"
               + (f", {len(deactivated)} deactivated" if deactivated else ""),
               "\n".join(lines))


def alert_providers_exhausted(detail: str, unscored: int) -> None:
    send_alert(
        "providers_exhausted",
        "every LLM provider refused — scoring has stopped",
        "No configured LLM provider could score anything.\n\n"
        f"  {detail}\n\n"
        f"{unscored} job(s) are marked scoring_failed and are invisible to the "
        f"queue until retried.\n\n"
        "Recover with:\n"
        "  python -m score.run --retry-failed\n\n"
        "Check provider health with:\n"
        "  python -m score.healthcheck\n",
    )


def check_pipeline_dry(hours: int = DRY_THRESHOLD_HOURS) -> bool:
    """Alert if no posting has been seen for the first time in `hours`.

    This is the quietest failure mode of all: the cron can be disabled, every
    token can rot, and the dashboard still shows yesterday's queue. Nothing
    else in the system notices.
    """
    base, headers = _db()
    since = _since(hours)
    resp = requests.get(f"{base}/jobs?select=id&first_seen_at=gte.{since}&limit=1",
                        headers=headers, timeout=30)
    resp.raise_for_status()
    if resp.json():
        return False

    runs = requests.get(
        f"{base}/run_log?select=started_at,jobs_seen,jobs_new"
        f"&order=started_at.desc&limit=1", headers=headers, timeout=30).json()
    last = runs[0]["started_at"] if runs else "never"
    send_alert(
        "pipeline_dry",
        f"no new postings in {hours}h — the pipeline may be dead",
        f"No job has been seen for the first time in the last {hours} hours "
        f"across any tracked board.\n\n"
        f"Last ingestion run: {last}\n\n"
        "Likely causes, in order of probability:\n"
        "  1. GitHub disabled the scheduled workflow (60 days of repo "
        "inactivity — keepalive.yml exists to prevent this)\n"
        "  2. Supabase credentials expired or the project was paused\n"
        "  3. Every board token rotted at once (unlikely)\n\n"
        "Check: https://github.com/harsh-m-10/automatedJobBoard/actions\n",
    )
    return True
