#!/usr/bin/env python
"""EXPLAIN ANALYZE the hot queries (specs/001-dashboard-speed-retention FR-009).

    python scripts/explain_hot.py --label before

Read-only: EXPLAIN ANALYZE on SELECTs only, inside a rolled-back transaction.
Writes specs/001-dashboard-speed-retention/explain-<label>.md.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
import psycopg2  # noqa: E402

import backup  # noqa: E402

HOT = {
    "queue page (dashboard)":
        "select job_id, title, fit_score from v_queue where fit_score >= 6.5 and status = 'queued' "
        "and location_ok and age_days <= 5 order by fit_score desc, job_id limit 26",
    "notify candidates: scores":
        "select job_id from job_scores where fit_score >= 6.5 and location_ok",
    "notify candidates: unpinged open jobs":
        "select id from jobs where closed_at is null and pinged_at is null "
        "and id = any(array(select job_id from job_scores where fit_score >= 6.5 and location_ok))",
    "ingest: job_keys for one board":
        "select * from job_keys where company_id = (select company_id from jobs "
        "group by 1 order by count(*) desc limit 1)",
    "funnel: 30 days of LLM scores":
        "select job_id, fit_score, gap_skills, scored_at from job_scores "
        "where fit_score is not null and scored_at >= now() - interval '30 days'",
    "referrals for queue companies":
        "select id from connections where company_norm in (select company_key from companies "
        "where company_key is not null limit 20)",
    "pipeline: applications by status":
        "select id from applications where status = 'applied' order by updated_at desc limit 25",
    "retention: stale unreferenced jobs":
        "select id from jobs j where first_seen_at < now() - interval '30 days' "
        "and not exists (select 1 from applications a where a.job_id = j.id)",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    args = ap.parse_args()
    conn = psycopg2.connect(**backup.source_params())
    out = [f"# Query plans: {args.label}", ""]
    with conn.cursor() as cur:
        for name, sql in HOT.items():
            cur.execute("explain (analyze, buffers, format text) " + sql)
            plan = "\n".join(r[0] for r in cur.fetchall())
            ms = re.search(r"Execution Time: ([\d.]+) ms", plan)
            seq = sorted(set(re.findall(r"Seq Scan on (\w+)", plan)))
            out += [f"## {name}: {ms.group(1) if ms else '?'} ms"
                    f"{'  (seq scan: ' + ', '.join(seq) + ')' if seq else ''}",
                    "", "```", plan, "```", ""]
            print(f"{name:42} {ms.group(1) if ms else '?':>9} ms  seq={seq}")
    conn.rollback()
    path = ROOT / "specs" / "001-dashboard-speed-retention" / f"explain-{args.label}.md"
    path.write_text("\n".join(out), encoding="utf-8")
    print("written:", path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
