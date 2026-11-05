# Dashboard timing: baseline

Measured 2026-11-05 23:10 India Standard Time from the operator's machine (India), 3 loads per page.

| page | status | function region | TTFB median (s) | total median (s) | KB |
|---|---|---|---|---|---|
| queue | 200 | iad1 | 7.84 | 8.03 | 82 |
| funnel | 200 | iad1 | 8.39 | 8.48 | 60 |
| contacts | 200 | iad1 | 6.22 | 6.33 | 253 |
| companies | 200 | iad1 | 4.55 | 4.84 | 191 |
| review | 200 | iad1 | 5.20 | 5.85 | 274 |

## Per-query breakdown (from /api/perf, server side)

### queue: 5949 ms total, 12 requests, 53 round trips

| ms | started at | pages | rows | request |
|---|---|---|---|---|
| 5909 | 35 | 10 | 9281 | `jobs?select=id,posted_at,first_seen_at&closed_at=is.null&order=id.asc` |
| 5837 | 18 | 12 | 11507 | `job_scores?select=job_id,fit_score,reject_reason&order=job_id.asc` |
| 5582 | 37 | 12 | 11507 | `job_scores?select=job_id,fit_score,reject_reason&order=job_id.asc` |
| 4305 | 0 | 10 | 9281 | `jobs?select=id,posted_at,first_seen_at&closed_at=is.null&order=id.asc` |
| 1930 | 33 | 2 | 1118 | `connections?select=id,full_name,company_norm,title,is_batchmate,profile_url,warm&order=warm.desc,is_batchmate.desc.nulls` |
| 760 | 30 | 1 | 23 | `v_queue?select=*&fit_score=gte.6.5&status=eq.queued&location_ok=is.true&age_days=lte.5` |
| 709 | 72 | 1 | 99 | `run_log?select=started_at&worker=eq.ingest&started_at=gte.2026-11-04T17:41:15.857Z&order=id.asc` |
| 693 | 73 | 1 | 15 | `companies?select=name&active=is.false&order=name.asc,id.asc` |
| 347 | 19 | 1 | 227 | `applications?select=applied_at&applied_at=not.is.null&order=id.asc` |
| 316 | 41 | 1 | 1 | `run_log?select=started_at,finished_at,jobs_seen,jobs_new,jobs_closed,trigger_source,duration_s,boards&worker=eq.ingest&o` |
| 296 | 39 | 1 | 227 | `applications?select=applied_at&applied_at=not.is.null&order=id.asc` |
| 294 | 75 | 1 | 501 | `job_scores?select=provider&fit_score=not.is.null&scored_at=gte.2026-10-29T17:41:15.857Z&order=job_id.asc` |

### funnel: 7668 ms total, 9 requests, 51 round trips

| ms | started at | pages | rows | request |
|---|---|---|---|---|
| 6205 | 7 | 12 | 11507 | `job_scores?select=job_id,fit_score,min_years,gap_skills,scored_at&order=job_id.asc` |
| 5416 | 2 | 12 | 11507 | `job_scores?select=job_id,fit_score,reject_reason&order=job_id.asc` |
| 4908 | 8 | 10 | 9281 | `jobs?select=id&closed_at=is.null&order=id.asc` |
| 4316 | 0 | 10 | 9281 | `jobs?select=id,posted_at,first_seen_at&closed_at=is.null&order=id.asc` |
| 871 | 6 | 1 | 325 | `v_funnel?select=*&order=application_id.asc` |
| 865 | 6798 | 2 | 1286 | `companies?select=company_key&order=id.asc` |
| 635 | 3 | 1 | 227 | `applications?select=applied_at&applied_at=not.is.null&order=id.asc` |
| 584 | 6213 | 1 | 0 | `referral_asks?select=outcome&order=id.asc` |
| 432 | 6797 | 2 | 1118 | `connections?select=company_norm&order=id.asc` |

### contacts: 4899 ms total, 5 requests, 26 round trips

| ms | started at | pages | rows | request |
|---|---|---|---|---|
| 4892 | 1 | 12 | 11507 | `job_scores?select=job_id,fit_score,reject_reason&order=job_id.asc` |
| 4533 | 0 | 10 | 9281 | `jobs?select=id,posted_at,first_seen_at&closed_at=is.null&order=id.asc` |
| 1434 | 4 | 2 | 1271 | `companies?select=name,company_key&active=is.true&company_key=not.is.null&order=id.asc` |
| 475 | 5 | 1 | 860 | `connections?select=id,full_name,title,company_norm,profile_url,warm,connected_on&company_norm=not.is.null&order=warm.des` |
| 254 | 3 | 1 | 227 | `applications?select=applied_at&applied_at=not.is.null&order=id.asc` |

### companies: 5475 ms total, 4 requests, 24 round trips

| ms | started at | pages | rows | request |
|---|---|---|---|---|
| 5470 | 0 | 10 | 9281 | `jobs?select=id,posted_at,first_seen_at&closed_at=is.null&order=id.asc` |
| 4948 | 1 | 12 | 11507 | `job_scores?select=job_id,fit_score,reject_reason&order=job_id.asc` |
| 244 | 4 | 1 | 141 | `companies?select=id,name,ats,board_token,category,priority,active,last_ok_at,consecutive_failures&ats=neq.aggregator&ord` |
| 243 | 3 | 1 | 227 | `applications?select=applied_at&applied_at=not.is.null&order=id.asc` |

