"""LAYER 3. The daily digest (decided 2026-10-15).

    python -m notify.daily              # send
    python -m notify.daily --dry-run    # print, send nothing

Instant pings go out per match (notify/instant.py). This is the evening
look-back that a stream of pings cannot give: everything pinged in the last 24
hours that is still open and not yet acted on, best first, and how big the
untouched queue is. One email a day from the operator's own Gmail -- it is a
summary to read, not an alert to hear, so self-sent is fine.

compose() is pure and tested in tests/test_daily.py.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOURS = 24


def collect(now: datetime | None = None) -> dict:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from common.settings import load_settings
    from notify.run import db_headers, get_all

    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(hours=HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    h = db_headers()
    threshold = float(load_settings().get("ping_threshold", 6.5))
    pinged = {r["job_id"] for r in get_all(
        f"notifications?select=job_id&ok=is.true&job_id=not.is.null&sent_at=gte.{since}", h)}
    rows = []
    if pinged:
        ids = ",".join(str(i) for i in sorted(pinged))
        rows = get_all(f"v_queue?select=job_id,title,company,location,absolute_url,fit_score,"
                       f"provisional,status&location_ok=is.true&job_id=in.({ids})", h)
    queued = get_all(f"v_queue?select=job_id&status=eq.queued&location_ok=is.true"
                     f"&fit_score=gte.{threshold}", h)
    return {"now": now, "threshold": threshold, "pinged": len(pinged), "rows": rows,
            "queued": len(queued)}


def compose(d: dict, dashboard: str = "") -> tuple[str, str] | None:
    """-> (subject, body), or None when there is nothing worth a mail."""
    rows = sorted(d["rows"], key=lambda r: -float(r.get("fit_score") or 0))
    open_rows = [r for r in rows if (r.get("status") or "queued") == "queued"]
    if not d["pinged"] and not d["queued"]:
        return None
    lines = [f"Last {HOURS}h: {d['pinged']} match(es) pinged; "
             f"{len(open_rows)} still untouched. Whole queue at or above "
             f"{d['threshold']:g}: {d['queued']}.", ""]
    for r in open_rows:
        flag = " (provisional)" if r.get("provisional") else ""
        lines.append(f"{float(r['fit_score']):.1f}{flag}  {r['title']} - {r['company']}"
                     + (f" ({r['location']})" if r.get("location") else ""))
        lines.append(f"      {r.get('absolute_url') or ''}")
    acted = len(rows) - len(open_rows)
    if acted:
        lines += ["", f"Already acted on: {acted}."]
    if dashboard:
        lines += ["", f"Queue: {dashboard.rstrip('/')}"]
    top = f" — top {float(open_rows[0]['fit_score']):.1f} {open_rows[0]['title']}" if open_rows else ""
    subject = f"[job agent] daily: {len(open_rows)} untouched of {d['pinged']} pinged{top}"
    return subject, "\n".join(lines) + "\n"


def main() -> int:
    import os
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    out = compose(collect(), os.environ.get("DASHBOARD_URL", ""))
    if out is None:
        print("nothing pinged and nothing queued — no mail")
        return 0
    subject, body = out
    print(f"Subject: {subject}\n\n{body}")
    if args.dry_run:
        return 0
    from notify import email as email_channel
    print(f"emailed via {email_channel.send(subject, body)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
