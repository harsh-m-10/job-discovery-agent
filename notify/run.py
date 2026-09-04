#!/usr/bin/env python
"""Delivery — ping once per job, ever.

    python -m notify.run --dry-run     # render the messages, send nothing
    python -m notify.run

Deduplication is the whole job here. A job is pinged at most once in its
lifetime, enforced by a lookup against `notifications` rather than by timing, so
a delayed or repeated GitHub Actions run cannot double-send.

Two channels, shaped by what each is good at. WhatsApp goes one message per job:
it is a phone ping meant to be glanced at, and CallMeBot truncates long bodies.
Email goes one digest per run, because the volume that made a per-job mail
reasonable no longer holds — at 92 boards a backlog run is 61 jobs, and 61
separate mails is not an inbox anyone reads. Every job in a digest still gets
its own `notifications` row, so dedupe stays per job and a later failure cannot
re-send the whole batch.

No daily cap. The dedupe check already bounds a runaway to one mail per job
ever, and the digest bounds a burst to one mail per run.
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


def digest(entries: list[tuple[dict, str]], threshold: float,
           dashboard: str = "") -> tuple[str, str]:
    """-> (subject, body) for one run's worth of jobs.

    Jobs arrive already sorted by descending fit score, so the subject can name
    the best one: the whole point of a digest is that it is triageable from the
    notification list without opening it.

    `entries` carries the per-job WhatsApp error alongside each job. Those are
    summarised once at the foot rather than repeated per job — when WhatsApp is
    simply unconfigured all 61 reasons are the same string.
    """
    jobs = [job for job, _ in entries]
    top = jobs[0]
    count = len(jobs)
    plural = "s" if count != 1 else ""
    subject = (f"[job agent] {count} new match{'es' if count != 1 else ''} — "
               f"top {float(top['fit_score']):.1f} {top['title']} at {top['company']}")

    lines = [f"{count} job{plural} scoring {threshold} or above, newest run.",
             "Sorted by fit score. Each block is one posting.", ""]
    for n, job in enumerate(jobs, 1):
        lines.append(f"{'=' * 58}")
        lines.append(f"[{n}/{count}]")
        lines.append(whatsapp.format_message(job, dashboard))
        lines.append("")

    reasons = sorted({reason for _, reason in entries})
    lines.append("=" * 58)
    lines.append("Delivered by email rather than WhatsApp: "
                 + ("; ".join(reasons) if reasons else "unknown"))
    if dashboard:
        lines.append(f"Dashboard: {dashboard.rstrip('/')}")
    return subject, "\n".join(lines)


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
        if whatsapp.configured():
            for job in jobs:
                print("-" * 50)
                print(whatsapp.format_message(job, dashboard))
            print("-" * 50)
        else:
            # Render exactly what would be sent, digest and all, rather than a
            # per-job preview of a mail that will never be sent that way.
            subject, body = digest(
                [(job, "whatsapp not configured") for job in jobs],
                threshold, dashboard)
            print(f"Subject: {subject}\n")
            print(body)
        print(f"\ndry run — nothing sent. whatsapp configured: "
              f"{whatsapp.configured()}, email transport: "
              f"{email_channel.transport() or 'none'}")
        return 0

    # An unconfigured CallMeBot is a settled state, not a per-job failure: it
    # will refuse all 61 pings for the same reason and leave 61 identical
    # "not set" rows behind. Decide once, and if it is not set up, go straight
    # to email rather than logging a failure per job. A configured-but-broken
    # CallMeBot still falls through per job, which is the case that needs the
    # per-job record.
    whatsapp_ready = whatsapp.configured()
    if not whatsapp_ready:
        print("whatsapp not configured — delivering by email\n")

    sent = failed = 0
    # Jobs that still need email, paired with why WhatsApp did not take them.
    # They are collected rather than sent inline so that one run produces one
    # email: at 92 boards a backlog run is 61 jobs, and 61 separate mails is
    # not a usable inbox. WhatsApp stays per-job — it is a phone ping, and
    # CallMeBot truncates long bodies.
    for_email: list[tuple[dict, str]] = []

    for job in jobs:
        if whatsapp_ready:
            try:
                whatsapp.send(whatsapp.format_message(job, dashboard))
                record(job["job_id"], "whatsapp", True, None)
                sent += 1
                print(f"  sent    {job['fit_score']} {job['title'][:48]}")
                continue
            except Exception as exc:
                record(job["job_id"], "whatsapp", False, str(exc))
                print(f"  FAILED  {job['title'][:48]}: {str(exc)[:90]}")
                for_email.append((job, str(exc)))
        else:
            for_email.append((job, "whatsapp not configured"))

    # One digest for everything WhatsApp did not deliver. Dedupe is still keyed
    # per job, so every job in the digest gets its own notifications row — a
    # single row for the batch would let the whole set re-send after a partial
    # failure elsewhere.
    if for_email:
        subject, body = digest(for_email, threshold, dashboard)
        try:
            transport = email_channel.send(subject, body)
            for job, _ in for_email:
                record(job["job_id"], "email", True, None)
            sent += len(for_email)
            print(f"\n  emailed {len(for_email)} job(s) as one digest via {transport}")
        except Exception as exc:
            for job, _ in for_email:
                record(job["job_id"], "email", False, str(exc))
            failed += len(for_email)
            print(f"\n  digest email failed: {str(exc)[:120]}")

    print(f"\ndelivered {sent}, undelivered {failed}")
    # Undelivered jobs keep no successful notification row, so the next run
    # retries them automatically.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
