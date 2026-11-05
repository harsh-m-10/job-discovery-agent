# Query plans: before

## queue page (dashboard): 5.024 ms  (seq scan: job_scores)

```
Limit  (cost=515.45..515.45 rows=1 width=49) (actual time=4.879..4.884 rows=23 loops=1)
  Buffers: shared hit=1762 dirtied=2
  ->  Sort  (cost=515.45..515.45 rows=1 width=49) (actual time=4.878..4.881 rows=23 loops=1)
        Sort Key: s.fit_score DESC, j.id
        Sort Method: quicksort  Memory: 27kB
        Buffers: shared hit=1762 dirtied=2
        ->  Nested Loop  (cost=0.71..515.44 rows=1 width=49) (actual time=0.621..4.820 rows=23 loops=1)
              Buffers: shared hit=1756 dirtied=2
              ->  Nested Loop Left Join  (cost=0.43..515.13 rows=1 width=57) (actual time=0.609..4.317 rows=23 loops=1)
                    Filter: (COALESCE(a.status, 'queued'::text) = 'queued'::text)
                    Rows Removed by Filter: 56
                    Buffers: shared hit=1686
                    ->  Nested Loop  (cost=0.29..511.53 rows=20 width=57) (actual time=0.399..4.184 rows=79 loops=1)
                          Buffers: shared hit=1551
                          ->  Seq Scan on job_scores s  (cost=0.00..359.84 rows=73 width=13) (actual time=0.021..2.260 rows=445 loops=1)
                                Filter: ((fit_score IS NOT NULL) AND location_ok AND (fit_score >= 6.5))
                                Rows Removed by Filter: 11076
                                Buffers: shared hit=216
                          ->  Index Scan using jobs_pkey on jobs j  (cost=0.29..2.08 rows=1 width=52) (actual time=0.004..0.004 rows=0 loops=445)
                                Index Cond: (id = s.job_id)
                                Filter: ((closed_at IS NULL) AND ((EXTRACT(epoch FROM (now() - COALESCE(posted_at, first_seen_at))) / '86400'::numeric) <= '5'::numeric))
                                Rows Removed by Filter: 1
                                Buffers: shared hit=1335
                    ->  Index Scan using applications_job_id_key on applications a  (cost=0.15..0.17 rows=1 width=16) (actual time=0.001..0.001 rows=1 loops=79)
                          Index Cond: (job_id = j.id)
                          Buffers: shared hit=135
              ->  Index Only Scan using companies_pkey on companies c  (cost=0.28..0.30 rows=1 width=8) (actual time=0.021..0.021 rows=1 loops=23)
                    Index Cond: (id = j.company_id)
                    Heap Fetches: 23
                    Buffers: shared hit=70 dirtied=2
Planning:
  Buffers: shared hit=475
Planning Time: 2.475 ms
Execution Time: 5.024 ms
```

## notify candidates: scores: 1.721 ms  (seq scan: job_scores)

```
Seq Scan on job_scores  (cost=0.00..359.84 rows=469 width=8) (actual time=0.018..1.683 rows=445 loops=1)
  Filter: (location_ok AND (fit_score >= 6.5))
  Rows Removed by Filter: 11076
  Buffers: shared hit=216
Planning Time: 0.058 ms
Execution Time: 1.721 ms
```

## notify candidates: unpinged open jobs: 6.305 ms  (seq scan: job_scores)

```
Index Scan using jobs_unpinged_idx on jobs  (cost=360.12..382.60 rows=8 width=8) (actual time=1.921..6.276 rows=1 loops=1)
  Index Cond: (id = ANY ((InitPlan 1).col1))
  Filter: (closed_at IS NULL)
  Rows Removed by Filter: 6
  Buffers: shared hit=328 read=20 dirtied=16
  InitPlan 1
    ->  Seq Scan on job_scores  (cost=0.00..359.84 rows=469 width=8) (actual time=0.014..1.745 rows=445 loops=1)
          Filter: (location_ok AND (fit_score >= 6.5))
          Rows Removed by Filter: 11076
          Buffers: shared hit=216
Planning:
  Buffers: shared hit=3
Planning Time: 0.149 ms
Execution Time: 6.305 ms
```

## ingest: job_keys for one board: 13.191 ms  (seq scan: jobs)

```
Append  (cost=1301.88..1325.04 rows=12 width=226) (actual time=6.266..13.073 rows=771 loops=1)
  Buffers: shared hit=1857
  InitPlan 1
    ->  Limit  (cost=1301.59..1301.60 rows=1 width=16) (actual time=5.079..5.081 rows=1 loops=1)
          Buffers: shared hit=1110
          ->  Sort  (cost=1301.59..1304.72 rows=1249 width=16) (actual time=5.078..5.079 rows=1 loops=1)
                Sort Key: (count(*)) DESC
                Sort Method: top-N heapsort  Memory: 25kB
                Buffers: shared hit=1110
                ->  HashAggregate  (cost=1282.86..1295.35 rows=1249 width=16) (actual time=4.768..4.929 rows=1249 loops=1)
                      Group Key: jobs.company_id
                      Batches: 1  Memory Usage: 193kB
                      Buffers: shared hit=1110
                      ->  Seq Scan on jobs  (cost=0.00..1225.24 rows=11524 width=8) (actual time=0.016..2.970 rows=11525 loops=1)
                            Buffers: shared hit=1110
  ->  Index Scan using jobs_company_id_ats_job_id_key on jobs j  (cost=0.29..11.39 rows=9 width=246) (actual time=6.265..12.989 rows=771 loops=1)
        Index Cond: (company_id = (InitPlan 1).col1)
        Buffers: shared hit=1855
  ->  Nested Loop Anti Join  (cost=1.56..11.99 rows=3 width=165) (actual time=0.018..0.018 rows=0 loops=1)
        Buffers: shared hit=2
        ->  Bitmap Heap Scan on seen_jobs s  (cost=1.27..4.45 rows=3 width=104) (actual time=0.017..0.017 rows=0 loops=1)
              Recheck Cond: (company_id = (InitPlan 1).col1)
              Buffers: shared hit=2
              ->  Bitmap Index Scan on seen_jobs_pkey  (cost=0.00..1.27 rows=3 width=0) (actual time=0.007..0.008 rows=0 loops=1)
                    Index Cond: (company_id = (InitPlan 1).col1)
                    Buffers: shared hit=2
        ->  Index Only Scan using jobs_company_id_ats_job_id_key on jobs j_1  (cost=0.29..2.50 rows=1 width=24) (never executed)
              Index Cond: ((company_id = (InitPlan 1).col1) AND (ats_job_id = s.ats_job_id))
              Heap Fetches: 0
Planning:
  Buffers: shared hit=106
Planning Time: 0.722 ms
Execution Time: 13.191 ms
```

## funnel: 30 days of LLM scores: 1.814 ms  (seq scan: job_scores)

```
Seq Scan on job_scores  (cost=0.00..417.37 rows=1491 width=109) (actual time=0.013..1.736 rows=1534 loops=1)
  Filter: ((fit_score IS NOT NULL) AND (scored_at >= (now() - '30 days'::interval)))
  Rows Removed by Filter: 9987
  Buffers: shared hit=216
Planning:
  Buffers: shared hit=22
Planning Time: 0.166 ms
Execution Time: 1.814 ms
```

## referrals for queue companies: 6.023 ms  (seq scan: companies)

```
Nested Loop  (cost=0.84..50.81 rows=35 width=8) (actual time=1.242..5.972 rows=10 loops=1)
  Buffers: shared hit=51
  ->  HashAggregate  (cost=0.56..0.75 rows=19 width=10) (actual time=0.032..0.039 rows=20 loops=1)
        Group Key: companies.company_key
        Batches: 1  Memory Usage: 24kB
        Buffers: shared hit=2
        ->  Limit  (cost=0.00..0.51 rows=20 width=10) (actual time=0.018..0.024 rows=20 loops=1)
              Buffers: shared hit=2
              ->  Seq Scan on companies  (cost=0.00..32.86 rows=1286 width=10) (actual time=0.017..0.020 rows=20 loops=1)
                    Filter: (company_key IS NOT NULL)
                    Buffers: shared hit=2
  ->  Index Scan using connections_company_norm_idx on connections  (cost=0.28..2.61 rows=2 width=21) (actual time=0.291..0.296 rows=0 loops=20)
        Index Cond: (company_norm = companies.company_key)
        Buffers: shared hit=49
Planning:
  Buffers: shared hit=61
Planning Time: 7.941 ms
Execution Time: 6.023 ms
```

## pipeline: applications by status: 0.181 ms  (seq scan: applications)

```
Limit  (cost=14.61..14.68 rows=25 width=16) (actual time=0.134..0.138 rows=25 loops=1)
  Buffers: shared hit=8
  ->  Sort  (cost=14.61..15.12 rows=202 width=16) (actual time=0.133..0.135 rows=25 loops=1)
        Sort Key: updated_at DESC
        Sort Method: top-N heapsort  Memory: 26kB
        Buffers: shared hit=8
        ->  Seq Scan on applications  (cost=0.00..8.91 rows=202 width=16) (actual time=0.012..0.068 rows=206 loops=1)
              Filter: (status = 'applied'::text)
              Rows Removed by Filter: 119
              Buffers: shared hit=5
Planning:
  Buffers: shared hit=11
Planning Time: 0.094 ms
Execution Time: 0.181 ms
```

## retention: stale unreferenced jobs: 10.290 ms  (seq scan: applications)

```
Hash Anti Join  (cost=12.33..1105.91 rows=1891 width=8) (actual time=1.397..10.137 rows=1928 loops=1)
  Hash Cond: (j.id = a.job_id)
  Buffers: shared hit=2033 dirtied=6
  ->  Index Scan using jobs_first_seen_idx on jobs j  (cost=0.29..1069.80 rows=1944 width=8) (actual time=1.287..9.573 rows=1977 loops=1)
        Index Cond: (first_seen_at < (now() - '30 days'::interval))
        Buffers: shared hit=2028 dirtied=6
  ->  Hash  (cost=8.13..8.13 rows=313 width=8) (actual time=0.097..0.098 rows=325 loops=1)
        Buckets: 1024  Batches: 1  Memory Usage: 21kB
        Buffers: shared hit=5
        ->  Seq Scan on applications a  (cost=0.00..8.13 rows=313 width=8) (actual time=0.011..0.049 rows=325 loops=1)
              Buffers: shared hit=5
Planning:
  Buffers: shared hit=11
Planning Time: 0.234 ms
Execution Time: 10.290 ms
```
