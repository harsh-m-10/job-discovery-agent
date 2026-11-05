-- 0028_retention_v2.sql -- specs/001-dashboard-speed-retention (FR-010..013).
--
-- Replaces 0025's single 15-day rule with the operator's windows (2026-11-05):
--
--   never deleted     applications, application_events, referral_asks,
--                     connections (warm tags), and any job they reference
--   30 days           description and raw nulled, unless applied to
--   30 days           rejected or out-of-city postings never pinged: deleted
--   90 days           closed, or never acted on: deleted
--   30 days           notifications (pinged_at on jobs/seen_jobs is the
--                     never-ping-twice record since 0026), trigger_log,
--                     processed alert_inbox mail
--   90 days           run_log, after rolling each day into daily_stats
--   180 days          seen_jobs (identities of deleted postings)
--
-- Every deleted posting's identity goes to seen_jobs in the same statement,
-- so ingest never re-inserts or re-pings it (0026). A dry run does the same
-- work inside a sub-transaction and rolls it back, so its counts are exact.
-- Called nightly by backup.yml only after the encrypted backup succeeded.

create or replace function prune_old_data_v2(
  p_dry_run boolean default true,
  p_desc_days integer default 30,
  p_reject_days integer default 30,
  p_stale_days integer default 90,
  p_notif_days integer default 30,
  p_runlog_days integer default 90,
  p_seen_days integer default 180,
  p_trigger_days integer default 30,
  p_inbox_days integer default 30
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  r jsonb := jsonb_build_object('dry_run', p_dry_run);
  n int;
  jobs_out jsonb;
begin
  begin
    -- 1. run_log -> daily_stats, every finished day still present.
    insert into daily_stats (day, worker, runs, error_runs, jobs_seen, jobs_new,
                             jobs_closed, duration_s_sum, duration_s_max)
    select started_at::date, worker, count(*),
           count(*) filter (where errors is not null
                              and errors::text not in ('[]', '{}', 'null')),
           coalesce(sum(jobs_seen), 0), coalesce(sum(jobs_new), 0),
           coalesce(sum(jobs_closed), 0), coalesce(sum(duration_s), 0), max(duration_s)
      from run_log where started_at < date_trunc('day', now())
     group by 1, 2
    on conflict (day, worker) do update set
      runs = excluded.runs, error_runs = excluded.error_runs,
      jobs_seen = excluded.jobs_seen, jobs_new = excluded.jobs_new,
      jobs_closed = excluded.jobs_closed, duration_s_sum = excluded.duration_s_sum,
      duration_s_max = excluded.duration_s_max;
    get diagnostics n = row_count; r := r || jsonb_build_object('daily_stats_upserted', n);

    -- 2. Logs and expired identities.
    delete from notifications where sent_at < now() - make_interval(days => p_notif_days);
    get diagnostics n = row_count; r := r || jsonb_build_object('notifications_deleted', n);
    delete from trigger_log where fired_at < now() - make_interval(days => p_trigger_days);
    get diagnostics n = row_count; r := r || jsonb_build_object('trigger_log_deleted', n);
    delete from run_log where started_at < now() - make_interval(days => p_runlog_days);
    get diagnostics n = row_count; r := r || jsonb_build_object('run_log_deleted', n);
    delete from alert_inbox where processed_at < now() - make_interval(days => p_inbox_days);
    get diagnostics n = row_count; r := r || jsonb_build_object('alert_inbox_deleted', n);
    delete from seen_jobs where removed_at < now() - make_interval(days => p_seen_days);
    get diagnostics n = row_count; r := r || jsonb_build_object('seen_jobs_expired', n);

    -- 3. Postings. Protected: anything an application, a referral ask or a
    --    notification still kept (under p_notif_days) points at.
    with protected as (
      select job_id from applications
      union select job_id from referral_asks
      union select job_id from notifications where job_id is not null
    ), doomed as (
      select j.id, j.company_id, j.ats_job_id, j.content_hash, j.source,
             j.first_seen_at, j.pinged_at,
             coalesce((s.verdict = 'reject' or s.location_ok is false)
                      and j.pinged_at is null, false) as rejected
        from jobs j left join job_scores s on s.job_id = j.id
       where not exists (select 1 from protected p where p.job_id = j.id)
         and (
           ((s.verdict = 'reject' or s.location_ok is false) and j.pinged_at is null
             and j.first_seen_at < now() - make_interval(days => p_reject_days))
           or j.closed_at < now() - make_interval(days => p_stale_days)
           or j.first_seen_at < now() - make_interval(days => p_stale_days))
    ), remembered as (
      insert into seen_jobs (company_id, ats_job_id, content_hash, source,
                             first_seen_at, pinged_at)
      select company_id, ats_job_id, content_hash, source, first_seen_at, pinged_at
        from doomed
      on conflict (company_id, ats_job_id) do update
        set removed_at = now(),
            pinged_at = coalesce(seen_jobs.pinged_at, excluded.pinged_at)
      returning 1
    ), gone as (
      delete from jobs j using doomed d where j.id = d.id returning d.rejected
    )
    select jsonb_build_object(
             'jobs_deleted', (select count(*) from gone),
             'jobs_deleted_rejected', (select count(*) from gone where rejected),
             'jobs_deleted_stale', (select count(*) from gone where not rejected),
             'seen_recorded', (select count(*) from remembered))
      into jobs_out;
    r := r || jobs_out;

    -- 4. Descriptions of what is left, after p_desc_days, unless applied to.
    update jobs j set description = null, raw = '{}'::jsonb
     where j.first_seen_at < now() - make_interval(days => p_desc_days)
       and (j.description is not null or j.raw <> '{}'::jsonb)
       and not exists (select 1 from applications a
                        where a.job_id = j.id and a.status <> 'skipped');
    get diagnostics n = row_count; r := r || jsonb_build_object('descriptions_stripped', n);

    if p_dry_run then
      raise exception using errcode = 'P0001', message = 'dry_run_rollback';
    end if;
  exception
    when raise_exception then
      if sqlerrm <> 'dry_run_rollback' then raise; end if;
      -- Dry run: everything above is rolled back; the counts in r stand.
  end;
  return r;
end $$;

-- Database and table sizes, for prune.py's before/after report (FR-013).
create or replace function db_size_report() returns jsonb
language sql
security definer
set search_path = public
as $$
  select jsonb_build_object(
    'db_bytes', pg_database_size(current_database()),
    'tables', (select jsonb_object_agg(relname, jsonb_build_object(
                  'rows', n_live_tup, 'bytes', pg_total_relation_size(relid)))
                 from pg_stat_user_tables where schemaname = 'public'));
$$;

revoke all on function prune_old_data_v2(boolean, integer, integer, integer, integer,
                                         integer, integer, integer, integer) from public;
revoke all on function db_size_report() from public;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on function prune_old_data_v2(boolean, integer, integer, integer, integer,
                                             integer, integer, integer, integer) from anon;
    revoke all on function db_size_report() from anon;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on function prune_old_data_v2(boolean, integer, integer, integer, integer,
                                             integer, integer, integer, integer) from authenticated;
    revoke all on function db_size_report() from authenticated;
  end if;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant execute on function prune_old_data_v2(boolean, integer, integer, integer, integer,
                                                integer, integer, integer, integer) to service_role;
    grant execute on function db_size_report() to service_role;
  end if;
end $$;
