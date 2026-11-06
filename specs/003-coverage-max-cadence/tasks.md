# Tasks: Wider board coverage and the fastest affordable polling

## Phase 0: Egress incident (2026-11-06), blocks everything below

Supabase emailed at 23:08 UTC on 2026-11-05: the organisation exceeded its egress quota.
It is in a one-time grace period, and fair-use restrictions apply from 2026-12-06 if
egress stays above 5.5 GB.

Measured per read, as JSON in the database (no egress spent measuring):

| Read | Per call | Calls/day | Month |
|---|---|---|---|
| score.run `scores()`: all 11,500 scores with reasoning, to find failed ones | 3.2 MB | 96 | **8.7 GB** |
| ingest: stored rows for every board diff | 615 KB | 96 | 1.7 GB |
| nightly backup (pg_dump of every table) | 45 MB | 1 | 1.3 GB |
| notify: candidate scores + every contact | 418 KB | 96 | 1.2 GB |
| score: open ids + scored ids; location sync | 341 KB | 96 | 1.0 GB |
| aggregate: stored descriptions of the batch's companies | up to MBs | 24 | ? |

The 8.7 GB read predates this feature (the 2026-10-15 recovery step). This session added
about 4.5 h of 5-minute cron-job.org firing before the email, which was reverted to
15 minutes on 2026-11-06.

- [X] T100 Revert cron-job.org to 15 minutes; hold the cadence settings and the Worker
  crons (not deployed)
- [X] T101 `score/store.py failed_job_ids()`; `open_jobs()` as a PostgREST anti-join
- [X] T102 `score/run.py`: the location sync once a day (03:00 UTC run) or `--sync-location`
- [X] T103 `notify/run.py`: candidates filtered in the database (unpinged, open, inside the
  age ceiling) with the job fields embedded
- [X] T104 `ingest/aggregate.py` + `db/migrations/0031`: compare by `desc_len`; fetch text by
  id only when needed
- [X] T105 Board fingerprint (0030): unchanged boards skip the diff read
- [ ] T106 After 24 h: sum `run_log.db_read_kb` and size the other steps; the projected
  month must be under 3 GB before T004 is re-raised
- [ ] T107 Backup: decide with the operator whether nightly full dumps (1.3 GB a month)
  stay, or go every other night once retention has shrunk the tables

## Phase 1: US1 cadence (P1), held until T106 passes

- [X] T001 [US1] Measure egress per run and per priority (plan.md Research)
- [X] T002 [US1] `db/migrations/0030_board_fingerprint.sql`, applied
- [X] T003 [US1] Fingerprint shortcut, touch_company_jobs, db_read_kb; tested
- [ ] T004 [US1] Cadence 0 / 13 / 115 and lease 4 (reverted; re-apply after T106)
- [ ] T005 [US1] cron-job.org every 5 minutes (reverted to 15; re-apply after T106)
- [ ] T006 [US1] Worker crons every 5 minutes, Indeed :11/:41 (written, reverted, not deployed)
- [ ] T007 [US1] Apps Script standby 25 → 12 minutes

## Phase 2: US2 coverage (P1)

- [ ] T008 [US2] `seeds/companies_batch8.yaml`: verify GSK, LSEG, Intel, eBay, Illumina,
  ABB, JLL, WEX; seed those with India postings
- [ ] T010 [US2] JPMorganChase (Oracle HCM) and SAP (SuccessFactors) through the existing adapters
- [ ] T011 [US2] `common/company_key.py`: "Name | Abbrev" → Name

## Phase 3: Verify

- [ ] T009 After 24 h at any new cadence: runs per hour, db_read_kb, posted → pinged
  latency, Indeed 429s → `after.md`
