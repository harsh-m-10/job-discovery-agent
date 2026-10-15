#!/usr/bin/env python
"""(Re)classify jobs.role_family for every row, from the title.

    python scripts/backfill_role_family.py            # write
    python scripts/backfill_role_family.py --dry-run  # show what would change

Run once after db/migrations/0007, and again whenever common/role.py changes:
ingest classifies new and edited rows as it writes them, but a rule change does
not reach rows that have not been touched since. Uses the session pooler
directly because one UPDATE over a VALUES list is a single round trip for
~4,400 rows, where PostgREST would be thousands of PATCHes.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.parse as up
from collections import Counter
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from common.role import role_family   # noqa: E402


def connect():
    ref = up.urlparse(os.environ["SUPABASE_URL"]).hostname.split(".")[0]
    return psycopg2.connect(
        host=os.environ.get("SUPABASE_POOLER_HOST", "aws-0-ap-south-1.pooler.supabase.com"),
        port=5432, user=f"postgres.{ref}", dbname="postgres",
        password=os.environ["SUPABASE_DB_PASSWORD"], connect_timeout=30)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = connect()
    with conn, conn.cursor() as cur:
        cur.execute("select id, title, role_family from jobs")
        rows = cur.fetchall()
        changes = [(job_id, role_family(title)) for job_id, title, current in rows
                   if role_family(title) != current]
        print(f"{len(rows)} jobs, {len(changes)} to (re)classify")
        print("resulting mix:", dict(Counter(role_family(t) for _, t, _ in rows).most_common()))
        if args.dry_run or not changes:
            conn.rollback()
            return 0
        execute_values(
            cur,
            "update jobs set role_family = v.family "
            "from (values %s) as v(id, family) where jobs.id = v.id",
            changes, page_size=1000)
        # cur.rowcount reflects only the last page execute_values sent, so
        # verify instead of trusting it.
        cur.execute("select count(*) from jobs where role_family is null")
        print(f"wrote {len(changes)} rows; unclassified now: {cur.fetchone()[0]}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
