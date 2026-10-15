-- 0010_outcome_capture.sql — outcomes from email, and what was asked at apply.
--
-- docs/PLAN.md Phase 4 (gap G4). 37 applications, 0 outcomes recorded: the
-- system was built to learn whether speed and referrals convert, and nothing
-- wrote the answer back. Rejections were being deleted from Gmail.
--
-- Flow:
--   Gmail label job/outcome --(Apps Script)--> POST /api/events   [stores raw]
--   each pipeline run: outcomes/run.py classifies, matches to an application
--   by company key, and applies the status change.          [all logic Python]
--   anything ambiguous lands in the dashboard's review queue; resolving it only
--   sets application_id, and the next run applies the effect.
--
-- The dashboard writes raw rows and resolutions; it never classifies.

create table if not exists application_events (
  id               bigserial primary key,
  message_id       text not null unique,          -- Gmail id: makes re-posting idempotent
  received_at      timestamptz not null,
  sender           text,
  subject          text,
  snippet          text,
  -- set by outcomes/run.py; NULL until processed
  kind             text check (kind in ('rejection', 'interview_invite', 'assessment',
                                        'acknowledgement', 'other')),
  match_state      text not null default 'pending'
                   check (match_state in ('pending', 'matched', 'needs_review',
                                          'unmatched', 'ignored')),
  application_id   bigint references applications(id),
  candidates       jsonb,                          -- application ids considered, for review
  effect_applied   boolean not null default false, -- status change written?
  resolved_by      text check (resolved_by in ('auto', 'operator')),
  created_at       timestamptz not null default now(),
  processed_at     timestamptz
);
create index if not exists application_events_state_idx on application_events (match_state);
create index if not exists application_events_app_idx on application_events (application_id);

alter table application_events enable row level security;

-- Phase 4.4: what the application form asked. Notice period and CTC are
-- common screen-outs in India; the funnel should be able to show whether they
-- are why applications die. All optional.
alter table applications
  add column if not exists resume_variant      text,
  add column if not exists notice_period_days  int,
  add column if not exists current_ctc_lpa     numeric(6,2),
  add column if not exists expected_ctc_lpa    numeric(6,2),
  add column if not exists yoe_stated          numeric(4,1),
  add column if not exists relocation_ok       boolean;

comment on table application_events is
  'One row per outcome email. Raw from /api/events; classified and matched by '
  'outcomes/run.py. See db/migrations/0010.';

-- v_funnel gains the outcome stages: first acknowledgement, rejection,
-- assessment and interview per application, from matched events. A rejection
-- inside 48h of applying is almost always an automated screen; later, a human
-- read it -- the two mean different things for targeting.
create or replace view v_funnel
with (security_invoker = true) as
with first_ping as (
  select job_id, min(sent_at) as first_ping_at
    from notifications
   where ok and job_id is not null
   group by job_id
),
stages as (
  select application_id,
         min(received_at) filter (where kind = 'acknowledgement')  as acknowledged_at,
         min(received_at) filter (where kind = 'rejection')        as rejected_at,
         min(received_at) filter (where kind = 'assessment')       as assessment_at,
         min(received_at) filter (where kind = 'interview_invite') as interview_at
    from application_events
   where match_state = 'matched' and application_id is not null
   group by application_id
)
select
  a.id                    as application_id,
  a.job_id,
  a.status,
  a.applied_at,
  a.responded_at,
  a.via_referral,
  a.referral_contact_id,
  a.hours_since_posted,
  c.name                  as company,
  c.priority              as company_tier,
  j.role_family,
  j.posted_at,
  j.posted_at_precision,
  j.first_seen_at,
  s.fit_score,
  s.min_years,
  p.first_ping_at,
  case when j.posted_at_precision = 'timestamp'
       then extract(epoch from j.first_seen_at - j.posted_at) / 3600 end
                          as discover_hours,
  extract(epoch from p.first_ping_at - j.first_seen_at) / 3600
                          as ping_lag_hours,
  case when a.applied_at >= p.first_ping_at
       then extract(epoch from a.applied_at - p.first_ping_at) / 3600 end
                          as ping_to_apply_hours,
  (a.applied_at < p.first_ping_at)  as applied_before_ping,
  -- Phase 4.5 stages
  st.acknowledged_at,
  st.rejected_at,
  st.assessment_at,
  st.interview_at,
  case when st.rejected_at is not null and a.applied_at is not null
       then st.rejected_at - a.applied_at < interval '48 hours' end
                          as rejected_within_48h,
  -- Phase 4.4 apply-time answers
  a.resume_variant,
  a.notice_period_days,
  a.current_ctc_lpa,
  a.expected_ctc_lpa,
  a.yoe_stated,
  a.relocation_ok
from applications a
join jobs j       on j.id = a.job_id
join companies c  on c.id = j.company_id
left join job_scores s on s.job_id = a.job_id
left join first_ping p on p.job_id = a.job_id
left join stages st    on st.application_id = a.id;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on v_funnel from anon;
    revoke all on application_events from anon;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on v_funnel from authenticated;
    revoke all on application_events from authenticated;
  end if;
end $$;
