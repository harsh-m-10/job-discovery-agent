# Query plans: after

## queue page (dashboard): 2.022 ms

```
Limit  (cost=224.55..224.56 rows=1 width=49) (actual time=1.959..1.964 rows=23 loops=1)
  Buffers: shared hit=1711 read=3
  ->  Sort  (cost=224.55..224.56 rows=1 width=49) (actual time=1.957..1.960 rows=23 loops=1)
        Sort Key: s.fit_score DESC, j.id
        Sort Method: quicksort  Memory: 27kB
        Buffers: shared hit=1711 read=3
        ->  Nested Loop  (cost=2.84..224.54 rows=1 width=49) (actual time=0.396..1.937 rows=23 loops=1)
              Buffers: shared hit=1711 read=3
              ->  Nested Loop Left Join  (cost=2.57..224.24 rows=1 width=57) (actual time=0.391..1.881 rows=23 loops=1)
                    Filter: (COALESCE(a.status, 'queued'::text) = 'queued'::text)
                    Rows Removed by Filter: 56
                    Buffers: shared hit=1641 read=3
                    ->  Nested Loop  (cost=2.42..220.64 rows=20 width=57) (actual time=0.313..1.783 rows=79 loops=1)
                          Buffers: shared hit=1506 read=3
                          ->  Bitmap Heap Scan on job_scores s  (cost=2.13..68.94 rows=73 width=13) (actual time=0.108..0.380 rows=445 loops=1)
                                Recheck Cond: ((fit_score IS NOT NULL) AND (fit_score >= 6.5) AND location_ok)
                                Heap Blocks: exact=171
                                Buffers: shared hit=171 read=3
                                ->  Bitmap Index Scan on job_scores_bengaluru_fit_idx  (cost=0.00..2.12 rows=73 width=0) (actual time=0.087..0.087 rows=445 loops=1)
                                      Index Cond: ((fit_score IS NOT NULL) AND (fit_score >= 6.5))
                                      Buffers: shared read=3
                          ->  Index Scan using jobs_pkey on jobs j  (cost=0.29..2.08 rows=1 width=52) (actual time=0.003..0.003 rows=0 loops=445)
                                Index Cond: (id = s.job_id)
                                Filter: ((closed_at IS NULL) AND ((EXTRACT(epoch FROM (now() - COALESCE(posted_at, first_seen_at))) / '86400'::numeric) <= '5'::numeric))
                                Rows Removed by Filter: 1
                                Buffers: shared hit=1335
                    ->  Index Scan using applications_job_id_key on applications a  (cost=0.15..0.17 rows=1 width=16) (actual time=0.001..0.001 rows=1 loops=79)
                          Index Cond: (job_id = j.id)
                          Buffers: shared hit=135
              ->  Index Only Scan using companies_pkey on companies c  (cost=0.28..0.30 rows=1 width=8) (actual time=0.002..0.002 rows=1 loops=23)
                    Index Cond: (id = j.company_id)
                    Heap Fetches: 23
                    Buffers: shared hit=70
Planning:
  Buffers: shared hit=91 read=2
Planning Time: 1.083 ms
Execution Time: 2.022 ms
```

## notify candidates: scores: 0.414 ms

```
Bitmap Heap Scan on job_scores  (cost=6.12..227.98 rows=469 width=8) (actual time=0.071..0.362 rows=445 loops=1)
  Recheck Cond: ((fit_score >= 6.5) AND location_ok)
  Heap Blocks: exact=171
  Buffers: shared hit=174
  ->  Bitmap Index Scan on job_scores_bengaluru_fit_idx  (cost=0.00..6.00 rows=469 width=0) (actual time=0.049..0.049 rows=445 loops=1)
        Index Cond: (fit_score >= 6.5)
        Buffers: shared hit=3
Planning Time: 0.087 ms
Execution Time: 0.414 ms
```

## notify candidates: unpinged open jobs: 0.760 ms

```
Index Scan using jobs_unpinged_idx on jobs  (cost=228.27..250.75 rows=8 width=8) (actual time=0.467..0.727 rows=1 loops=1)
  Index Cond: (id = ANY ((InitPlan 1).col1))
  Filter: (closed_at IS NULL)
  Rows Removed by Filter: 6
  Buffers: shared hit=239
  InitPlan 1
    ->  Bitmap Heap Scan on job_scores  (cost=6.12..227.98 rows=469 width=8) (actual time=0.109..0.351 rows=445 loops=1)
          Recheck Cond: ((fit_score >= 6.5) AND location_ok)
          Heap Blocks: exact=171
          Buffers: shared hit=174
          ->  Bitmap Index Scan on job_scores_bengaluru_fit_idx  (cost=0.00..6.00 rows=469 width=0) (actual time=0.083..0.084 rows=445 loops=1)
                Index Cond: (fit_score >= 6.5)
                Buffers: shared hit=3
Planning Time: 0.136 ms
Execution Time: 0.760 ms
```

## ingest: job_keys for one board: 6.240 ms  (seq scan: jobs)

```
Append  (cost=1301.88..1325.04 rows=12 width=226) (actual time=5.298..6.127 rows=771 loops=1)
  Buffers: shared hit=1857
  InitPlan 1
    ->  Limit  (cost=1301.59..1301.60 rows=1 width=16) (actual time=5.278..5.279 rows=1 loops=1)
          Buffers: shared hit=1110
          ->  Sort  (cost=1301.59..1304.72 rows=1249 width=16) (actual time=5.276..5.277 rows=1 loops=1)
                Sort Key: (count(*)) DESC
                Sort Method: top-N heapsort  Memory: 25kB
                Buffers: shared hit=1110
                ->  HashAggregate  (cost=1282.86..1295.35 rows=1249 width=16) (actual time=4.964..5.122 rows=1249 loops=1)
                      Group Key: jobs.company_id
                      Batches: 1  Memory Usage: 193kB
                      Buffers: shared hit=1110
                      ->  Seq Scan on jobs  (cost=0.00..1225.24 rows=11524 width=8) (actual time=0.011..3.172 rows=11525 loops=1)
                            Buffers: shared hit=1110
  ->  Index Scan using jobs_company_id_ats_job_id_key on jobs j  (cost=0.29..11.39 rows=9 width=246) (actual time=5.298..6.046 rows=771 loops=1)
        Index Cond: (company_id = (InitPlan 1).col1)
        Buffers: shared hit=1855
  ->  Nested Loop Anti Join  (cost=1.56..11.99 rows=3 width=165) (actual time=0.012..0.013 rows=0 loops=1)
        Buffers: shared hit=2
        ->  Bitmap Heap Scan on seen_jobs s  (cost=1.27..4.45 rows=3 width=104) (actual time=0.011..0.011 rows=0 loops=1)
              Recheck Cond: (company_id = (InitPlan 1).col1)
              Buffers: shared hit=2
              ->  Bitmap Index Scan on seen_jobs_pkey  (cost=0.00..1.27 rows=3 width=0) (actual time=0.005..0.005 rows=0 loops=1)
                    Index Cond: (company_id = (InitPlan 1).col1)
                    Buffers: shared hit=2
        ->  Index Only Scan using jobs_company_id_ats_job_id_key on jobs j_1  (cost=0.29..2.50 rows=1 width=24) (never executed)
              Index Cond: ((company_id = (InitPlan 1).col1) AND (ats_job_id = s.ats_job_id))
              Heap Fetches: 0
Planning:
  Buffers: shared hit=2
Planning Time: 0.337 ms
Execution Time: 6.240 ms
```

## funnel: 30 days of LLM scores: 1.861 ms  (seq scan: job_scores)

```
Seq Scan on job_scores  (cost=0.00..417.63 rows=1492 width=109) (actual time=0.014..1.784 rows=1535 loops=1)
  Filter: ((fit_score IS NOT NULL) AND (scored_at >= (now() - '30 days'::interval)))
  Rows Removed by Filter: 9987
  Buffers: shared hit=216
Planning:
  Buffers: shared hit=15
Planning Time: 0.142 ms
Execution Time: 1.861 ms
```

## referrals for queue companies: 0.132 ms  (seq scan: companies)

```
Nested Loop  (cost=0.84..50.81 rows=35 width=8) (actual time=0.040..0.097 rows=10 loops=1)
  Buffers: shared hit=51
  ->  HashAggregate  (cost=0.56..0.75 rows=19 width=10) (actual time=0.028..0.031 rows=20 loops=1)
        Group Key: companies.company_key
        Batches: 1  Memory Usage: 24kB
        Buffers: shared hit=2
        ->  Limit  (cost=0.00..0.51 rows=20 width=10) (actual time=0.014..0.020 rows=20 loops=1)
              Buffers: shared hit=2
              ->  Seq Scan on companies  (cost=0.00..32.86 rows=1286 width=10) (actual time=0.013..0.016 rows=20 loops=1)
                    Filter: (company_key IS NOT NULL)
                    Buffers: shared hit=2
  ->  Index Scan using connections_company_norm_idx on connections  (cost=0.28..2.61 rows=2 width=21) (actual time=0.002..0.003 rows=0 loops=20)
        Index Cond: (company_norm = companies.company_key)
        Buffers: shared hit=49
Planning:
  Buffers: shared hit=6
Planning Time: 1.660 ms
Execution Time: 0.132 ms
```

## pipeline: applications by status: 0.089 ms

```
Limit  (cost=0.27..1.75 rows=25 width=16) (actual time=0.059..0.067 rows=25 loops=1)
  Buffers: shared hit=2 read=2
  ->  Index Scan using applications_status_updated_idx on applications  (cost=0.27..12.45 rows=206 width=16) (actual time=0.058..0.064 rows=25 loops=1)
        Index Cond: (status = 'applied'::text)
        Buffers: shared hit=2 read=2
Planning:
  Buffers: shared hit=6
Planning Time: 0.151 ms
Execution Time: 0.089 ms
```

## retention: stale unreferenced jobs: 1.772 ms  (seq scan: applications)

```
Hash Anti Join  (cost=12.60..1106.16 rows=1889 width=8) (actual time=0.113..1.672 rows=1928 loops=1)
  Hash Cond: (j.id = a.job_id)
  Buffers: shared hit=1974
  ->  Index Scan using jobs_first_seen_idx on jobs j  (cost=0.29..1069.80 rows=1944 width=8) (actual time=0.017..1.319 rows=1977 loops=1)
        Index Cond: (first_seen_at < (now() - '30 days'::interval))
        Buffers: shared hit=1969
  ->  Hash  (cost=8.25..8.25 rows=325 width=8) (actual time=0.091..0.092 rows=325 loops=1)
        Buckets: 1024  Batches: 1  Memory Usage: 21kB
        Buffers: shared hit=5
        ->  Seq Scan on applications a  (cost=0.00..8.25 rows=325 width=8) (actual time=0.007..0.048 rows=325 loops=1)
              Buffers: shared hit=5
Planning:
  Buffers: shared hit=12
Planning Time: 0.225 ms
Execution Time: 1.772 ms
```
