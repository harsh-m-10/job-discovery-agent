-- 0030_board_fingerprint.sql -- specs/003-coverage-max-cadence.
--
-- Polling cadence is bounded by database egress, not by GitHub or the boards:
-- measured 2026-11-05, every run read ~615 KB of stored rows to diff boards
-- (1.7 GB a month at 15 minutes; 5.9 GB at 5 minutes, over the 5 GB free tier).
-- Between two polls minutes apart nearly every board is unchanged, so ingest
-- now stores a fingerprint of each board's postings after a successful diff and
-- skips the read when the next fetch has the same fingerprint.
--
--   companies.board_hash   sha1 of sorted ats_job_id:content_hash, set only
--                          after a diff applied in full (ingest/run.py)
--   run_log.db_read_kb     what the run read from the database
-- Idempotent.

alter table companies add column if not exists board_hash text;
alter table run_log add column if not exists db_read_kb integer;
