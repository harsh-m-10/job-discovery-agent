-- 0021_avature.sql -- Avature career portals (Siemens), 2026-10-16.
-- ingest/adapters/avature.py; HTML only, keyword search doubles as the location filter.

alter table companies drop constraint if exists companies_ats_check;
alter table companies add constraint companies_ats_check check (ats = any (array[
  'greenhouse', 'lever', 'ashby', 'workday', 'smartrecruiters', 'successfactors',
  'gem', 'oracle', 'atlassian', 'avature', 'aggregator']));
