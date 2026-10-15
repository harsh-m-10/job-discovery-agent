-- 0005_run_log_telemetry.sql — how a run was triggered, and how long it took.
--
-- Phase 0.2 of docs/PLAN.md. Two facts the run log could not answer:
--
--   * Which trigger fired the run. From Phase 2 the pipeline is driven by three
--     redundant triggers (a cron-job.org dispatch, a Cloudflare standby
--     dispatcher, and GitHub's own best-effort `schedule`). Without recording
--     the source there is no way to check that the primary trigger is the one
--     doing the work — which is exactly the acceptance test Phase 2 sets
--     itself ("trigger_source shows >= 90% cronjob").
--   * How long the run took, so per-board timeouts have something to be tuned
--     against. `finished_at - started_at` is a usable proxy but it also
--     contains the final PATCH round-trip; this is the wall clock measured
--     inside the process, which is what the timeout actually governs.
--
-- Both are nullable. Runs predating this migration have neither, and a reader
-- must treat NULL as "not recorded", never as a value — the same rule 0004 set
-- for `boards`.
--
-- Deliberately NOT added here, though the plan lists them under this task:
-- `llm_scored` and `llm_provider_mix`. `run_log` is a Layer 1 table, and those
-- are Layer 2 facts; writing them from score/ is precisely the cross-layer
-- write that scripts/check_boundaries.py exists to stop. They belong to Phase
-- 7.1's `provider_usage` table, which the status strip (1.2) and the weekly
-- report (9.1) can read instead. Recorded in docs/DECISIONS.md.

alter table run_log
  add column if not exists trigger_source text,
  add column if not exists duration_s     numeric(10, 3);

comment on column run_log.trigger_source is
  'Which trigger started this run: cronjob | cf-standby | schedule | manual. '
  'NULL on runs predating 2026-10-15. See db/migrations/0005.';

comment on column run_log.duration_s is
  'Wall-clock seconds from the start of polling to the final write, measured '
  'in-process. NULL on runs predating 2026-10-15.';
