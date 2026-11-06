# Implementation Plan: Wider board coverage and the fastest affordable polling

**Branch**: `003-coverage-max-cadence` (work lands on `main`) | **Date**: 2026-11-06 | **Spec**: [spec.md](spec.md)

## Summary

> **2026-11-06: held.** Supabase reported the egress quota exceeded. The cadence
> changes are reverted until a day of measured reads (tasks.md Phase 0) projects under
> 3 GB a month. The fingerprint and the egress fixes ship first.

Measured first (research below). Database egress, not GitHub or the boards, bounds the
cadence: diff reads cost about 615 KB per run. Going to 5 minutes as things stood would
have meant 5.9 GB a month.

A board fingerprint removes the read for unchanged boards. Measured on three boards, the
read fell from 64 KB to 25 KB, and what remained was the fixed per-run company list.

That makes 5-minute ingest, 15-minute and 2-hour tiers, and Indeed twice an hour
affordable.

Coverage comes from the Indeed data itself: the companies with Bengaluru matches and no
board. Those with a public Workday, Oracle or SuccessFactors board are added through the
existing adapters and verified first.

## Research

| Finding | Source |
|---|---|
| 37 / 53 / 36 boards at priority 1 / 2 / 3; a run polls about 51 boards in a median 83 s (p95 153 s) | run_log, 2 days |
| Diff reads per board: p1 393 KB, p2 870 KB, p3 427 KB in total; about 615 KB per 15-minute run, 1.7 GB a month | job_keys reads, 2026-11-05 |
| 5 minutes without a fingerprint: 202 MB a day, 5.9 GB a month, over the free tier | same |
| Fingerprint on Stripe, Cloudflare and Databricks: the second run read 25 KB instead of 64 KB | live manual run |
| Workday tenants found: gsk wd5 gskCareers (970), lseg wd3 Careers (701), intel wd1 External (853), ebay wd5 apply (268), illumina wd1 illumina-careers (173), abb wd3 external_career_page (2000), jll wd1 jllCareers (2000), wexinc wd5 Wexinc (95) | scripts/find_workday.py plus probes |
| No Workday tenant: natwest, honeywell, teradyne, sage, signify, deltek, unitedhealthgroup | same |

## Constitution Check

| Principle | Result |
|---|---|
| II Sources by consent | Only public ATS endpoints; Indeed unchanged; no LinkedIn or Naukri |
| III Free tier | Egress projected under 3 GB a month; Cloudflare stays at 2 crons; Actions minutes are free (public repo) |
| IV Nothing fails silently | db_read_kb per run; new boards verified; the empty-fetch guard is untouched |
| V Measure | Baseline above; after-measure in tasks T009 |

## Changes

- `db/migrations/0030_board_fingerprint.sql`: `companies.board_hash`, `run_log.db_read_kb`
- `ingest/run.py`: `board_fingerprint`; the unchanged-board shortcut; the read report
- `ingest/store.py`: `touch_company_jobs`; `mark_company_ok(board_hash)`; byte counting
- `config/settings.yaml`: `poll_interval_minutes` 0 / 13 / 115; `run_lease_minutes` 4
- `infra/cf-standby`: ingest every 5 minutes at :02/:07…; Indeed at :11 and :41
- cron-job.org job 8656265: every 5 minutes (done through its API)
- `apps_script/pipeline.gs`: standby threshold 25 → 12 minutes
- `seeds/companies_batch8.yaml`: verified Workday, Oracle and SuccessFactors boards
- `common/company_key.py`: "Name | Abbrev" → Name
