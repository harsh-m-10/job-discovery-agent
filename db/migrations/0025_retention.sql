-- 0025_retention.sql -- keep the database from piling up (decided 2026-10-16).
--
-- Free tier: 500 MB. At the time: 53 MB after two months, 34 MB of it jobs,
-- almost all descriptions. A blanket "delete after 15 days" would break
-- things: notifications rows ARE the never-ping-twice record, and
-- applications, notifications and referral_asks reference jobs. So:
--
--   jobs closed > p_days, never pinged, applied to or asked about -> deleted
--        (job_scores go with them: ON DELETE CASCADE)
--   jobs closed > p_days that are referenced -> row kept, description and raw
--        dropped (the bulk of the space; the history and dedupe survive)
--   alert_inbox processed > p_days -> deleted
--   notifications that failed > p_days -> deleted (successes are the dedupe)
--   trigger_log > 30 days, run_log > 90 days -> deleted (reports look back 7)
--
-- Called nightly by .github/workflows/backup.yml after the encrypted backup
-- has been pushed, so everything removed is still in that night's backup.
-- A posting deleted here that reappears on its board comes back as new.

create or replace function prune_old_data(p_days integer default 15)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  cutoff timestamptz := now() - make_interval(days => p_days);
  n_deleted int; n_stripped int; n_inbox int; n_notif int; n_trig int; n_runs int;
begin
  with referenced as (
    select job_id from applications
    union select job_id from notifications where job_id is not null
    union select job_id from referral_asks
  )
  delete from jobs j
   where j.closed_at < cutoff
     and not exists (select 1 from referenced r where r.job_id = j.id);
  get diagnostics n_deleted = row_count;

  update jobs set description = null, raw = '{}'::jsonb
   where closed_at < cutoff and (description is not null or raw <> '{}'::jsonb);
  get diagnostics n_stripped = row_count;

  delete from alert_inbox where processed_at < cutoff;
  get diagnostics n_inbox = row_count;

  delete from notifications where not ok and sent_at < cutoff;
  get diagnostics n_notif = row_count;

  delete from trigger_log where fired_at < now() - interval '30 days';
  get diagnostics n_trig = row_count;

  delete from run_log where started_at < now() - interval '90 days';
  get diagnostics n_runs = row_count;

  return jsonb_build_object(
    'days', p_days, 'jobs_deleted', n_deleted, 'jobs_stripped', n_stripped,
    'alert_inbox_deleted', n_inbox, 'failed_notifications_deleted', n_notif,
    'trigger_log_deleted', n_trig, 'run_log_deleted', n_runs);
end $$;

revoke all on function prune_old_data(integer) from public;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on function prune_old_data(integer) from anon;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on function prune_old_data(integer) from authenticated;
  end if;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant execute on function prune_old_data(integer) to service_role;
  end if;
end $$;

comment on function prune_old_data(integer) is
  'Nightly retention: closed jobs past p_days deleted, or stripped if referenced; '
  'old alert mail, failed notifications and logs removed. See 0025.';
