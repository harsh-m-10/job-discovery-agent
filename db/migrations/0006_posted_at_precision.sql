-- 0006_posted_at_precision.sql — how much of `posted_at` is real.
--
-- Phase 1.4 of docs/PLAN.md (gap G8). Workday's detail response gives
-- `startDate` as a bare date ("2026-10-15"), which lands in `posted_at` as
-- midnight UTC. Measured 2026-10-15: all 1,470 Workday rows with a posted_at
-- sit exactly on 00:00:00 UTC; 0 of the 2,917 Greenhouse, Lever and Ashby rows
-- do. So for a third of the open corpus (704 of 2,304) the time of day is not
-- data, it is a placeholder — and every age and latency computed from it
-- carries up to 24 hours of invented precision, ~12 on average.
--
-- That matters most for apply latency. The funnel buckets an application as
-- <6h / 6-48h / >48h after posting, from a number frozen at apply time; a
-- Workday application made 3 hours after posting can be recorded as 26.
--
--   'timestamp'  the ATS gave a real instant
--   'date'       the ATS gave only a calendar day; the time is midnight UTC
--   NULL         no posted_at at all (age is measured from first_seen_at)
--
-- Code and schema ship separately here, and unlike run_log there is no
-- PGRST204 fallback on the jobs upsert, so THIS MIGRATION MUST BE APPLIED
-- BEFORE the code that writes the column is deployed.

alter table jobs
  add column if not exists posted_at_precision text
    check (posted_at_precision in ('timestamp', 'date'));

comment on column jobs.posted_at_precision is
  'timestamp | date | NULL. ''date'' means posted_at is a calendar day stored '
  'as midnight UTC (Workday startDate) and its time of day is not data. NULL '
  'when posted_at is NULL. See db/migrations/0006.';

-- Backfill. The split above is exact on the live data, so the rule is not a
-- heuristic: Workday rows at midnight UTC are dates, everything else with a
-- posted_at is a timestamp.
update jobs j
   set posted_at_precision = 'date'
  from companies c
 where c.id = j.company_id
   and c.ats = 'workday'
   and j.posted_at is not null
   and j.posted_at_precision is null
   and (j.posted_at at time zone 'UTC')::time = time '00:00:00';

update jobs
   set posted_at_precision = 'timestamp'
 where posted_at is not null
   and posted_at_precision is null;
