#!/usr/bin/env python
"""Set connections.is_batchmate / near_batch from the year window (PLAN 5.3).

    python scripts/flag_batchmates.py --dry-run
    python scripts/flag_batchmates.py

The window is config/settings.yaml:referral_warmth. Import applies it too;
this re-applies it to rows already in the table, e.g. after the window changes.
Two PATCHes per tier, by year range, so the run is a handful of requests.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from common.settings import load_settings          # noqa: E402
from score.referral import warmth_windows          # noqa: E402


def year_range(lo: int, hi: int) -> str:
    return f"connected_on=gte.{lo}-01-01&connected_on=lte.{hi}-12-31"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    batch, near = warmth_windows(load_settings(ROOT / "config" / "settings.yaml"))
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/connections"
    key = os.environ["SUPABASE_SERVICE_KEY"]
    h = {"apikey": key, "Authorization": f"Bearer {key}",
         "Content-Type": "application/json", "Prefer": "return=minimal,count=exact"}

    def count(q: str) -> int:
        r = requests.head(f"{base}?select=id&{q}", headers={**h, "Prefer": "count=exact"},
                          timeout=60)
        r.raise_for_status()
        return int(r.headers["Content-Range"].rpartition("/")[2])

    n_batch = count(year_range(*batch))
    n_near = count(year_range(*near)) - n_batch
    total = count("id=gt.0")
    print(f"batch {batch[0]}-{batch[1]}: {n_batch}; near-batch {near[0]}-{near[1]}: "
          f"{n_near}; of {total}")
    if args.dry_run:
        return 0

    steps = [
        ("id=gt.0", {"is_batchmate": False, "near_batch": False}),
        (year_range(*near), {"near_batch": True}),
        (year_range(*batch), {"is_batchmate": True, "near_batch": False}),
    ]
    for q, body in steps:
        requests.patch(f"{base}?{q}", headers=h, json=body, timeout=120).raise_for_status()
    print("flags written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
