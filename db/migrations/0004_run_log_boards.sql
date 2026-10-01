-- 0004_run_log_boards.sql — per-board outcome for every ingestion run.
--
-- `run_log.errors` records only raised exceptions. The failure that actually
-- costs data raises nothing: a board that answers 200 with an empty list, or
-- whose postings are all removed by the India filter, reaches lifecycle.plan()
-- as a legitimately empty fetch and closes every open role for that company.
-- The next good run reopens them all and increments reopen_count.
--
-- Measured 2026-10-01 before the guard in lifecycle.plan() landed: 413 of
-- 3,564 jobs carried reopen_count > 0 after seven weeks of history, and 574 of
-- them sat in 125 groups that shared one company and one close minute — the
-- signature of a whole-board close rather than genuine withdrawals.
--
-- Nothing in the schema could distinguish "this board had nothing today" from
-- "this board failed in a way that returns 200", so neither the watchdog nor
-- the dashboard could see it. This column stores one entry per board polled:
--
--   {"company": "Cisco", "ats": "workday", "token": "cisco|wd5|cisco_Careers",
--    "seen": 284, "new": 3, "changed": 1, "reopened": 0, "closed": 2,
--    "suppressed_closes": 0}
--
-- or, for a board that failed outright:
--
--   {"company": "Postman", "ats": "greenhouse", "token": "postman",
--    "error": "board not found (404) — token may have changed"}
--
-- Nullable with no default: runs written before this column existed keep NULL,
-- which readers must treat as "not recorded", never as "no boards polled".

alter table run_log
  add column if not exists boards jsonb;

comment on column run_log.boards is
  'Per-board outcome for this run: seen/new/changed/reopened/closed/'
  'suppressed_closes, or {error} for a board that could not be fetched. '
  'NULL on runs predating 2026-10-01. See db/migrations/0004.';
