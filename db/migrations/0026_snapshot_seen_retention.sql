-- 0026_snapshot_seen_retention.sql -- specs/001-dashboard-speed-retention
--
-- 1. jobs.pinged_at: the never-ping-twice record moves off `notifications`,
--    which becomes a 30-day delivery log (retention v2, 0028).
-- 2. seen_jobs + job_keys + jobs_skip_seen: a posting removed by retention
--    keeps its identity for 180 days, so the board listing it again neither
--    re-inserts nor re-pings it. Ingest reads job_keys (one request per board,
--    as before) and skips seen_only keys; the trigger is the safety net.
-- 3. daily_stats: run_log rolled up per day before old runs are deleted.
-- 4. dashboard_snapshot + refresh_dashboard_snapshot(): every headline number
--    the dashboard shows, computed in Postgres at the end of each ingest run.
--    The dashboard read ~50 sequential pages from Washington to Mumbai per
--    view to compute these itself (research.md R1).
-- Idempotent.

-- 1 -------------------------------------------------------------------------
alter table jobs add column if not exists pinged_at timestamptz;

update jobs j set pinged_at = p.first_ping
  from (select job_id, min(sent_at) first_ping from notifications
         where ok and job_id is not null group by job_id) p
 where j.id = p.job_id and j.pinged_at is null;

create index if not exists jobs_unpinged_idx on jobs (id) where pinged_at is null;

-- 2 -------------------------------------------------------------------------
create table if not exists seen_jobs (
  company_id    bigint      not null,
  ats_job_id    text        not null,
  content_hash  text,
  source        text,
  first_seen_at timestamptz,
  pinged_at     timestamptz,
  removed_at    timestamptz not null default now(),
  primary key (company_id, ats_job_id)
);
create index if not exists seen_jobs_removed_idx on seen_jobs (removed_at);
alter table seen_jobs enable row level security;

create or replace function jobs_skip_seen() returns trigger
language plpgsql as $$
begin
  if exists (select 1 from seen_jobs s
              where s.company_id = new.company_id and s.ats_job_id = new.ats_job_id) then
    return null;   -- removed by retention within 180 days: not new, never re-pinged
  end if;
  return new;
end $$;

drop trigger if exists jobs_skip_seen on jobs;
create trigger jobs_skip_seen before insert on jobs
  for each row execute function jobs_skip_seen();

create or replace view job_keys as
  select j.id, j.company_id, j.ats_job_id, j.content_hash, j.closed_at, j.reopen_count,
         j.last_seen_at, j.description, j.source, false as seen_only
    from jobs j
  union all
  select null::bigint, s.company_id, s.ats_job_id, s.content_hash, null::timestamptz,
         0, null::timestamptz, null::text, s.source, true
    from seen_jobs s
   where not exists (select 1 from jobs j
                      where j.company_id = s.company_id and j.ats_job_id = s.ats_job_id);

-- 3 -------------------------------------------------------------------------
create table if not exists daily_stats (
  day            date    not null,
  worker         text    not null,
  runs           integer not null default 0,
  error_runs     integer not null default 0,
  jobs_seen      bigint  not null default 0,
  jobs_new       bigint  not null default 0,
  jobs_closed    bigint  not null default 0,
  duration_s_sum numeric not null default 0,
  duration_s_max numeric,
  primary key (day, worker)
);
alter table daily_stats enable row level security;

-- 4 -------------------------------------------------------------------------
create table if not exists dashboard_snapshot (
  id          smallint primary key default 1 check (id = 1),
  computed_at timestamptz not null,
  data        jsonb not null
);
alter table dashboard_snapshot enable row level security;

create or replace function refresh_dashboard_snapshot(
  p_threshold numeric default 6.5,     -- config/settings.yaml: ping_threshold
  p_max_age_days integer default 5     -- config/settings.yaml: max_display_age_days
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  out jsonb;
  last_run record;
  week_start timestamptz := now() - interval '7 days';
  live_providers int;
  level text := 'green';
  reasons jsonb := '[]'::jsonb;
begin
  select started_at, finished_at, jobs_seen, jobs_new, jobs_closed, trigger_source,
         duration_s, boards
    into last_run
    from run_log where worker = 'ingest' order by started_at desc limit 1;

  select count(distinct provider) into live_providers
    from job_scores where scored_at > now() - interval '24 hours'
     and fit_score is not null and provider is not null;

  with open_jobs as (
    select j.id, coalesce(j.posted_at, j.first_seen_at) eff, j.role_family, j.first_seen_at,
           s.job_id is not null as scored, s.fit_score, s.reject_reason, s.location_ok
      from jobs j left join job_scores s on s.job_id = j.id
     where j.closed_at is null
  ), queue as (
    select o.* from open_jobs o
     where o.fit_score >= p_threshold and o.location_ok
       and not exists (select 1 from applications a where a.job_id = o.id)
  )
  select jsonb_build_object(
    'version', 1,
    'threshold', p_threshold,
    'max_age_days', p_max_age_days,
    'open_jobs', (select count(*) from open_jobs),
    'unscored', (select count(*) from open_jobs where not scored),
    'stranded', (select count(*) from job_scores where reject_reason like 'scoring_failed%'),
    'llm_scored', (select count(*) from job_scores where fit_score is not null),
    'hidden_by_age', (select count(*) from queue
                       where p_max_age_days > 0 and eff < now() - make_interval(days => p_max_age_days)),
    'queue', jsonb_build_object(
       'total', (select count(*) from queue
                  where p_max_age_days = 0 or eff >= now() - make_interval(days => p_max_age_days)),
       'by_family', coalesce((select jsonb_object_agg(f, n) from (
           select coalesce(role_family, 'other') f, count(*) n from queue
            where p_max_age_days = 0 or eff >= now() - make_interval(days => p_max_age_days)
            group by 1) x), '{}'::jsonb)),
    'new_24h', jsonb_build_object(
       'total', (select count(*) from queue where first_seen_at > now() - interval '24 hours'),
       'by_family', coalesce((select jsonb_object_agg(f, n) from (
           select coalesce(role_family, 'other') f, count(*) n from queue
            where first_seen_at > now() - interval '24 hours' group by 1) x), '{}'::jsonb)),
    'week', jsonb_build_object(
       'applied', (select count(*) from applications
                    where applied_at >= week_start and status <> 'skipped'),
       'responses', (select count(distinct application_id) from application_events
                      where match_state = 'matched' and received_at >= week_start
                        and kind in ('rejection', 'assessment', 'interview_invite')),
       'referral_asks', (select count(*) from referral_asks where asked_at >= week_start)),
    'applied_total', (select count(*) from applications where applied_at is not null),
    'last_run', case when last_run.started_at is null then null else jsonb_build_object(
       'started_at', last_run.started_at, 'finished_at', last_run.finished_at,
       'jobs_seen', last_run.jobs_seen, 'jobs_new', last_run.jobs_new,
       'jobs_closed', last_run.jobs_closed, 'trigger', last_run.trigger_source,
       'duration_s', last_run.duration_s, 'boards', coalesce(last_run.boards, '[]'::jsonb)) end,
    'runs_24h', (select count(*) from run_log
                  where worker = 'ingest' and started_at > now() - interval '24 hours'),
    'inactive_boards', coalesce((select jsonb_agg(name order by name) from companies
                                  where not active and ats <> 'aggregator'), '[]'::jsonb),
    'providers_7d', coalesce((select jsonb_agg(jsonb_build_object('provider', p, 'count', n)
                                               order by n desc) from (
         select coalesce(provider, 'unrecorded') p, count(*) n from job_scores
          where fit_score is not null and scored_at > now() - interval '7 days'
          group by 1) x), '[]'::jsonb),
    'review_pending', (select count(*) from application_events
                        where match_state in ('needs_review', 'unmatched')
                          and kind in ('rejection', 'assessment', 'interview_invite'))
  ) into out;

  -- Health, in plain words (contracts/snapshot.schema.md).
  if last_run.started_at is null or last_run.started_at < now() - interval '45 minutes' then
    level := 'red';
    reasons := reasons || to_jsonb('No check for new jobs in the last 45 minutes'::text);
  end if;
  if (out->>'stranded')::int > 0 then
    level := 'red';
    reasons := reasons || to_jsonb(format('%s job%s could not be scored', out->>'stranded',
               case when (out->>'stranded')::int = 1 then '' else 's' end));
  end if;
  if (out->>'unscored')::int > 0 and level = 'green' then
    level := 'amber';
    reasons := reasons || to_jsonb(format('%s new job%s still being scored', out->>'unscored',
               case when (out->>'unscored')::int = 1 then '' else 's' end));
  end if;
  if coalesce(live_providers, 0) < 2 and level = 'green' then
    level := 'amber';
    reasons := reasons || to_jsonb('Only one scoring model is working'::text);
  end if;
  out := out || jsonb_build_object('health', jsonb_build_object(
           'level', level, 'reasons', reasons, 'live_providers_24h', coalesce(live_providers, 0)));

  insert into dashboard_snapshot (id, computed_at, data) values (1, now(), out)
  on conflict (id) do update set computed_at = excluded.computed_at, data = excluded.data;
  return out;
end $$;

-- v_funnel: first ping from jobs.pinged_at, which survives the 30-day
-- notification window. Same columns, same order as 0023.
create or replace view v_funnel as
 WITH stages AS (
   SELECT application_id,
          min(received_at) FILTER (WHERE kind = 'acknowledgement') AS acknowledged_at,
          min(received_at) FILTER (WHERE kind = 'rejection') AS rejected_at,
          min(received_at) FILTER (WHERE kind = 'assessment') AS assessment_at,
          min(received_at) FILTER (WHERE kind = 'interview_invite') AS interview_at
     FROM application_events
    WHERE match_state = 'matched' AND application_id IS NOT NULL
    GROUP BY application_id)
 SELECT a.id AS application_id, a.job_id, a.status, a.applied_at, a.responded_at,
        a.via_referral, a.referral_contact_id, a.hours_since_posted,
        c.name AS company, c.priority AS company_tier, j.role_family, j.posted_at,
        j.posted_at_precision, j.first_seen_at, s.fit_score, s.min_years,
        j.pinged_at AS first_ping_at,
        CASE WHEN j.posted_at_precision = 'timestamp'
             THEN EXTRACT(epoch FROM j.first_seen_at - j.posted_at) / 3600::numeric END AS discover_hours,
        EXTRACT(epoch FROM j.pinged_at - j.first_seen_at) / 3600::numeric AS ping_lag_hours,
        CASE WHEN a.applied_at >= j.pinged_at
             THEN EXTRACT(epoch FROM a.applied_at - j.pinged_at) / 3600::numeric END AS ping_to_apply_hours,
        a.applied_at < j.pinged_at AS applied_before_ping,
        st.acknowledged_at, st.rejected_at, st.assessment_at, st.interview_at,
        CASE WHEN st.rejected_at IS NOT NULL AND a.applied_at IS NOT NULL
             THEN (st.rejected_at - a.applied_at) < interval '48 hours' END AS rejected_within_48h,
        a.resume_variant, a.notice_period_days, a.current_ctc_lpa, a.expected_ctc_lpa,
        a.yoe_stated, a.relocation_ok, a.channel
   FROM applications a
   JOIN jobs j ON j.id = a.job_id
   JOIN companies c ON c.id = j.company_id
   LEFT JOIN job_scores s ON s.job_id = a.job_id
   LEFT JOIN stages st ON st.application_id = a.id;

-- Grants: service_role only (the pipeline and the dashboard's server side).
revoke all on function refresh_dashboard_snapshot(numeric, integer) from public;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on function refresh_dashboard_snapshot(numeric, integer) from anon;
    revoke all on seen_jobs, daily_stats, dashboard_snapshot, job_keys from anon;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on function refresh_dashboard_snapshot(numeric, integer) from authenticated;
    revoke all on seen_jobs, daily_stats, dashboard_snapshot, job_keys from authenticated;
  end if;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant execute on function refresh_dashboard_snapshot(numeric, integer) to service_role;
    grant all on seen_jobs, daily_stats, dashboard_snapshot to service_role;
    grant select on job_keys to service_role;
  end if;
end $$;
