# Dashboard timing: after

Measured 2026-11-05 23:23 India Standard Time from the operator's machine (India), 3 loads per page.

| page | status | function region | TTFB median (s) | total median (s) | KB |
|---|---|---|---|---|---|
| queue | 200 | bom1 | 1.13 | 1.13 | 83 |
| funnel | 200 | bom1 | 1.02 | 1.02 | 60 |
| contacts | 200 | bom1 | 0.91 | 0.92 | 176 |
| companies | 200 | bom1 | 1.23 | 1.23 | 191 |
| review | 200 | bom1 | 1.05 | 1.06 | 274 |

## Per-query breakdown (from /api/perf, server side)

### queue: HTTP 500

### funnel: 181 ms total, 7 requests, 10 round trips

| ms | started at | pages | rows | request |
|---|---|---|---|---|
| 170 | 5 | 2 | 1538 | `job_scores?select=job_id,fit_score,gap_skills,scored_at&fit_score=not.is.null&scored_at=gte.2026-10-06T17:53:21.875Z&ord` |
| 97 | 3 | 1 | 325 | `v_funnel?select=*&order=application_id.asc` |
| 95 | 10 | 2 | 1118 | `connections?select=company_norm&order=id.asc` |
| 95 | 11 | 2 | 1287 | `companies?select=company_key&company_key=not.is.null&order=id.asc` |
| 76 | 8 | 1 | 0 | `referral_asks?select=outcome&order=id.asc` |
| 75 | 0 | 1 | 1 | `dashboard_snapshot?select=computed_at,data&id=eq.N` |
| 49 | 6 | 1 | 1 | `dashboard_snapshot?select=computed_at,data&id=eq.N` |

### contacts: 111 ms total, 3 requests, 4 round trips

| ms | started at | pages | rows | request |
|---|---|---|---|---|
| 95 | 1 | 2 | 1272 | `companies?select=name,company_key&active=is.true&company_key=not.is.null&order=id.asc` |
| 46 | 2 | 1 | 860 | `connections?select=id,full_name,title,company_norm,profile_url,warm,connected_on&company_norm=not.is.null&order=warm.des` |
| 39 | 0 | 1 | 1 | `dashboard_snapshot?select=computed_at,data&id=eq.N` |

### companies: 86 ms total, 2 requests, 2 round trips

| ms | started at | pages | rows | request |
|---|---|---|---|---|
| 77 | 0 | 1 | 1 | `dashboard_snapshot?select=computed_at,data&id=eq.N` |
| 71 | 15 | 1 | 141 | `companies?select=id,name,ats,board_token,category,priority,active,last_ok_at,consecutive_failures&ats=neq.aggregator&ord` |

