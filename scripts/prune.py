#!/usr/bin/env python
"""Nightly retention (db/migrations/0028; specs/001-dashboard-speed-retention).

    python scripts/prune.py --dry-run    # exact counts of what would change; changes nothing
    python scripts/prune.py              # apply
    python scripts/prune.py --report     # also append before/after to the spec directory

Runs from .github/workflows/backup.yml after the encrypted backup is pushed,
so anything removed is still in that night's backup. The rules live in the
database function prune_old_data_v2(); the windows are config/settings.yaml:
retention. Applications, outcome emails, referral asks and warm tags are never
deleted; deleted postings keep their identity in seen_jobs for 180 days so
they are never stored or pinged again.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from common.settings import load_settings   # noqa: E402

DEFAULTS = {"descriptions_days": 30, "rejected_days": 30, "stale_days": 90,
            "notifications_days": 30, "run_log_days": 90, "seen_days": 180,
            "trigger_log_days": 30, "alert_inbox_days": 30}
PROTECTED = ("applications", "application_events", "referral_asks", "connections")


def rpc(name: str, body: dict) -> dict:
    key = os.environ["SUPABASE_SERVICE_KEY"]
    r = requests.post(f"{os.environ['SUPABASE_URL'].rstrip('/')}/rest/v1/rpc/{name}",
                      headers={"apikey": key, "Authorization": f"Bearer {key}",
                               "Content-Type": "application/json"},
                      data=json.dumps(body), timeout=600)
    if r.status_code >= 300:
        raise RuntimeError(f"{name}: {r.status_code} {r.text[:300]}")
    return r.json()


def windows() -> dict:
    cfg = load_settings(ROOT / "config" / "settings.yaml").get("retention") or {}
    return {k: int(cfg.get(k, v)) for k, v in DEFAULTS.items()}


def mb(n: int) -> str:
    return f"{n / 1_048_576:.1f} MB"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true",
                    help="append before/after to specs/001-dashboard-speed-retention/")
    args = ap.parse_args()

    w = windows()
    before = rpc("db_size_report", {})
    try:
        result = rpc("prune_old_data_v2", {
            "p_dry_run": args.dry_run, "p_desc_days": w["descriptions_days"],
            "p_reject_days": w["rejected_days"], "p_stale_days": w["stale_days"],
            "p_notif_days": w["notifications_days"], "p_runlog_days": w["run_log_days"],
            "p_seen_days": w["seen_days"], "p_trigger_days": w["trigger_log_days"],
            "p_inbox_days": w["alert_inbox_days"]})
    except RuntimeError as exc:
        print(f"prune failed: {exc}", file=sys.stderr)
        return 1
    after = rpc("db_size_report", {})

    # Never-delete tables must be identical (SC-006).
    changed = [t for t in PROTECTED
               if before["tables"].get(t, {}).get("rows") != after["tables"].get(t, {}).get("rows")]

    lines = [f"retention {'(dry run: nothing changed)' if args.dry_run else ''}",
             f"  windows: {json.dumps(w)}",
             f"  result:  {json.dumps(result)}",
             f"  database: {mb(before['db_bytes'])} -> {mb(after['db_bytes'])}"
             " (space is reused by Postgres; the file shrinks only after VACUUM FULL)"]
    lines.append("  tables (rows before -> after, size after):")
    for t in sorted(before["tables"], key=lambda t: -before["tables"][t]["bytes"])[:14]:
        b, a = before["tables"][t], after["tables"].get(t, {"rows": 0, "bytes": 0})
        lines.append(f"    {t:22} {b['rows']:>7} -> {a['rows']:>7}  {mb(a['bytes'])}")
    if changed:
        lines.append(f"  NOTE: row estimates moved for protected tables {changed} "
                     "(n_live_tup is a statistic; new rows during the run also count)")
    text = "\n".join(lines)
    print(text)

    if args.report:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = ROOT / "specs" / "001-dashboard-speed-retention" / f"retention-{day}.md"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n## {datetime.now(timezone.utc):%H:%M UTC}\n\n```\n{text}\n```\n")
        print(f"report appended: {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
