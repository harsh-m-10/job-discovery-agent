#!/usr/bin/env python
"""Recompute the dashboard's headline numbers (db/migrations/0026).

    python scripts/refresh_snapshot.py

Last step of every ingest run and of the nightly retention. The dashboard
reads the one row this writes instead of aggregating 20k rows per page view
(specs/001-dashboard-speed-retention). Threshold and display age come from
config/settings.yaml so the snapshot and the pings agree.
"""

from __future__ import annotations

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


def main() -> int:
    s = load_settings(ROOT / "config" / "settings.yaml")
    body = {"p_threshold": float(s.get("ping_threshold", 6.5)),
            "p_max_age_days": int(s.get("max_display_age_days", 5))}
    key = os.environ["SUPABASE_SERVICE_KEY"]
    r = requests.post(os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/rpc/refresh_dashboard_snapshot",
                      headers={"apikey": key, "Authorization": f"Bearer {key}",
                               "Content-Type": "application/json"},
                      data=json.dumps(body), timeout=120)
    if r.status_code >= 300:
        print(f"snapshot refresh failed: {r.status_code} {r.text[:300]}", file=sys.stderr)
        return 1
    d = r.json()
    print(f"snapshot: health {d['health']['level']}, queue {d['queue']['total']}, "
          f"open {d['open_jobs']}, unscored {d['unscored']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
