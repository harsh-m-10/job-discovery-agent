-- 0012_more_ats.sql — allow the Phase 6 adapters in companies.ats.
--
-- 0001 restricted ats to the four original adapters. Phase 6 adds
-- SmartRecruiters (6.1) and, measured feasible on 2026-10-15, Phenom (HPE's
-- careers site) and Oracle Recruiting Cloud (Oracle's), which is where a large
-- share of the operator's referral network works. The list is widened once
-- here for all three rather than per adapter.

alter table companies drop constraint if exists companies_ats_check;
alter table companies add constraint companies_ats_check
  check (ats in ('greenhouse', 'lever', 'ashby', 'workday',
                 'smartrecruiters', 'phenom', 'oracle'));
