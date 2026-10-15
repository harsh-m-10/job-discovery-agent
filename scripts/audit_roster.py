#!/usr/bin/env python
"""Find active boards that cost a fetch every cycle and have never paid off.

    python scripts/audit_roster.py            # report
    python scripts/audit_roster.py --alert    # ...and email if any are found

Found 2026-10-15: 7 of 124 active boards had never produced a job -- two dead
tokens, three tokens that resolved to a *different* company with the same name
(Navi, Porter and Slice were polling US employers), two real companies with no
India roles. Nothing had ever alerted, because the empty-fetch guard only fires
when there are stored rows to protect, and a board that never returned anything
looks exactly like a healthy quiet one. See seeds/companies_retired.yaml.

Cheap by design: the database says which boards have fetched successfully yet
never yielded a job; only those are fetched again, unfiltered, to classify why.

  dead          the board returns no postings at all (token gone, or moved ATS)
  no_india      live board, no India-relevant posting -- either the company
                has no India roles, or the token is someone else's company
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv   # noqa: E402
load_dotenv(ROOT / ".env")

from common.geo import is_india_relevant          # noqa: E402
from ingest.adapters import get_adapter           # noqa: E402
from ingest.models import BoardFetchError         # noqa: E402
from outcomes.run import Db                       # noqa: E402


def suspects(db: Db) -> list[dict]:
    """Active, fetched successfully at least once, and no job ever stored."""
    companies = db.get_all("companies?select=id,name,ats,board_token,last_ok_at"
                           "&active=is.true&last_ok_at=not.is.null")
    produced = {r["company_id"] for r in db.get_all("jobs?select=company_id")}
    return [c for c in companies if c["id"] not in produced]


def classify(company: dict) -> tuple[str, str]:
    try:
        jobs = get_adapter(company["ats"]).fetch(company["board_token"])
    except BoardFetchError as exc:
        return "error", str(exc)[:120]
    if not jobs:
        return "dead", "no postings at all"
    india = [j for j in jobs if is_india_relevant(j.location, j.description)]
    if india:
        # Has India postings now; the next ingest run will store them.
        return "ok", f"{len(india)} India posting(s) now live"
    where = Counter((j.location or "?").split(",")[-1].strip() for j in jobs).most_common(3)
    return "no_india", f"{len(jobs)} postings, none in India; mostly {where}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alert", action="store_true")
    args = ap.parse_args()

    db = Db()
    flagged = []
    for c in suspects(db):
        verdict, detail = classify(c)
        print(f"  {verdict:<9} {c['name']:<22} {c['ats']}/{c['board_token']}: {detail}")
        if verdict in ("dead", "no_india"):
            flagged.append((verdict, c, detail))

    if not flagged:
        print("roster audit: every active board has produced at least one job")
        return 0

    print(f"\nroster audit: {len(flagged)} board(s) cost a fetch every cycle and "
          f"have never produced a job")
    if args.alert:
        from notify.alerts import send_alert
        lines = [f"{len(flagged)} active board(s) have never produced a job:", ""]
        for verdict, c, detail in flagged:
            lines.append(f"  [{verdict}] {c['name']} ({c['ats']}/{c['board_token']}): {detail}")
        lines += ["",
                  "dead: the token is gone or the company moved ATS -- find the new board.",
                  "no_india: no India roles, or the token belongs to a different company",
                  "with the same name (Navi, Porter and Slice did). Check a sample posting.",
                  "",
                  "Retire with companies.active = false and record why in",
                  "seeds/companies_retired.yaml."]
        send_alert("roster_audit", f"{len(flagged)} board(s) never produced a job",
                   "\n".join(lines))
    return 1


if __name__ == "__main__":
    sys.exit(main())
