-- 0008_v_funnel.sql — one row per application, with every latency stage.
--
-- docs/PLAN.md 3.4 (ping -> apply latency) and the start of 4.5 (the funnel as
-- a view). The dashboard reads this instead of joining applications, jobs,
-- scores and notifications in TypeScript.
--
-- The stages split the posting-to-apply time into the part the machine owns
-- and the part the operator owns. Measured 2026-10-15 on the 13 applications
-- with an exact posting time that were pinged before applying: posting ->
-- discovered median 4.0h, discovered -> pinged 0.0h, pinged -> applied 1.0h.
-- The plan assumed human latency was as large as machine latency; it is about
-- a fifth of it.
--
-- SECURITY: views run as their owner by default, which would bypass row-level
-- security on the base tables, and Supabase grants SELECT on new public
-- objects to the anon and authenticated roles. Together that would expose
-- applications to anyone holding the anon key. security_invoker makes the
-- view obey the caller's RLS, and the explicit revokes remove the grants. The
-- dashboard uses the service role, which bypasses RLS by design.

create or replace view v_funnel
with (security_invoker = true) as
with first_ping as (
  select job_id, min(sent_at) as first_ping_at
    from notifications
   where ok and job_id is not null
   group by job_id
)
select
  a.id                    as application_id,
  a.job_id,
  a.status,
  a.applied_at,
  a.responded_at,
  a.via_referral,
  a.referral_contact_id,
  a.hours_since_posted,             -- frozen at apply time; see 0006 for precision
  c.name                  as company,
  c.priority              as company_tier,
  j.role_family,
  j.posted_at,
  j.posted_at_precision,
  j.first_seen_at,
  s.fit_score,
  s.min_years,
  p.first_ping_at,
  -- Machine: posting to first sighting. Only meaningful for an exact posting
  -- time; a date-only posted_at would add up to a day of invented delay.
  case when j.posted_at_precision = 'timestamp'
       then extract(epoch from j.first_seen_at - j.posted_at) / 3600 end
                          as discover_hours,
  -- Machine: first sighting to first delivered ping.
  extract(epoch from p.first_ping_at - j.first_seen_at) / 3600
                          as ping_lag_hours,
  -- Operator: ping to application. NULL when the application came first.
  case when a.applied_at >= p.first_ping_at
       then extract(epoch from a.applied_at - p.first_ping_at) / 3600 end
                          as ping_to_apply_hours,
  -- The job was found and applied to before the pipeline pinged it at all.
  (a.applied_at < p.first_ping_at)  as applied_before_ping
from applications a
join jobs j       on j.id = a.job_id
join companies c  on c.id = j.company_id
left join job_scores s on s.job_id = a.job_id
left join first_ping p on p.job_id = a.job_id;

-- Guarded because CI's backup self-test replays every migration onto vanilla
-- Postgres, where Supabase's roles do not exist.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on v_funnel from anon;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on v_funnel from authenticated;
  end if;
end $$;

comment on view v_funnel is
  'One row per application with latency stages split into machine '
  '(discover_hours, ping_lag_hours) and operator (ping_to_apply_hours). '
  'See db/migrations/0008.';
