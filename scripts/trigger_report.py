#!/usr/bin/env python
"""How well is the trigger plane working? Phase 2's acceptance check, as a command.

    python scripts/trigger_report.py            # last 48 hours
    python scripts/trigger_report.py --hours 168

docs/PLAN.md Phase 2 sets three numbers to hit after 48 hours live:
  * median interval between automated runs <= 17 min, p90 <= 30 min
  * trigger_source shows >= 90% "cronjob"
  * zero overlapping runs

Before Phase 2 the median was 302 minutes. This reads run_log only, so it can
run anywhere the Supabase service key is available. Manual runs are excluded
from the interval statistics -- a spot check with --only is not a poll.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

TARGET_MEDIAN_MIN = 17
TARGET_P90_MIN = 30
TARGET_PRIMARY_SHARE = 0.90


def parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def fetch(hours: float) -> list[dict]:
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"
    key = os.environ["SUPABASE_SERVICE_KEY"]
    since = quote((datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat())
    resp = requests.get(
        f"{url}/run_log?select=started_at,finished_at,trigger_source"
        f"&worker=eq.ingest&started_at=gte.{since}&order=started_at.asc",
        headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=60)
    resp.raise_for_status()
    return resp.json()


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct * (len(ordered) - 1))))
    return ordered[index]


def overlaps(rows: list[dict]) -> int:
    """Runs that started before the previous one finished."""
    count = 0
    finished = [r for r in rows if r.get("finished_at")]
    for prev, cur in zip(finished, finished[1:]):
        if parse(cur["started_at"]) < parse(prev["finished_at"]):
            count += 1
    return count


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48)
    args = ap.parse_args()

    rows = fetch(args.hours)
    automated = [r for r in rows if r.get("trigger_source") not in (None, "manual")]
    sources = Counter(r.get("trigger_source") or "unrecorded" for r in rows)

    print(f"run_log, last {args.hours:g}h: {len(rows)} ingest runs, "
          f"{len(automated)} automated\n")
    print("by trigger:")
    for source, n in sources.most_common():
        print(f"  {source:<12} {n:>4}")

    if len(automated) < 2:
        print("\nfewer than two automated runs — no interval to measure yet")
        return 1

    starts = [parse(r["started_at"]) for r in automated]
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(starts, starts[1:])]
    median, p90 = statistics.median(gaps), percentile(gaps, 0.90)
    primary = sources.get("cronjob", 0) / len(automated)
    overlap = overlaps(rows)

    def mark(ok: bool) -> str:
        return "PASS" if ok else "FAIL"

    print(f"\ninterval between automated runs (min): median {median:.1f}, "
          f"p90 {p90:.1f}, max {max(gaps):.1f}")
    print()
    print(f"  {mark(median <= TARGET_MEDIAN_MIN)}  median <= {TARGET_MEDIAN_MIN} min")
    print(f"  {mark(p90 <= TARGET_P90_MIN)}  p90 <= {TARGET_P90_MIN} min")
    print(f"  {mark(primary >= TARGET_PRIMARY_SHARE)}  cronjob share "
          f"{primary:.0%} >= {TARGET_PRIMARY_SHARE:.0%}")
    print(f"  {mark(overlap == 0)}  overlapping runs: {overlap}")

    ok = (median <= TARGET_MEDIAN_MIN and p90 <= TARGET_P90_MIN
          and primary >= TARGET_PRIMARY_SHARE and overlap == 0)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
