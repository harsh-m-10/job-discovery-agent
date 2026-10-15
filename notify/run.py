#!/usr/bin/env python
"""Delivery — ping once per job, ever.

    python -m notify.run --dry-run     # render the messages, send nothing
    python -m notify.run

Deduplication is the whole job here. A job is pinged at most once in its
lifetime, enforced by a lookup against `notifications` rather than by timing, so
a delayed or repeated GitHub Actions run cannot double-send.

Every match goes out at once on two channels (decided 2026-10-15): an ntfy.sh
push and one email from a non-Gmail sender, subject "[JOB 8.4] ..." -- see
notify/instant.py. Each job gets its own `notifications` row per channel, so
dedupe stays per job. A job neither channel takes, or one over
max_pings_per_day, goes into a single overflow digest for the run rather than
being dropped. The once-a-day summary is notify/daily.py.
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
from notify import instant                  # noqa: E402
from notify import whatsapp                 # noqa: E402

# Channel names in `notifications`. Both instant channels go out for every job
# (decided 2026-10-15): an ntfy push and one email from a non-Gmail sender.
# "email" is the overflow digest, for jobs neither instant channel took.
INSTANT_CHANNELS = ("ntfy", "email-instant")


def pings_sent_today(channels: tuple[str, ...]) -> int:
    """Successful instant pings since 00:00 IST, for max_pings_per_day.

    The day boundary is the operator's (India), not UTC's: a cap that reset at
    05:30 local would split one working morning across two budgets.
    """
    from datetime import timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    midnight = datetime.now(ist).replace(hour=0, minute=0, second=0, microsecond=0)
    since = midnight.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = get_all(
        f"notifications?select=job_id&ok=is.true&sent_at=gte.{since}"
        f"&channel=in.({','.join(channels)})", db_headers())
    # Distinct jobs: one job is two rows now, a push and an email.
    return len({r.get("job_id") for r in rows})

SETTINGS = ROOT / "config" / "settings.yaml"


def db_headers() -> dict:
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not key or not os.environ.get("SUPABASE_URL"):
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
    return {"apikey": key, "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"}


def base_url() -> str:
    return os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"


PAGE = 1000          # PostgREST's per-response cap, whatever Range asks for
MAX_PAGES = 50


def get_all(path_and_query: str, headers: dict) -> list[dict]:
    """Paged GET. Every read in this module goes through here.

    PostgREST silently caps a response at 1000 rows. The dashboard was bitten
    by exactly this on 2026-10-15 — 29 scored postings invisible because the
    jobs lookup was truncated — and pending_jobs had the same three unpaged
    reads. None had hit the cap yet (170 delivered pings, ~170 jobs over
    threshold), but `ok` notification rows only ever grow, and the failure
    mode is the worst kind: a truncated dedupe list re-pings old jobs, and a
    truncated jobs lookup drops a job as "closed between scoring and
    notifying" without a word.

    Raises on an HTTP error. Before, an error body was a JSON object that the
    callers then iterated as if it were rows.
    """
    from common.paging import stable_order
    table, _, query = path_and_query.partition("?")
    path_and_query = f"{table}?{stable_order(table, query)}"
    rows: list[dict] = []
    for page in range(MAX_PAGES):
        offset = page * PAGE
        resp = requests.get(
            f"{base_url()}/{path_and_query}",
            headers={**headers, "Range-Unit": "items",
                     "Range": f"{offset}-{offset + PAGE - 1}"},
            timeout=60)
        resp.raise_for_status()
        batch = resp.json()
        rows.extend(batch)
        if len(batch) < PAGE:
            return rows
    raise RuntimeError(
        f"{path_and_query.split('?')[0]}: still full pages after "
        f"{MAX_PAGES * PAGE} rows — the query is probably missing a filter")


def prune_failures(retention_days: float) -> int | None:
    """Delete delivery failures older than the retention window. Never raises.

    Only `ok = false` rows are touched. `ok = true` rows ARE the dedupe record
    — deleting one would re-ping its job — so the filter is fixed here rather
    than configurable.

    Measured 2026-10-15: 26,304 rows, of which 26,134 were failures, and those
    were two error strings repeated 13,067 times each ("CALLMEBOT_PHONE and
    CALLMEBOT_APIKEY are not set" / "no email transport configured"), every
    one from before 2026-09-05. A failure row is worth having while someone
    might investigate it; after a month it is only growth against the 500 MB
    free-tier cap.

    Housekeeping must never cost a ping, so any error is reported and
    swallowed. -> rows deleted, or None if the count was unavailable.
    """
    from notify.alerts import _since

    try:
        resp = requests.delete(
            f"{base_url()}/notifications?ok=is.false"
            f"&sent_at=lt.{_since(retention_days * 24)}",
            headers={**db_headers(), "Prefer": "return=minimal,count=exact"},
            timeout=120)
        resp.raise_for_status()
    except Exception as exc:
        print(f"  retention: could not prune failed notifications "
              f"({type(exc).__name__}: {exc}) — not fatal, continuing")
        return None
    # Content-Range on a counted DELETE reads "*/<n>".
    count = (resp.headers.get("Content-Range") or "").rpartition("/")[2]
    deleted = int(count) if count.isdigit() else None
    if deleted:
        print(f"  retention: deleted {deleted} failed notification(s) older "
              f"than {retention_days:g} days")
    return deleted


def age_label(hours: float, date_only: bool = False) -> str:
    """Human age. For a date-only posted_at (Workday) the hours are a
    midnight-UTC placeholder, so only whole days are honest."""
    if date_only:
        days = int(hours // 24)
        return "today" if days == 0 else f"{days}d"
    if hours < 1:
        return f"{max(1, int(hours * 60))}m"
    if hours < 48:
        return f"{int(hours)}h"
    return f"{int(hours / 24)}d"


def load_contacts(headers: dict) -> list:
    """Imported LinkedIn connections with a known employer (Phase 5.2)."""
    from datetime import date
    from score.referral import Contact
    rows = get_all(
        "connections?select=id,full_name,company_raw,company_norm,title,connected_on,"
        "is_batchmate,near_batch,profile_url&company_norm=not.is.null", headers)
    out = []
    for r in rows:
        try:
            on = date.fromisoformat(r["connected_on"]) if r.get("connected_on") else None
        except ValueError:
            on = None
        out.append(Contact(id=r["id"], full_name=r["full_name"], company_raw=r.get("company_raw"),
                           company_norm=r.get("company_norm") or "", title=r.get("title"),
                           connected_on=on, is_batchmate=bool(r.get("is_batchmate")),
                           profile_url=r.get("profile_url"),
                           near_batch=bool(r.get("near_batch"))))
    return out


def referrals_for(company: str, title: str, contacts: list, limit: int = 3) -> list[dict]:
    """The best few people to ask at this job's company (Phase 5.2).

    score/referral.py's match_company and rank_matches were written and tested
    in an earlier phase and then called only from tests: every ping and digest
    carried "referrals": []. Ranking is batchmates first, then contacts whose
    title can speak to the role, then the most recent connections.
    """
    from score.referral import match_company, rank_matches
    ranked = rank_matches(match_company(company, contacts), target_role=title, limit=limit)
    return [{"id": m.contact.id, "full_name": m.contact.full_name, "title": m.contact.title,
             "is_batchmate": m.contact.is_batchmate, "profile_url": m.contact.profile_url,
             "match": m.method}
            for m in ranked]


def resume_variants() -> dict[str, str]:
    """family -> link, empty links dropped (Phase 3.3)."""
    try:
        raw = (yaml.safe_load(SETTINGS.read_text(encoding="utf-8")) or {}).get(
            "resume_variants") or {}
    except (OSError, yaml.YAMLError):
        return {}
    return {str(k): str(v).strip() for k, v in raw.items() if str(v or "").strip()}


def pending_jobs(threshold: float, max_age_days: float | None = None) -> list[dict]:
    """Jobs above threshold, still open, never notified, not stale.

    The age check is repeated here rather than trusted to the scorer's gate.
    That gate only governs which jobs get scored; a job scored before the gate
    existed, or under a wider ceiling, keeps its score and would otherwise be
    pinged months late.

    The ceiling passed here is deliberately looser than the scoring gate — see
    max_display_age_days in config/settings.yaml. Tightening it to match the
    scorer would drop jobs in the window between being scored and being sent.
    """
    headers = db_headers()
    scores = get_all(
        f"job_scores?select=job_id,fit_score,min_years,max_years,"
        f"matched_skills,gap_skills,reasoning,provisional&fit_score=gte.{threshold}",
        headers)
    if not scores:
        return []

    already = {
        row["job_id"] for row in get_all(
            "notifications?select=job_id&ok=is.true", headers)
        if row.get("job_id") is not None
    }
    wanted = [s for s in scores if s["job_id"] not in already]
    if not wanted:
        return []

    ids = ",".join(str(s["job_id"]) for s in wanted)
    jobs = get_all(
        f"jobs?select=id,title,location,absolute_url,compensation,"
        f"posted_at,posted_at_precision,role_family,first_seen_at,source,companies(name)"
        f"&closed_at=is.null&id=in.({ids})",
        headers)
    job_by_id = {j["id"]: j for j in jobs}

    now = datetime.now(timezone.utc)
    variants = resume_variants()
    contacts = load_contacts(headers)
    out = []
    for score in wanted:
        job = job_by_id.get(score["job_id"])
        if not job:
            continue                      # closed between scoring and notifying
        stamp = job.get("posted_at") or job["first_seen_at"]
        hours = (now - datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                 ).total_seconds() / 3600
        if max_age_days and hours / 24 > max_age_days:
            continue
        out.append({
            "job_id": job["id"],
            "title": job["title"],
            "company": (job.get("companies") or {}).get("name", ""),
            "location": job.get("location"),
            "absolute_url": job["absolute_url"],
            "compensation": job.get("compensation"),
            "age_label": age_label(
                hours, date_only=job.get("posted_at_precision") == "date"),
            "age_hours": hours,
            "role_family": job.get("role_family"),
            "source": job.get("source") or "ats",
            "resume_variant": variants.get(job.get("role_family") or ""),
            "fit_score": score.get("fit_score"),
            # Phase 3.2/7.4: a rules score is a guess; the ping says so.
            "provisional": bool(score.get("provisional")),
            "min_years": score.get("min_years"),
            "max_years": score.get("max_years"),
            "matched_skills": score.get("matched_skills") or [],
            "gap_skills": score.get("gap_skills") or [],
            "reasoning": score.get("reasoning"),
            "referrals": referrals_for(
                (job.get("companies") or {}).get("name", ""), job["title"], contacts),
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

    `entries` carries why the instant channels did not take each job. Those
    are summarised once at the foot rather than repeated per job -- when a
    channel is simply unconfigured every reason is the same string.
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
    lines.append("Delivered by email rather than as an instant ping: "
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

    # The display ceiling, not the scoring gate. Scoring is gated tighter (3
    # days) so stale reqs never cost a call; delivery gets the wider window so a
    # job admitted just inside that gate is not dropped unpinged when the run
    # that would have sent it is delayed. Falls back to max_age_days if the
    # wider setting is absent, which keeps an old config working.
    max_age = float(
        settings.get("max_display_age_days")
        or settings.get("max_age_days", 0) or 0
    ) or None
    # Before the early return below, so housekeeping happens on quiet runs
    # too, and never on a dry run, which promises to record nothing.
    if not args.dry_run:
        prune_failures(float(
            settings.get("notification_failure_retention_days", 30)))

    jobs = pending_jobs(threshold, max_age)
    age_note = f", posted within {max_age:g} days" if max_age else ""
    print(f"{len(jobs)} job(s) at or above {threshold}{age_note} "
          f"awaiting a first ping\n")
    if not jobs:
        return 0

    if args.dry_run:
        for job in jobs:
            print("-" * 50)
            print(f"Subject: {instant.subject(job)}\n")
            print(instant.format_message(job, dashboard))
        print("-" * 50)
        print(f"\ndry run — nothing sent. ntfy: "
              f"{'on' if instant.ntfy_configured() else 'off'}, match email via "
              f"{instant.email_transport() or 'none'}")
        return 0

    # max_pings_per_day (Phase 3.1). A job over the cap is not dropped: it goes
    # into the digest. Highest scores go first, because `jobs` arrives sorted by
    # descending fit.
    cap = int(settings.get("max_pings_per_day", 0) or 0)
    budget = None
    if cap > 0:
        budget = max(0, cap - pings_sent_today(INSTANT_CHANNELS))
        print(f"instant: {budget} of {cap} left today (IST)\n")

    sent = failed = 0
    # Jobs that still need email, paired with why the instant channels did not
    # take them. Collected so one run produces at most one digest.
    for_email: list[tuple[dict, str]] = []

    for job in jobs:
        if budget is not None and budget <= 0:
            for_email.append((job, f"daily ping cap ({cap}) reached"))
            continue
        delivered, why = [], []
        if instant.ntfy_configured():
            try:
                instant.send_ntfy(instant.ntfy_payload(job, dashboard))
                record(job["job_id"], "ntfy", True, None)
                delivered.append("ntfy")
            except Exception as exc:
                record(job["job_id"], "ntfy", False, str(exc))
                why.append(f"ntfy: {exc}")
        else:
            why.append("ntfy: NTFY_TOPIC not set")
        if instant.email_transport():
            try:
                via = instant.send_email(job, dashboard)
                record(job["job_id"], "email-instant", True, None)
                delivered.append(f"email/{via}")
            except Exception as exc:
                record(job["job_id"], "email-instant", False, str(exc))
                why.append(f"email: {exc}")
        else:
            why.append("email: no transport configured")

        if delivered:
            sent += 1
            if budget is not None:
                budget -= 1
            print(f"  {'+'.join(delivered):<20} {job['fit_score']} {job['title'][:48]}")
        else:
            print(f"  FAILED  {job['title'][:48]}: {'; '.join(why)[:110]}")
            for_email.append((job, "; ".join(why)))

    # One digest for everything the instant channels did not deliver. Dedupe is
    # still keyed per job, so every job in it gets its own notifications row.
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
