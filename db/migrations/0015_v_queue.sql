-- 0015_v_queue.sql — the dashboard queue as a view (docs/PLAN.md 8.1).
--
-- One row per open job that carries a real score, with everything the queue
-- card needs: job, company, score, application status and age. The dashboard
-- used to assemble this in TypeScript by reading every open job (4,200+, five
-- pages) to join against a few hundred scores; it now asks this view for
-- fit_score >= threshold, status = queued, age_days <= window, and gets only
-- the rows it shows.
--
-- age_days follows the rule used everywhere else: the ATS posting time, or our
-- first sighting when the ATS gave none (dashboard/lib/db.ts effectivePostedAt,
-- notify/run.py, score/run.py). Defining it here is what lets the read side
-- stop re-deriving it.

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
                                      as age_days
from jobs j
join companies c   on c.id = j.company_id
join job_scores s  on s.job_id = j.id and s.fit_score is not null
left join applications a on a.job_id = j.id
where j.closed_at is null;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on v_queue from anon;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on v_queue from authenticated;
  end if;
end $$;

comment on view v_queue is
  'Open, LLM- or rules-scored jobs with company, score, application status and '
  'age_days. The dashboard queue reads only this. See db/migrations/0015.';
