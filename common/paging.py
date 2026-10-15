"""Deterministic order for every paged PostgREST read.

    from common.paging import stable_order
    stable_order("jobs", "select=id&order=posted_at.desc")
    # -> "select=id&order=posted_at.desc,id.asc"

PostgREST pages with Range headers, i.e. LIMIT/OFFSET, and LIMIT/OFFSET is only
consistent across requests if the ORDER BY is unique. Without one, Postgres may
return rows in a different order on each page request -- a parallel or
synchronized sequential scan can start mid-table -- so pages overlap and other
rows are never returned.

Found 2026-10-15, twice in one day. On Standard Chartered's career site
(fixed in its adapter), and then in this project's own reads: the scorer pages
open jobs by `posted_at`, which is not unique (thousands of Workday rows share
midnight), and once more than 1,000 jobs needed scoring the pages overlapped
and the job_scores upsert failed on a duplicate key. Every paged reader in
the project now routes its query through this.
"""

from __future__ import annotations

# Unique key per table or view the project pages through.
PRIMARY_KEY = {
    "jobs": "id",
    "companies": "id",
    "job_scores": "job_id",
    "connections": "id",
    "applications": "id",
    "notifications": "id",
    "run_log": "id",
    "application_events": "id",
    "referral_asks": "id",
    "v_funnel": "application_id",
}


def stable_order(table: str, query: str) -> str:
    """Append the table's primary key to the query's ORDER BY.

    Unknown tables are returned unchanged rather than guessed at.
    """
    pk = PRIMARY_KEY.get(table.split("?")[0].strip("/"))
    if not pk:
        return query
    parts = query.split("&") if query else []
    for i, part in enumerate(parts):
        if part.startswith("order="):
            columns = [c.split(".")[0] for c in part[len("order="):].split(",")]
            if pk not in columns:
                parts[i] = f"{part},{pk}.asc"
            return "&".join(parts)
    return "&".join(parts + [f"order={pk}.asc"])
