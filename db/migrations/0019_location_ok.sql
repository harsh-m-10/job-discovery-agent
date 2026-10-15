-- 0019_location_ok.sql -- the operator's city as a stored flag (2026-10-16).
--
-- Bengaluru only. score/run.py rejects other cities before scoring, and on
-- every run sets location_ok on every scored row from common/geo.py
-- in_preferred_location, so jobs scored before the rule are filtered too
-- without losing their scores. The dashboard reads the flag rather than
-- re-implementing the city match (scripts/check_boundaries.py forbids that).

alter table job_scores
  add column if not exists location_ok boolean not null default true;

comment on column job_scores.location_ok is
  'In the operator''s preferred location (config/settings.yaml:preferred_location). '
  'Maintained by score/run.py. See 0019.';

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
  j.source,
  s.location_ok
from jobs j
join companies c   on c.id = j.company_id
join job_scores s  on s.job_id = j.id and s.fit_score is not null
left join applications a on a.job_id = j.id
where j.closed_at is null;
