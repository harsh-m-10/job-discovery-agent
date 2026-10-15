"""Assert a restored backup of tests/fixtures/backup_seed.sql is value-exact.

    python tests/fixtures/check_backup_roundtrip.py postgresql://.../target

Row counts (what scripts/backup.py verify checks) prove nothing went missing.
This proves nothing was *changed*: the column types a dump/restore could
plausibly mangle -- non-ASCII text, text[], jsonb, numeric, timestamptz, and
the 0006 check-constrained precision column -- come back exactly as seeded.
Used by the backup self-test job in .github/workflows/ci.yml.
"""

import sys
from datetime import datetime, timezone
from decimal import Decimal

import psycopg2

EXPECTED = {
    "select description from jobs where ats_job_id = 'g-1'":
        "Build things — with unicode ✓",
    "select posted_at_precision from jobs where ats_job_id = 'w-1'": "date",
    "select posted_at from jobs where ats_job_id = 'g-1'":
        datetime(2026, 10, 15, 8, 30, tzinfo=timezone.utc),
    "select matched_skills from job_scores where job_id = 1": ["python", "postgres"],
    "select gap_skills from job_scores where job_id = 2": None,
    "select fit_score from job_scores where job_id = 1": Decimal("8.5"),
    "select raw from jobs where ats_job_id = 'g-1'": {"k": [1, 2]},
    "select boards from run_log limit 1": [{"company": "Acme", "seen": 2}],
    "select hours_since_posted from applications limit 1": Decimal("1.5"),
    "select referral_contact_id is not null from applications limit 1": True,
}


def main() -> int:
    conn = psycopg2.connect(sys.argv[1])
    conn.set_client_encoding("UTF8")
    failures = []
    with conn.cursor() as cur:
        for query, want in EXPECTED.items():
            cur.execute(query)
            got = cur.fetchone()[0]
            if isinstance(got, datetime):
                got = got.astimezone(timezone.utc)
            if got != want:
                failures.append(f"{query}\n    got  {got!r}\n    want {want!r}")
    if failures:
        print("ROUND TRIP CHANGED VALUES:\n  " + "\n  ".join(failures))
        return 1
    print(f"round trip exact: {len(EXPECTED)} values identical after restore")
    return 0


if __name__ == "__main__":
    sys.exit(main())
