#!/usr/bin/env python
"""Nightly retention (db/migrations/0025).

    python scripts/prune.py            # run it
    python scripts/prune.py --dry-run  # count what it would touch, change nothing

Runs from .github/workflows/backup.yml after the encrypted backup is pushed,
so anything removed is still in that night's backup. The rules live in the
database function prune_old_data(); the window is
config/settings.yaml:retention_days (15).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from common.settings import load_settings   # noqa: E402

DRY_RUN_SQL_NOTE = ("dry run: nothing changed. Of these, ones never pinged, applied to or "
                    "asked about would be deleted; the rest kept without their description.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    days = int(load_settings(ROOT / "config" / "settings.yaml").get("retention_days", 15))
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"
    key = os.environ["SUPABASE_SERVICE_KEY"]
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    if args.dry_run:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = requests.head(f"{base}/jobs?select=id&closed_at=lt.{cutoff}",
                          headers={**h, "Prefer": "count=exact"}, timeout=60)
        r.raise_for_status()
        print(DRY_RUN_SQL_NOTE)
        print(f"closed more than {days} days ago: {r.headers['Content-Range'].rpartition('/')[2]}")
        return 0

    r = requests.post(f"{base}/rpc/prune_old_data", headers=h,
                      data=json.dumps({"p_days": days}), timeout=300)
    if r.status_code >= 300:
        print(f"prune failed: {r.status_code} {r.text[:300]}", file=sys.stderr)
        return 1
    print("retention:", json.dumps(r.json()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
