-- 0018_aggregator_sources.sql -- postings that do not come from an ATS board.
--
-- Decided 2026-10-15 (PLAN 6.7): Indeed through python-jobspy (no login, low
-- volume, hourly), and the job-alert emails LinkedIn, Naukri and Indeed send
-- to the operator's Gmail, forwarded by apps_script/outcomes.gs. Neither is a
-- board that can be polled and diffed, so:
--
--   * their companies get ats = 'aggregator', board_token = company_key. They
--     are never polled (ingest/store.py active_companies skips them) and never
--     audited as boards.
--   * jobs.source says where a posting came from: 'ats' for every board row
--     (all rows so far), 'indeed', or 'alert-email'.
--   * they are never closed on absence -- a search result missing from the
--     next search says nothing about the posting -- only by TTL after
--     aggregator_ttl_days without a sighting (ingest/aggregate.py).
--   * alert_inbox holds the forwarded mail until ingest/aggregate.py parses it.
--     Parsing is server-side so a format change is a tested commit, not an
--     Apps Script redeploy.

alter table companies drop constraint if exists companies_ats_check;
alter table companies add constraint companies_ats_check check (ats = any (array[
  'greenhouse', 'lever', 'ashby', 'workday', 'smartrecruiters', 'successfactors',
  'aggregator']));

alter table jobs
  add column if not exists source text not null default 'ats';
alter table jobs drop constraint if exists jobs_source_check;
alter table jobs add constraint jobs_source_check
  check (source = any (array['ats', 'indeed', 'alert-email']));

comment on column jobs.source is
  'ats (a polled board) | indeed (python-jobspy) | alert-email (a forwarded job '
  'alert). Non-ats rows are never closed on absence, only by TTL. See 0018.';

create table if not exists alert_inbox (
  id            bigserial primary key,
  message_id    text not null unique,
  received_at   timestamptz,
  sender        text,
  subject       text,
  body          text,
  stored_at     timestamptz not null default now(),
  processed_at  timestamptz,
  jobs_found    integer,
  error         text
);

create index if not exists alert_inbox_unprocessed
  on alert_inbox (id) where processed_at is null;

comment on table alert_inbox is
  'Job-alert emails forwarded by apps_script/outcomes.gs (label job/alerts), '
  'drained by ingest/aggregate.py. See 0018.';

alter table alert_inbox enable row level security;

-- v_queue gains the source column (appended: create or replace view may only
-- add columns at the end).
create or replace view v_queue
with (security_invoker = true) as
select
  j.id                    as job_id,
  j.title,
  j.location,
  j.absolute_url,
  j.compensation,
  j.posted_at,
  j.posted_at_precision,
  j.first_seen_at,
  j.role_family,
  c.name                  as company,
  c.category              as company_category,
  c.headcount_band,
  c.company_key,
  s.fit_score,
  s.verdict,
  s.provisional,
  s.min_years,
  s.max_years,
  s.matched_skills,
  s.gap_skills,
  s.reasoning,
  coalesce(a.status, 'queued')        as status,
  extract(epoch from now() - coalesce(j.posted_at, j.first_seen_at)) / 86400
                                      as age_days,
  j.source
from jobs j
join companies c   on c.id = j.company_id
join job_scores s  on s.job_id = j.id and s.fit_score is not null
left join applications a on a.job_id = j.id
where j.closed_at is null;
