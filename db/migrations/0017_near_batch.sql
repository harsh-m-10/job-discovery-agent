-- 0017_near_batch.sql -- a second warmth tier for referral ranking (PLAN 5.3).
--
-- Decided 2026-10-15: connections made in the RNSIT batch years (2021-2025)
-- are batchmates; one year either side (2020, 2026) is near-batch. The window
-- lives in config/settings.yaml:referral_warmth and is applied at import and
-- by scripts/flag_batchmates.py; this column only stores the result.

alter table connections
  add column if not exists near_batch boolean not null default false;

comment on column connections.near_batch is
  'Connected within one year of the batch window; ranks after batchmates. '
  'Set from config/settings.yaml:referral_warmth. See 0017.';
