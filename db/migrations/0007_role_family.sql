-- 0007_role_family.sql — the role family of every job, classified from its title.
--
-- docs/PLAN.md Phase 3.3 (resume variant in each ping) and 4.5 (funnel sliced
-- by role family). The classifier lives in common/role.py; ingest writes the
-- column on every upsert so SQL and the dashboard read it instead of
-- re-implementing the rules in TypeScript (the Phase 8 rule).
--
-- Backfilled by scripts/backfill_role_family.py, because the classifier is
-- Python. Re-run that script whenever common/role.py changes.
--
-- Like 0006, there is no missing-column fallback on the jobs upsert, so this
-- must be applied before the code that writes it is deployed.

alter table jobs
  add column if not exists role_family text
    check (role_family in ('genai', 'ml', 'data', 'fullstack', 'backend',
                           'platform', 'security', 'other'));

comment on column jobs.role_family is
  'Classified from the title by common/role.py. NULL only on rows written '
  'before 0007 and not yet backfilled.';
