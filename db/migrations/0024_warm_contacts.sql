-- 0024_warm_contacts.sql -- a contact Harsh marks warm himself (2026-10-16).
--
-- Replaces the connection-year batchmate flag as the first referral-ranking
-- signal: 84% of contacts fell inside the batch years, so the year said
-- little about who would actually answer. The year stays, as a weak
-- tiebreaker only. Set from the dashboard, one click per contact
-- (/api/contact-warm); scripts/import_connections.py carries it across a
-- re-import, which otherwise deletes and re-inserts every row.

alter table connections
  add column if not exists warm boolean not null default false,
  add column if not exists warm_set_at timestamptz;

comment on column connections.warm is
  'Marked warm by Harsh in the dashboard: ranks first for referrals. See 0024.';
