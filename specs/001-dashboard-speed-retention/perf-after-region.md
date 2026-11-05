# Dashboard timing: after-region

Measured 2026-11-05 23:15 India Standard Time from the operator's machine (India), 3 loads per page.

| page | status | function region | TTFB median (s) | total median (s) | KB |
|---|---|---|---|---|---|
| queue | 200 | iad1 | 5.22 | 5.53 | 83 |
| funnel | 200 | bom1 | 1.57 | 1.57 | 60 |
| contacts | 200 | bom1 | 1.24 | 1.45 | 253 |
| companies | 200 | bom1 | 1.25 | 1.25 | 191 |
| review | 200 | bom1 | 1.16 | 1.17 | 274 |

## Per-query breakdown (from /api/perf, server side)

### queue: 5705 ms total, 12 requests, 53 round trips

| ms | started at | pages | rows | request |
|---|---|---|---|---|
| 5694 | 6 | 12 | 11507 | `job_scores?select=job_id,fit_score,reject_reason&order=job_id.asc` |
| 4573 | 2 | 12 | 11507 | `job_scores?select=job_id,fit_score,reject_reason&order=job_id.asc` |
| 4022 | 5 | 10 | 9298 | `jobs?select=id,posted_at,first_seen_at&closed_at=is.null&order=id.asc` |
| 3471 | 0 | 10 | 9298 | `jobs?select=id,posted_at,first_seen_at&closed_at=is.null&order=id.asc` |
| 906 | 4 | 2 | 1118 | `connections?select=id,full_name,company_norm,title,is_batchmate,profile_url,warm&order=warm.desc,is_batchmate.desc.nulls` |
| 292 | 9 | 1 | 99 | `run_log?select=started_at&worker=eq.ingest&started_at=gte.2026-11-04T17:46:04.191Z&order=id.asc` |
| 290 | 8 | 1 | 1 | `run_log?select=started_at,finished_at,jobs_seen,jobs_new,jobs_closed,trigger_source,duration_s,boards&worker=eq.ingest&o` |
| 264 | 10 | 1 | 501 | `job_scores?select=provider&fit_score=not.is.null&scored_at=gte.2026-10-29T17:46:04.191Z&order=job_id.asc` |
| 261 | 4 | 1 | 23 | `v_queue?select=*&fit_score=gte.6.5&status=eq.queued&location_ok=is.true&age_days=lte.5` |
| 247 | 3 | 1 | 227 | `applications?select=applied_at&applied_at=not.is.null&order=id.asc` |
| 240 | 7 | 1 | 227 | `applications?select=applied_at&applied_at=not.is.null&order=id.asc` |
| 239 | 9 | 1 | 15 | `companies?select=name&active=is.false&order=name.asc,id.asc` |

### funnel: 692 ms total, 9 requests, 51 round trips

| ms | started at | pages | rows | request |
|---|---|---|---|---|
| 595 | 23 | 12 | 11507 | `job_scores?select=job_id,fit_score,min_years,gap_skills,scored_at&order=job_id.asc` |
| 571 | 0 | 10 | 9298 | `jobs?select=id,posted_at,first_seen_at&closed_at=is.null&order=id.asc` |
| 530 | 17 | 12 | 11507 | `job_scores?select=job_id,fit_score,reject_reason&order=job_id.asc` |
| 495 | 25 | 10 | 9298 | `jobs?select=id&closed_at=is.null&order=id.asc` |
| 139 | 21 | 1 | 325 | `v_funnel?select=*&order=application_id.asc` |
| 116 | 19 | 1 | 227 | `applications?select=applied_at&applied_at=not.is.null&order=id.asc` |
| 48 | 640 | 2 | 1118 | `connections?select=company_norm&order=id.asc` |
| 46 | 640 | 2 | 1287 | `companies?select=company_key&order=id.asc` |
| 20 | 619 | 1 | 0 | `referral_asks?select=outcome&order=id.asc` |

### contacts: 397 ms total, 5 requests, 26 round trips

| ms | started at | pages | rows | request |
|---|---|---|---|---|
| 392 | 0 | 10 | 9298 | `jobs?select=id,posted_at,first_seen_at&closed_at=is.null&order=id.asc` |
| 381 | 2 | 12 | 11507 | `job_scores?select=job_id,fit_score,reject_reason&order=job_id.asc` |
| 91 | 4 | 2 | 1272 | `companies?select=name,company_key&active=is.true&company_key=not.is.null&order=id.asc` |
| 75 | 5 | 1 | 860 | `connections?select=id,full_name,title,company_norm,profile_url,warm,connected_on&company_norm=not.is.null&order=warm.des` |
| 54 | 3 | 1 | 227 | `applications?select=applied_at&applied_at=not.is.null&order=id.asc` |

### companies: 508 ms total, 4 requests, 24 round trips

| ms | started at | pages | rows | request |
|---|---|---|---|---|
| 489 | 13 | 12 | 11507 | `job_scores?select=job_id,fit_score,reject_reason&order=job_id.asc` |
| 467 | 0 | 10 | 9298 | `jobs?select=id,posted_at,first_seen_at&closed_at=is.null&order=id.asc` |
| 86 | 17 | 1 | 141 | `companies?select=id,name,ats,board_token,category,priority,active,last_ok_at,consecutive_failures&ats=neq.aggregator&ord` |
| 84 | 16 | 1 | 227 | `applications?select=applied_at&applied_at=not.is.null&order=id.asc` |

