#!/usr/bin/env python
"""Process outcome emails: classify, match, and update application status.

    python -m outcomes.run              # process pending rows, apply effects
    python -m outcomes.run --dry-run    # show what would happen, write nothing

Runs as a step of the ingest workflow after notify (docs/PLAN.md 4.3). Rows
arrive raw from the dashboard's /api/events, posted by the Apps Script in the
operator's Gmail (apps_script/outcomes.gs).

Status is never written from a single email. For every application with a
matched email, it is recomputed from the *latest* significant email
(rejection, assessment, interview), so emails can arrive in any order -- a
backfilled rejection from last month cannot overwrite this week's interview --
and processing the same rows twice changes nothing. Two things are never
overridden: an "offer", and any status the operator set by hand after the
latest email (applications.updated_at later than it).
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from outcomes.classify import classify          # noqa: E402
from outcomes.match import App, match           # noqa: E402

PAGE = 1000
SIGNIFICANT = {"rejection": "rejected", "interview_invite": "interviewing",
               "assessment": "responded"}


def parse(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


# --- the status rule (pure) -----------------------------------------------

@dataclass(frozen=True)
class Event:
    kind: str
    received_at: datetime


def next_status(status: str, updated_at: datetime | None, responded_at: datetime | None,
                events: list[Event]) -> dict:
    """-> the PATCH to apply to one application, or {} for no change.

    `updated_at` is written as the latest email's own time, not "now", so a
    later email -- even one processed in the same run that wrote the previous
    status -- still applies, while a manual change made after it does not
    get overwritten.
    """
    significant = sorted((e for e in events if e.kind in SIGNIFICANT),
                         key=lambda e: e.received_at)
    if not significant:
        return {}
    latest = significant[-1]
    patch: dict = {}
    if responded_at is None:
        # Mirrors /api/status: a rejection is a response too.
        patch["responded_at"] = significant[0].received_at.isoformat()
    # A manual status set after the latest email wins -- except "applied",
    # which is the baseline, not a decision. The dashboard's Applied click
    # stamps updated_at = now, and an automated rejection can land minutes
    # after applying but before the click; it must still apply.
    manual_is_newer = (status != "applied" and updated_at is not None
                       and updated_at > latest.received_at)
    target = SIGNIFICANT[latest.kind]
    if status != "offer" and not manual_is_newer and status != target:
        patch["status"] = target
        patch["updated_at"] = latest.received_at.isoformat()
    return patch


# --- PostgREST ---------------------------------------------------------------

class Db:
    def __init__(self):
        url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        if not url or not key:
            raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        self.base = f"{url}/rest/v1"
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json"}

    def get_all(self, path: str) -> list[dict]:
        rows: list[dict] = []
        for page in range(50):
            off = page * PAGE
            r = requests.get(f"{self.base}/{path}", timeout=60,
                             headers={**self.headers, "Range-Unit": "items",
                                      "Range": f"{off}-{off + PAGE - 1}"})
            r.raise_for_status()
            batch = r.json()
            rows.extend(batch)
            if len(batch) < PAGE:
                return rows
        raise RuntimeError(f"{path}: runaway result")

    def patch(self, table: str, query: str, body: dict) -> None:
        r = requests.patch(f"{self.base}/{table}?{query}", json=body, timeout=60,
                           headers={**self.headers, "Prefer": "return=minimal"})
        r.raise_for_status()


def load_apps(db: Db) -> list[App]:
    rows = db.get_all("applications?select=id,status,applied_at,"
                      "jobs(title,companies(company_key))")
    out = []
    for r in rows:
        job = r.get("jobs") or {}
        out.append(App(id=r["id"], status=r["status"], applied_at=parse(r.get("applied_at")),
                       company_key=((job.get("companies") or {}).get("company_key") or ""),
                       title=job.get("title") or ""))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    db = Db()
    now = datetime.now(timezone.utc).isoformat()

    try:
        pending = db.get_all("application_events?select=*&match_state=eq.pending"
                             "&order=received_at.asc")
    except requests.HTTPError as exc:
        # Before migration 0010 the table does not exist. Not this step's job
        # to fail the pipeline over that.
        print(f"outcomes: application_events unavailable ({exc}); skipping")
        return 0

    apps = load_apps(db)
    tally: dict[str, int] = {}
    for ev in pending:
        kind = classify(ev.get("sender"), ev.get("subject"), ev.get("snippet"))
        m = match(ev.get("sender"), ev.get("subject"), ev.get("snippet"),
                  parse(ev["received_at"]), kind, apps)
        tally[m.state] = tally.get(m.state, 0) + 1
        print(f"  {kind:<17} {m.state:<12} app={m.application_id or '-':<5} "
              f"{(ev.get('subject') or '')[:60]}")
        if not args.dry_run:
            db.patch("application_events", f"id=eq.{ev['id']}", {
                "kind": kind, "match_state": m.state, "application_id": m.application_id,
                "candidates": {"applications": list(m.candidates),
                               "companies": list(m.companies)},
                "resolved_by": "auto" if m.state == "matched" else None,
                "processed_at": now,
            })

    # Effects: every application with a matched email whose effect is not yet
    # applied -- including ones the operator resolved in the review queue.
    todo = db.get_all("application_events?select=application_id"
                      "&match_state=eq.matched&effect_applied=is.false")
    # (In a dry run the rows classified above were not written, so their
    # effects do not appear below; only previously matched rows do.)
    touched = {r["application_id"] for r in todo if r["application_id"]}
    changed = 0
    for app_id in sorted(touched):
        events = [Event(e["kind"], parse(e["received_at"])) for e in db.get_all(
            f"application_events?select=kind,received_at&application_id=eq.{app_id}"
            f"&match_state=eq.matched")]
        app = db.get_all(f"applications?select=status,updated_at,responded_at&id=eq.{app_id}")[0]
        patch = next_status(app["status"], parse(app.get("updated_at")),
                            parse(app.get("responded_at")), events)
        if patch:
            changed += 1
            print(f"  application {app_id}: {app['status']} -> {patch.get('status', app['status'])}")
            if not args.dry_run:
                db.patch("applications", f"id=eq.{app_id}", patch)
        if not args.dry_run:
            db.patch("application_events",
                     f"application_id=eq.{app_id}&match_state=eq.matched&effect_applied=is.false",
                     {"effect_applied": True})

    print(f"\noutcomes: {len(pending)} new email(s) {tally or ''}; "
          f"{changed} application(s) updated{' (dry run)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
