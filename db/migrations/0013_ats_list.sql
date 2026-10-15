-- 0013_ats_list.sql — companies.ats matches the adapters that exist.
--
-- 0012 anticipated adapters for Phenom (HPE's careers site) and Oracle
-- Recruiting Cloud. Neither was needed: HPE's Phenom site fronts a Workday
-- tenant (hpe|wd5|Jobsathpe), and Oracle's public site carries almost no India
-- postings. SuccessFactors (Standard Chartered) was needed instead. No row uses
-- 'phenom' or 'oracle'.

alter table companies drop constraint if exists companies_ats_check;
alter table companies add constraint companies_ats_check
  check (ats in ('greenhouse', 'lever', 'ashby', 'workday',
                 'smartrecruiters', 'successfactors'));
