---
description: "Task list for feature 001: dashboard speed and data retention"
---

# Tasks: Dashboard speed and data retention

**Input**: Design documents from `/specs/001-dashboard-speed-retention/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: The constitution requires a test for every data-safety change. Tests are
included for the seen-record, retention and snapshot contract.

**Organization**: US2 (measure) runs first because the constitution requires a baseline
before any change. US1 (speed) is next and US3 (retention) last.

## Phase 1: Setup

- [X] T001 Add the timing collector `dashboard/lib/perf.ts` (AsyncLocalStorage; records name, table, pages, rows, ms; formats a `Server-Timing` header; `logPage(name)` prints `[timing] page=… total=…ms` plus each query)
- [X] T002 Wire `select`/`patch`/`upsert` in `dashboard/lib/db.ts` to record into the collector (per page fetched as well as per call)

## Phase 2: User Story 2 – Know where the time goes (P1) 🎯 first

**Goal**: Baseline per-query and per-page timings before any optimisation.
**Independent test**: `/api/perf?page=queue` returns a `Server-Timing` header; the report file exists.

- [X] T003 [US2] Add `dashboard/app/api/perf/route.ts` running each page's loaders (queue = layout meta + getQueue + getQueueMeta + getStatus; funnel; contacts; review; companies; job) inside the collector, returning `Server-Timing` and JSON per contracts/snapshot.schema.md
- [X] T004 [US2] Call `logPage()` at the end of each server page in `dashboard/app/d/[secret]/{layout,page}.tsx`, `funnel/page.tsx`, `contacts/page.tsx`, `review/page.tsx`, `companies/page.tsx`, `j/[id]/page.tsx`
- [X] T005 [P] [US2] Add `scripts/perf_report.py` (reads DASHBOARD_SECRET from .env; for each page: 3× curl-style TTFB/total/size, the `x-vercel-id` region, and the `/api/perf` breakdown; writes markdown without the secret path)
- [X] T006 [US2] Deploy (push to main) and record the baseline into `specs/001-dashboard-speed-retention/perf-baseline.md`, including the function region and the slowest 10 queries

**Checkpoint**: Baseline recorded; stop and report the numbers before changing behaviour.

## Phase 3: Foundational (blocks US1 and US3)

- [X] T007 Write `db/migrations/0026_snapshot_seen_retention.sql` per data-model.md:
  - `dashboard_snapshot` (id smallint PK `check (id = 1)`), `seen_jobs` (PK `(company_id, ats_job_id)`, index on `removed_at`), `daily_stats` (PK `(day, worker)`);
  - `jobs.pinged_at` backfilled from `min(notifications.sent_at) where ok`;
  - view `job_keys`; trigger `jobs_skip_seen` (BEFORE INSERT, return NULL when the key is in seen_jobs);
  - `v_funnel.first_ping_at` from `jobs.pinged_at`;
  - grants to service_role only.
- [X] T008 Add `refresh_dashboard_snapshot()` to the same migration, producing the `data` jsonb exactly per contracts/snapshot.schema.md, with health rules red/amber/green as specified there
- [X] T009 [P] Add `tests/test_seen_jobs.py`:
  - on a throwaway Postgres (CI service DB, as in `tests/fixtures/check_backup_roundtrip.py`), apply 0026 to a minimal schema;
  - assert the trigger drops a seen key;
  - assert job_keys flags `seen_only`;
  - assert a `pinged_at` row is never selected by notify's query.
- [X] T010 [P] Add `tests/test_snapshot_contract.py`: run `refresh_dashboard_snapshot()` on the throwaway DB and assert every key in contracts/snapshot.schema.md is present with the right type
- [X] T011 Wire T009/T010 into `.github/workflows/ci.yml` next to the backup round-trip step
- [X] T012 Apply 0026 to the live database (after that night's backup exists); verify `pinged_at` count equals the distinct notified job count (301+)

## Phase 4: User Story 1 – Open from a ping and see it at once (P1)

**Goal**: First load < 1.5 s on 4G; tab switch < 300 ms; flat as the data grows.
**Independent test**: perf_report after vs baseline; a throttled phone load.

- [X] T013 [US1] Add `dashboard/vercel.json` `{"regions": ["bom1"]}`; deploy; re-run perf_report into `perf-after-region.md` (the before/after per-query latency asked in C2)
- [X] T014 [US1] `notify/run.py`: dedupe on `jobs.pinged_at is null`; set `pinged_at` for each job after a successful send; stop reading all notifications
- [X] T015 [P] [US1] `ingest/store.py:existing_jobs` and `ingest/aggregate.py:existing_for` read `job_keys`; `ingest/run.py` skips `seen_only` keys (no insert, no close, not counted as new)
- [X] T016 [US1] `.github/workflows/ingest.yml`: final step (`if: always()`) calls `rpc/refresh_dashboard_snapshot`
- [X] T017 [US1] `dashboard/lib/queries.ts`: add `getSnapshot()`; layout and pages read headline counts, status, provider mix and inactive boards from it; delete `getQueueMeta`'s full-table reads; no `count=exact` anywhere
- [X] T018 [US1] `dashboard/lib/queries.ts`: `getQueue(page)` selects only the card fields from `v_queue` (no description), `limit=25&offset=…`, ordered by fit then recency; `getFunnel`/`getContacts` page at 25 and run their independent selects with `Promise.all`
- [X] T019 [P] [US1] Descriptions stay out of every list: the queue selects card fields only and the job page loads one description (no separate endpoint needed)
- [X] T020 [US1] Pagination control (Prev/Next, `?p=`) on the queue, funnel and contacts pages in `dashboard/app/d/[secret]/`
- [X] T021 [US1] Query plans: run EXPLAIN ANALYZE for each hot query (queue, notify candidates, existing_jobs, job_keys, retention selects) into `explain-before.md`; add only the indexes showing seq scans on > 1k rows in migration `0027_indexes.sql`; re-run into `explain-after.md`
- [X] T022 [US1] `npm run build`: confirm First Load JS < 200 KB on every route; record the figures in `perf-after.md`

## Phase 5: User Story 3 – Storage that doesn't pile up, no repeat pings (P2)

**Goal**: Operator's retention windows; size and row counts before and after; zero repeat pings.
**Independent test**: dry run, real run, replay test (quickstart 3–5).

- [X] T023 [US3] Add `prune_old_data_v2(p_dry_run boolean, …windows)` to `db/migrations/0028_retention_v2.sql` per the data-model.md retention table:
  - deleted jobs inserted into seen_jobs in the same transaction;
  - daily_stats upserted before run_log deletion;
  - returns jsonb counts;
  - never touches applications, application_events, referral_asks or connections.
- [X] T024 [P] [US3] Extend `tests/test_seen_jobs.py` with retention cases:
  - applied job kept;
  - rejected job deleted at day 31 and present in seen_jobs;
  - pinged job deleted at day 91 keeps `pinged_at` in seen_jobs;
  - dry run changes nothing.
- [X] T025 [US3] `scripts/prune.py`:
  - call v2;
  - print database size and per-table row counts before and after;
  - `--dry-run` calls v2 with `p_dry_run`;
  - append a report to `specs/001-dashboard-speed-retention/retention-<date>.md` when `--report` is set.
- [X] T026 [US3] `config/settings.yaml`: replace `retention_days: 15` with a `retention:` block (descriptions 30, rejected 30, stale 90, notifications 30, run_log 90, seen 180, trigger_log 30, alert_inbox 30)
- [X] T027 [US3] Apply 0028 live, run `prune.py --dry-run` (retention-2026-11-05.md); the first real run is tonight's backup.yml, after the backup
- [X] T028 [US3] `.github/workflows/backup.yml` keeps retention after a successful backup (already gated), now calling v2 and then refreshing the snapshot

## Phase 6: Polish

- [X] T029 Re-run perf_report into `perf-after.md` and compare with the baseline in a table (page × TTFB/total/queries)
- [ ] T030 [P] Update the README dashboard section (public-safe), plus the local `docs/RUNBOOK.md` and `docs/DECISIONS.md` (gitignored)
- [ ] T031 Commit in small steps; final commit when quickstart 1–6 pass

## Dependencies

- T001–T002 → T003–T006 (US2) → STOP: report the baseline.
- T007–T012 block T014–T018 and T023–T028.
- US1: T013 is independent (region); T014–T016 pipeline; T017–T020 dashboard; T021 after T017–T018; T022 last.
- US3 needs T007/T012 (seen_jobs, pinged_at) because deleting without them breaks never-ping-twice.

## Parallel examples

- After T012: T014 (notify), T015 (ingest) and T017 (dashboard) touch different files.
- T009, T010 and T005 can be written together.

## Implementation strategy

MVP = US2 baseline + T013 region pin + T017 snapshot. These are expected to remove most
of the 20 s. Then the rest of US1, then US3. Feature 002 starts only after T029 and the
mockup approval.
