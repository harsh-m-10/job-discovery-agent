-- 0023_application_channel.sql -- where an application was made (2026-10-16).
--
-- Harsh applies from the Naukri and Indeed apps when a ping comes from those
-- sites. applications.channel records which, so the funnel can compare
-- sources: 'indeed' | 'naukri' | 'linkedin' (alert-email jobs carry the site
-- they came from in jobs.raw->>'site') | 'direct' (the company's own board).
-- Set by trigger when an application first reaches a submitted status, so
-- every path that marks a job applied (dashboard button, apply-time form,
-- outcome emails) gets it without each one knowing the rule. An explicit
-- value is never overwritten.

alter table applications
  add column if not exists channel text
    check (channel in ('direct', 'indeed', 'naukri', 'linkedin'));

create or replace function application_channel(p_job_id bigint) returns text
language sql stable as $$
  select case
           when j.source = 'indeed' then 'indeed'
           when j.source = 'alert-email'
                and (j.raw->>'site') in ('indeed', 'naukri', 'linkedin') then j.raw->>'site'
           else 'direct'
         end
  from jobs j where j.id = p_job_id
$$;

create or replace function set_application_channel() returns trigger
language plpgsql as $$
begin
  if new.channel is null and new.status in ('applied', 'interviewing', 'offer', 'rejected') then
    new.channel := application_channel(new.job_id);
  end if;
  return new;
end $$;

drop trigger if exists applications_channel on applications;
create trigger applications_channel
  before insert or update of status, channel on applications
  for each row execute function set_application_channel();

update applications set channel = application_channel(job_id)
 where channel is null and status in ('applied', 'interviewing', 'offer', 'rejected');

comment on column applications.channel is
  'direct | indeed | naukri | linkedin -- where the application was made, '
  'from the job''s source. Set by trigger; see 0023.';

-- v_funnel gains the channel (appended; create or replace may only add at the end).
create or replace view v_funnel
with (security_invoker = true) as
with first_ping as (
  select job_id, min(sent_at) as first_ping_at
    from notifications where ok and job_id is not null group by job_id
), stages as (
  select application_id,
         min(received_at) filter (where kind = 'acknowledgement') as acknowledged_at,
         min(received_at) filter (where kind = 'rejection') as rejected_at,
         min(received_at) filter (where kind = 'assessment') as assessment_at,
         min(received_at) filter (where kind = 'interview_invite') as interview_at
    from application_events
   where match_state = 'matched' and application_id is not null
   group by application_id
)
select a.id as application_id, a.job_id, a.status, a.applied_at, a.responded_at,
       a.via_referral, a.referral_contact_id, a.hours_since_posted,
       c.name as company, c.priority as company_tier, j.role_family,
       j.posted_at, j.posted_at_precision, j.first_seen_at,
       s.fit_score, s.min_years, p.first_ping_at,
       case when j.posted_at_precision = 'timestamp'
            then extract(epoch from j.first_seen_at - j.posted_at) / 3600 end as discover_hours,
       extract(epoch from p.first_ping_at - j.first_seen_at) / 3600 as ping_lag_hours,
       case when a.applied_at >= p.first_ping_at
            then extract(epoch from a.applied_at - p.first_ping_at) / 3600 end as ping_to_apply_hours,
       a.applied_at < p.first_ping_at as applied_before_ping,
       st.acknowledged_at, st.rejected_at, st.assessment_at, st.interview_at,
       case when st.rejected_at is not null and a.applied_at is not null
            then st.rejected_at - a.applied_at < interval '48 hours' end as rejected_within_48h,
       a.resume_variant, a.notice_period_days, a.current_ctc_lpa, a.expected_ctc_lpa,
       a.yoe_stated, a.relocation_ok,
       a.channel
  from applications a
  join jobs j on j.id = a.job_id
  join companies c on c.id = j.company_id
  left join job_scores s on s.job_id = a.job_id
  left join first_ping p on p.job_id = a.job_id
  left join stages st on st.application_id = a.id;
