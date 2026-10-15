-- 0020_more_ats.sql -- three careers sites resolved by recording their requests
-- (2026-10-16): Gem (PromptQL, formerly Hasura), Oracle Recruiting Cloud, and
-- Atlassian's own listing endpoint in front of iCIMS. ingest/adapters/.

alter table companies drop constraint if exists companies_ats_check;
alter table companies add constraint companies_ats_check check (ats = any (array[
  'greenhouse', 'lever', 'ashby', 'workday', 'smartrecruiters', 'successfactors',
  'gem', 'oracle', 'atlassian', 'aggregator']));
