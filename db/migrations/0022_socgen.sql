-- 0022_socgen.sql -- Societe Generale careers (Sinequa search), 2026-10-16.
-- ingest/adapters/socgen.py; CSRF token from the page, then a bearer token, then the search.

alter table companies drop constraint if exists companies_ats_check;
alter table companies add constraint companies_ats_check check (ats = any (array[
  'greenhouse', 'lever', 'ashby', 'workday', 'smartrecruiters', 'successfactors',
  'gem', 'oracle', 'atlassian', 'avature', 'socgen', 'aggregator']));
