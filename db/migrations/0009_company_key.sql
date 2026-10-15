-- 0009_company_key.sql — one stored identity key per company (Phase 5.1).
--
-- common/company_key.py computes it. Ingest fills it for any company that
-- lacks one at the start of every run, so newly seeded boards get a key without
-- a separate step; scripts/backfill_role_family.py-style one-offs are not
-- needed. Contacts already carry the same key as connections.company_norm.
--
-- Read by: the dashboard's referral join (replacing a TypeScript copy of the
-- normaliser) and outcome capture's sender/subject -> application matching.

alter table companies add column if not exists company_key text;
create index if not exists companies_company_key_idx on companies (company_key);

comment on column companies.company_key is
  'common/company_key.py applied to name. Joins to connections.company_norm. '
  'Filled by ingest when NULL. See db/migrations/0009.';
