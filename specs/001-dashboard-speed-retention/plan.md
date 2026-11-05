# Implementation Plan: Dashboard speed and data retention

**Branch**: `001-dashboard-speed-retention` (work lands on `main`) | **Date**: 2026-11-05 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-dashboard-speed-retention/spec.md`

## Summary

The dashboard spends its ~20 s on about 50 sequential PostgREST round trips from
Vercel's US region to Supabase in Mumbai. Most of them come from computing headline
counts by downloading every open job and score, twice per page view (research R1).

The fix, in order:
1. Measure.
2. Pin functions to bom1.
3. Replace live aggregates with one precomputed snapshot row written at the end of each
   ingest run.
4. Make lists lean, paginated and parallel.
5. Add only the indexes that query plans show are needed.
6. Replace the 15-day retention with the operator's windows. A `seen_jobs` record and
   `jobs.pinged_at` keep the never-ping-twice guarantee after rows are deleted.

## Technical Context

- **Language/version**: TypeScript 5.7 on Next.js 16 / React 19 (dashboard); Python 3.11
  (pipeline); PostgreSQL 15 (Supabase).
- **Primary dependencies**: PostgREST (Supabase REST); no new npm or pip packages.
- **Storage**: Supabase Postgres, ap-south-1. 86 MB on 2026-11-05; jobs 57 MB / 11.5k rows.
- **Testing**:
  - plain Python test scripts in CI;
  - `next build` for the dashboard;
  - a throwaway-Postgres test for the seen/retention SQL, following the backup round-trip
    pattern.
- **Target platform**: Vercel Hobby (functions, bom1), GitHub Actions.
- **Project type**: web dashboard plus batch pipeline.
- **Performance goals**:
  - first load < 1.5 s on 4G;
  - navigation < 300 ms;
  - each page ≤ 4 DB requests, all concurrent;
  - the snapshot read < 50 ms.
- **Constraints**:
  - free tiers;
  - egress well under 160 MB/day;
  - no `count=exact` on large tables;
  - client JS < 200 KB.
- **Scale**: one operator; about 1,000 new postings a day; 10–15k stored postings.

## Constitution Check

| Principle | Check | Result |
|---|---|---|
| I Operator decides | No automatic sending is added | Pass |
| II Sources by consent | No new sources | Pass |
| III Free tier | bom1 is allowed on Hobby; the snapshot cuts egress; retention cuts storage | Pass |
| IV Nothing fails silently | The snapshot carries health and its own age; retention reports counts; dry-run mode | Pass |
| V Measure before changing | T001 baseline and T0xx after-report, query plans before and after | Pass |
| VI Public repo | perf reports omit the secret path; no data in the repo | Pass |
| VII Answers, not internals | No new UI text beyond pagination (002 does the UI) | Pass |

Post-design re-check: same result; no violations, so no complexity tracking is needed.

## Project Structure

### Documentation (this feature)

```text
specs/001-dashboard-speed-retention/
├── plan.md  research.md  data-model.md  quickstart.md
├── contracts/snapshot.schema.md
├── perf-baseline.md   perf-after.md        # measured, T001 / final
├── explain-before.md  explain-after.md     # query plans
├── retention-2026-11-xx.md                 # before/after sizes
└── tasks.md
```

### Source code

```text
dashboard/
├── vercel.json                      # NEW regions: ["bom1"]
├── lib/db.ts                        # timing collector; select(limit/offset) single-page variant
├── lib/perf.ts                      # NEW AsyncLocalStorage collector + Server-Timing format
├── lib/queries.ts                   # snapshot reads; lean selects; pagination; parallel
├── app/api/perf/route.ts            # NEW Server-Timing endpoint
├── app/api/job-description/route.ts # NEW description on expand
└── app/d/[secret]/{layout,page,funnel,contacts}.tsx   # read snapshot, paginate
db/migrations/0026_snapshot_seen_retention.sql         # NEW tables, view, trigger, functions
ingest/store.py, ingest/aggregate.py                   # read job_keys; skip seen_only
notify/run.py                                          # pinged_at dedupe
scripts/prune.py                                       # v2 + report + dry-run
scripts/perf_report.py                                 # NEW baseline/after measurement
.github/workflows/ingest.yml                           # final step: refresh snapshot
tests/test_seen_jobs.py, tests/test_snapshot_contract.py  # NEW
```

**Structure decision**: The existing layout stays. Database logic goes in SQL
functions, so the pipeline and the dashboard stay thin callers.

## Rollout order (each step deployable alone)

1. Timing and baseline (no behaviour change).
2. Region pin, then re-measure.
3. Migration 0026, applied live:
   - pinged_at backfilled;
   - seen_jobs/job_keys/trigger created but unused;
   - snapshot function created.
4. Ingest and aggregate read job_keys; notify uses pinged_at; the ingest final step
   refreshes the snapshot.
5. Dashboard reads the snapshot; lean, paginated, parallel queries; lazy descriptions.
6. Indexes, driven by query plans.
7. Retention v2: dry-run report, then the first real run (after that night's backup),
   then switch backup.yml over.
8. After-report against budgets.

## Complexity Tracking

None.
