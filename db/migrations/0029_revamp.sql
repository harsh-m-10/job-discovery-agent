-- 0029_revamp.sql -- specs/002-dashboard-revamp.
--
-- 1. Application stages for the PIPELINE screen: applied -> screen ->
--    assessment -> interview -> offer, plus rejected. `responded` stays valid
--    (older rows, shown as Screen). Next step and its date, editable from
--    TODAY's "In progress" cards; skip_reason from JOBS' Skip.
-- 2. v_queue gains company_tier (companies.priority) for the Tier chips.
-- 3. Snapshot v2: in-progress count, new jobs by source (the Health page's
--    yield), and the latest canary and backup results, which feed the health
--    dot. canary.yml and backup.yml write those rows via scripts/log_worker.py.
-- Idempotent.

-- 1 -------------------------------------------------------------------------
alter table applications drop constraint if exists applications_status_check;
alter table applications add constraint applications_status_check check (status = any (array[
  'queued', 'applied', 'skipped', 'screen', 'responded', 'assessment', 'interviewing',
  'rejected', 'offer']));
alter table applications add column if not exists next_step text;
alter table applications add column if not exists next_step_at date;
alter table applications add column if not exists skip_reason text;

-- 2 -------------------------------------------------------------------------
create or replace view v_queue as
 SELECT j.id AS job_id, j.title, j.location, j.absolute_url, j.compensation, j.posted_at,
        j.posted_at_precision, j.first_seen_at, j.role_family, c.name AS company,
        c.category AS company_category, c.headcount_band, c.company_key, s.fit_score,
        s.verdict, s.provisional, s.min_years, s.max_years, s.matched_skills, s.gap_skills,
        s.reasoning, COALESCE(a.status, 'queued'::text) AS status,
        EXTRACT(epoch FROM now() - COALESCE(j.posted_at, j.first_seen_at)) / 86400::numeric AS age_days,
        j.source, s.location_ok,
        c.priority AS company_tier
   FROM jobs j
     JOIN companies c ON c.id = j.company_id
     JOIN job_scores s ON s.job_id = j.id AND s.fit_score IS NOT NULL
     LEFT JOIN applications a ON a.job_id = j.id
  WHERE j.closed_at IS NULL;

-- 3 -------------------------------------------------------------------------
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
  canary record;
  bkp record;
  week_start timestamptz := now() - interval '7 days';
  live_providers int;
  level text := 'green';
  reasons jsonb := '[]'::jsonb;
begin
  select started_at, finished_at, jobs_seen, jobs_new, jobs_closed, trigger_source,
         duration_s, boards
    into last_run
    from run_log where worker = 'ingest' order by started_at desc limit 1;
  select started_at, coalesce(errors::text, '[]') in ('[]', 'null', '{}') as ok
    into canary from run_log where worker = 'canary' order by started_at desc limit 1;
  select started_at, coalesce(errors::text, '[]') in ('[]', 'null', '{}') as ok
    into bkp from run_log where worker = 'backup' order by started_at desc limit 1;

  select count(distinct provider) into live_providers
    from job_scores where scored_at > now() - interval '24 hours'
     and fit_score is not null and provider is not null;

  with open_jobs as (
    select j.id, coalesce(j.posted_at, j.first_seen_at) eff, j.role_family, j.first_seen_at,
           coalesce(j.source, 'ats') source,
           s.job_id is not null as scored, s.fit_score, s.reject_reason, s.location_ok
      from jobs j left join job_scores s on s.job_id = j.id
     where j.closed_at is null
  ), queue as (
    select o.* from open_jobs o
     where o.fit_score >= p_threshold and o.location_ok
       and not exists (select 1 from applications a where a.job_id = o.id)
  )
  select jsonb_build_object(
    'version', 2,
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
            where first_seen_at > now() - interval '24 hours' group by 1) x), '{}'::jsonb),
       -- Everything found in 24 h, matched or not: the yield of each source.
       'by_source', coalesce((select jsonb_object_agg(src, n) from (
           select coalesce(source, 'ats') src, count(*) n from jobs
            where first_seen_at > now() - interval '24 hours' group by 1) x), '{}'::jsonb)),
    'week', jsonb_build_object(
       'applied', (select count(*) from applications
                    where applied_at >= week_start and status <> 'skipped'),
       'responses', (select count(distinct application_id) from application_events
                      where match_state = 'matched' and received_at >= week_start
                        and kind in ('rejection', 'assessment', 'interview_invite')),
       'referral_asks', (select count(*) from referral_asks where asked_at >= week_start)),
    'in_progress', (select count(*) from applications
                     where status in ('screen', 'responded', 'assessment', 'interviewing', 'offer')),
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
                          and kind in ('rejection', 'assessment', 'interview_invite')),
    'ops', jsonb_build_object(
       'canary', case when canary.started_at is null then null
                      else jsonb_build_object('at', canary.started_at, 'ok', canary.ok) end,
       'backup', case when bkp.started_at is null then null
                      else jsonb_build_object('at', bkp.started_at, 'ok', bkp.ok) end)
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
  if (out->>'unscored')::int > 0 then
    if level = 'green' then level := 'amber'; end if;
    reasons := reasons || to_jsonb(format('%s new job%s still being scored', out->>'unscored',
               case when (out->>'unscored')::int = 1 then '' else 's' end));
  end if;
  if coalesce(live_providers, 0) < 2 then
    if level = 'green' then level := 'amber'; end if;
    reasons := reasons || to_jsonb('Only one scoring model is working'::text);
  end if;
  if canary.started_at is not null and not canary.ok then
    if level = 'green' then level := 'amber'; end if;
    reasons := reasons || to_jsonb('The daily self-check found a problem'::text);
  end if;
  if bkp.started_at is not null and (not bkp.ok or bkp.started_at < now() - interval '30 hours') then
    if level = 'green' then level := 'amber'; end if;
    reasons := reasons || to_jsonb(case when not bkp.ok then 'Last night''s backup failed'
                                        else 'No backup in the last day' end);
  end if;
  out := out || jsonb_build_object('health', jsonb_build_object(
           'level', level, 'reasons', reasons, 'live_providers_24h', coalesce(live_providers, 0)));

  insert into dashboard_snapshot (id, computed_at, data) values (1, now(), out)
  on conflict (id) do update set computed_at = excluded.computed_at, data = excluded.data;
  return out;
end $$;

revoke all on function refresh_dashboard_snapshot(numeric, integer) from public;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on function refresh_dashboard_snapshot(numeric, integer) from anon;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on function refresh_dashboard_snapshot(numeric, integer) from authenticated;
  end if;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant execute on function refresh_dashboard_snapshot(numeric, integer) to service_role;
  end if;
end $$;
