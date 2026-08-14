#!/usr/bin/env python
"""Delivery — ping once per job, ever.

    python -m notify.run --dry-run     # render the messages, send nothing
    python -m notify.run

Deduplication is the whole job here. A job is pinged at most once in its
lifetime, enforced by a lookup against `notifications` rather than by timing, so
a delayed or repeated GitHub Actions run cannot double-send.

No daily cap: at the observed flow of roughly 1-3 scoreable jobs per week, a
runaway-volume guard would only ever fire on a bug, and the dedupe check already
covers that case.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# LLM output routinely contains en-dashes and non-breaking hyphens. The Windows
# console defaults to cp1252, which cannot encode them, and an unhandled
# UnicodeEncodeError at print time would fail a run whose work is already
# committed to the database.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


import requests
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from notify import email as email_channel   # noqa: E402
from notify import whatsapp                 # noqa: E402

SETTINGS = ROOT / "config" / "settings.yaml"


def db_headers() -> dict:
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not key or not os.environ.get("SUPABASE_URL"):
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
    return {"apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"}


def base_url() -> str:
    return os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"


def age_label(hours: float) -> str:
    if hours < 1:
        return f"{max(1, int(hours * 60))}m"
    if hours < 48:
        return f"{int(hours)}h"
    return f"{int(hours / 24)}d"


def pending_jobs(threshold: float) -> list[dict]:
    """Jobs above threshold, still open, never notified."""
    headers = db_headers()
    scores = requests.get(
        f"{base_url()}/job_scores?select=job_id,fit_score,min_years,max_years,"
        f"matched_skills,gap_skills,reasoning&fit_score=gte.{threshold}",
        headers=headers, timeout=60).json()
    if not scores:
        return []

    already = {
        row["job_id"] for row in requests.get(
            f"{base_url()}/notifications?select=job_id&ok=is.true",
            headers=headers, timeout=60).json()
        if row.get("job_id") is not None
    }
    wanted = [s for s in scores if s["job_id"] not in already]
    if not wanted:
        return []

    ids = ",".join(str(s["job_id"]) for s in wanted)
    jobs = requests.get(
        f"{base_url()}/jobs?select=id,title,location,absolute_url,compensation,"
        f"posted_at,first_seen_at,companies(name)&closed_at=is.null&id=in.({ids})",
        headers=headers, timeout=60).json()
    job_by_id = {j["id"]: j for j in jobs}

    now = datetime.now(timezone.utc)
    out = []
    for score in wanted:
        job = job_by_id.get(score["job_id"])
        if not job:
            continue                      # closed between scoring and notifying
        stamp = job.get("posted_at") or job["first_seen_at"]
        hours = (now - datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                 ).total_seconds() / 3600
        out.append({
            "job_id": job["id"],
            "title": job["title"],
            "company": (job.get("companies") or {}).get("name", ""),
            "location": job.get("location"),
            "absolute_url": job["absolute_url"],
            "compensation": job.get("compensation"),
            "age_label": age_label(hours),
            "age_hours": hours,
            "fit_score": score.get("fit_score"),
            "min_years": score.get("min_years"),
            "max_years": score.get("max_years"),
            "matched_skills": score.get("matched_skills") or [],
            "gap_skills": score.get("gap_skills") or [],
            "reasoning": score.get("reasoning"),
            "referrals": [],              # populated in phase 5
        })

    out.sort(key=lambda j: -(float(j["fit_score"] or 0)))
    return out


def record(job_id: int, channel: str, ok: bool, error: str | None) -> None:
    requests.post(f"{base_url()}/notifications",
                  headers={**db_headers(), "Prefer": "return=minimal"},
                  json={"job_id": job_id, "channel": channel, "ok": ok,
                        "error": (error or "")[:500] or None},
                  timeout=30)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="render messages and print them; send nothing, record nothing")
    ap.add_argument("--threshold", type=float)
    args = ap.parse_args()

    settings = yaml.safe_load(SETTINGS.read_text(encoding="utf-8")) or {}
    threshold = args.threshold if args.threshold is not None else float(
        settings.get("ping_threshold", 6.5))
    dashboard = os.environ.get("DASHBOARD_URL", "")

    jobs = pending_jobs(threshold)
    print(f"{len(jobs)} job(s) at or above {threshold} awaiting a first ping\n")
    if not jobs:
        return 0

    if args.dry_run:
        for job in jobs:
            print("-" * 50)
            print(whatsapp.format_message(job, dashboard))
        print("-" * 50)
        print(f"\ndry run — nothing sent. whatsapp configured: "
              f"{whatsapp.configured()}, email transport: "
              f"{email_channel.transport() or 'none'}")
        return 0

    sent = failed = 0
    for job in jobs:
        message = whatsapp.format_message(job, dashboard)
        try:
            whatsapp.send(message)
            record(job["job_id"], "whatsapp", True, None)
            sent += 1
            print(f"  sent    {job['fit_score']} {job['title'][:48]}")
            continue
        except Exception as exc:
            whatsapp_error = str(exc)
            record(job["job_id"], "whatsapp", False, whatsapp_error)
            print(f"  FAILED  {job['title'][:48]}: {whatsapp_error[:90]}")

        # Fallback. A WhatsApp failure must not cost the job.
        try:
            transport = email_channel.send(
                f"[job agent] {job['fit_score']} {job['title']} at {job['company']}",
                message + "\n\n(sent by email because the WhatsApp ping failed: "
                + whatsapp_error + ")")
            record(job["job_id"], "email", True, None)
            sent += 1
            print(f"          -> emailed instead via {transport}")
        except Exception as exc:
            record(job["job_id"], "email", False, str(exc))
            failed += 1
            print(f"          -> email also failed: {str(exc)[:90]}")

    print(f"\ndelivered {sent}, undelivered {failed}")
    # Undelivered jobs keep no successful notification row, so the next run
    # retries them automatically.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
