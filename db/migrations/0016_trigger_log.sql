-- 0016_trigger_log.sql -- every trigger fire, including the ones that collapse.
--
-- From 2026-10-15 four triggers start ingest.yml on purpose: cron-job.org
-- (:00/:15/:30/:45), a Cloudflare Worker (:07/:22/:37/:52, unconditional), the
-- Apps Script standby (only when the newest run is >25 min old) and GitHub's
-- own schedule. The concurrency group plus the 8-minute run lease collapse the
-- duplicates, and a collapsed run deliberately writes no run_log row (or the
-- next trigger would read the skip as a fresh run and skip too, forever).
--
-- That leaves run_log blind to a trigger that is healthy but always loses the
-- race -- and, worse, unable to tell it from one that has gone quiet. This
-- table records every fire with what became of it, so the weekly report can
-- show the mix per source and a silent trigger stands out.

create table if not exists trigger_log (
  id         bigserial primary key,
  fired_at   timestamptz not null default now(),
  source     text not null,
  outcome    text not null check (outcome in ('ran', 'lease')),
  run_id     bigint references run_log(id) on delete set null
);

create index if not exists trigger_log_fired_at on trigger_log (fired_at desc);

comment on table trigger_log is
  'One row per ingest trigger fire: ran (polled; run_id set) or lease '
  '(collapsed into a recent run). Layer 1, written by ingest/run.py. See 0016.';

alter table trigger_log enable row level security;
