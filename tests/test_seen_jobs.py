"""Seen record and never-ping-twice (db/migrations/0026; spec 001 FR-011/012).

    python tests/test_seen_jobs.py postgresql://.../source

Runs against a throwaway database that has every migration and
tests/fixtures/backup_seed.sql applied (the backup-selftest job in CI). All
changes happen inside one transaction that is rolled back.
"""

import sys

import psycopg2


def check(cur) -> list[str]:
    bad = []
    cur.execute("select id from companies order by id limit 1")
    company = cur.fetchone()[0]

    # A posting removed by retention is in seen_jobs ...
    cur.execute("insert into seen_jobs (company_id, ats_job_id, content_hash, source, pinged_at) "
                "values (%s, 'gone-1', 'h', 'ats', now())", (company,))
    # ... and the board lists it again: the insert must be dropped.
    cur.execute("insert into jobs (company_id, ats_job_id, title, absolute_url, content_hash) "
                "values (%s, 'gone-1', 'SWE', 'https://x/1', 'h2') returning id", (company,))
    if cur.fetchone() is not None:
        bad.append("a seen posting was re-inserted into jobs")

    # The upsert path the pipeline uses (ON CONFLICT DO UPDATE) is dropped too.
    cur.execute("insert into jobs (company_id, ats_job_id, title, absolute_url, content_hash) "
                "values (%s, 'gone-1', 'SWE', 'https://x/1', 'h3') "
                "on conflict (company_id, ats_job_id) do update set title = excluded.title "
                "returning id", (company,))
    if cur.fetchone() is not None:
        bad.append("a seen posting was re-inserted through the upsert path")

    # Ingest reads job_keys: the key is there, flagged seen_only.
    cur.execute("select seen_only from job_keys where company_id = %s and ats_job_id = 'gone-1'",
                (company,))
    rows = cur.fetchall()
    if rows != [(True,)]:
        bad.append(f"job_keys should list the seen key once as seen_only, got {rows}")

    # A brand-new posting still goes in.
    cur.execute("insert into jobs (company_id, ats_job_id, title, absolute_url, content_hash) "
                "values (%s, 'fresh-1', 'SWE', 'https://x/2', 'h') returning id", (company,))
    if cur.fetchone() is None:
        bad.append("a new posting was dropped")
    cur.execute("select seen_only from job_keys where company_id = %s and ats_job_id = 'fresh-1'",
                (company,))
    if cur.fetchall() != [(False,)]:
        bad.append("a live posting should appear once in job_keys, not seen_only")

    # pinged_at is the dedupe notify reads; a pinged job is never a candidate.
    cur.execute("update jobs set pinged_at = now() where ats_job_id = 'fresh-1'")
    cur.execute("select count(*) from jobs where ats_job_id = 'fresh-1' and pinged_at is null")
    if cur.fetchone()[0] != 0:
        bad.append("pinged_at did not stick")
    return bad


def main() -> int:
    conn = psycopg2.connect(sys.argv[1])
    try:
        with conn.cursor() as cur:
            bad = check(cur)
    finally:
        conn.rollback()
        conn.close()
    if bad:
        print("SEEN JOBS FAILED:\n  " + "\n  ".join(bad))
        return 1
    print("seen jobs: removed postings stay out; new ones go in; pinged_at sticks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
