"""Seen record and never-ping-twice (db/migrations/0026; spec 001 FR-011/012).

    python tests/test_seen_jobs.py postgresql://.../source

Runs against a throwaway database that has every migration and
tests/fixtures/backup_seed.sql applied (the backup-selftest job in CI). All
changes happen inside one transaction that is rolled back.
"""

import sys

import psycopg2

RETENTION_RAN = False


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


def _job(cur, company, key, days_old, *, verdict="strong", pinged=False, closed_days=None):
    cur.execute(
        "insert into jobs (company_id, ats_job_id, title, absolute_url, content_hash, description,"
        " first_seen_at, pinged_at, closed_at) values (%s, %s, 'SWE', 'https://x', 'h', 'JD text',"
        " now() - make_interval(days => %s), case when %s then now() - interval '80 days' end,"
        " case when %s::int is null then null else now() - make_interval(days => %s::int) end)"
        " returning id", (company, key, days_old, pinged, closed_days, closed_days))
    jid = cur.fetchone()[0]
    cur.execute("insert into job_scores (job_id, fit_score, verdict, location_ok) "
                "values (%s, %s, %s, true)", (jid, 2 if verdict == "reject" else 8, verdict))
    return jid


def check_retention(cur) -> list[str]:
    """prune_old_data_v2 (0028) against the operator's windows."""
    bad = []
    cur.execute("select id from companies order by id limit 1")
    company = cur.fetchone()[0]
    rejected = _job(cur, company, "r-31", 31, verdict="reject")
    applied = _job(cur, company, "a-100", 100)
    cur.execute("insert into applications (job_id, status, applied_at) values (%s, 'applied', now())",
                (applied,))
    pinged_old = _job(cur, company, "p-91", 91, pinged=True)
    young = _job(cur, company, "y-40", 40)
    fresh_reject = _job(cur, company, "r-5", 5, verdict="reject")
    closed_old = _job(cur, company, "c-95", 95, closed_days=92)

    def count_jobs():
        cur.execute("select count(*) from jobs"); return cur.fetchone()[0]

    before = count_jobs()
    cur.execute("select prune_old_data_v2(true)")
    dry = cur.fetchone()[0]
    if count_jobs() != before:
        bad.append("dry run deleted rows")
    cur.execute("select prune_old_data_v2(false)")
    real = cur.fetchone()[0]
    if dry.get("jobs_deleted") != real.get("jobs_deleted"):
        bad.append(f"dry run counted {dry.get('jobs_deleted')}, real run deleted {real.get('jobs_deleted')}")

    cur.execute("select id, description is not null from jobs where id = any(%s)",
                ([rejected, applied, pinged_old, young, fresh_reject, closed_old],))
    left = dict(cur.fetchall())
    for name, jid in (("rejected 31d", rejected), ("pinged 91d", pinged_old),
                      ("closed 92d", closed_old)):
        if jid in left:
            bad.append(f"{name} should be deleted")
    if left.get(applied) is not True:
        bad.append("an applied job must be kept with its description")
    if left.get(young) is not False:
        bad.append("a 40-day job must be kept without its description")
    if left.get(fresh_reject) is not True:
        bad.append("a 5-day rejected job must be untouched")
    cur.execute("select ats_job_id, pinged_at is not null from seen_jobs "
                "where ats_job_id in ('r-31', 'p-91', 'c-95') order by 1")
    seen = dict(cur.fetchall())
    if seen != {"c-95": False, "p-91": True, "r-31": False}:
        bad.append(f"seen_jobs should remember all three, p-91 as pinged: {seen}")
    cur.execute("select count(*) from applications where job_id = %s", (applied,))
    if cur.fetchone()[0] != 1:
        bad.append("an application was touched")
    return bad


def main() -> int:
    conn = psycopg2.connect(sys.argv[1])
    try:
        with conn.cursor() as cur:
            bad = check(cur)
            cur.execute("select to_regproc('prune_old_data_v2') is not null")
            if cur.fetchone()[0]:
                bad += check_retention(cur)
                global RETENTION_RAN
                RETENTION_RAN = True
    finally:
        conn.rollback()
        conn.close()
    if bad:
        print("SEEN JOBS FAILED:\n  " + "\n  ".join(bad))
        return 1
    print("seen jobs: removed postings stay out; new ones go in; pinged_at sticks"
          + ("; retention v2 windows hold" if RETENTION_RAN else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
