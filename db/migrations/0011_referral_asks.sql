-- 0011_referral_asks.sql — every referral request, and what came of it.
--
-- docs/PLAN.md 5.4. 1,118 contacts imported, 0 referrals used, and no record
-- of any ask, so the funnel could not say whether asking works. The dashboard's
-- "Ask" button copies a drafted message for the operator to send themselves
-- (nothing is ever sent on their behalf -- PLAN non-negotiable 4) and logs the
-- ask here. Marking Applied "via referral" links the application to the most
-- recent ask for that job (applications.referral_contact_id).

create table if not exists referral_asks (
  id          bigserial primary key,
  contact_id  bigint not null references connections(id),
  job_id      bigint not null references jobs(id),
  asked_at    timestamptz not null default now(),
  replied_at  timestamptz,
  outcome     text check (outcome in ('referred', 'declined', 'no_reply')),
  unique (contact_id, job_id)
);
create index if not exists referral_asks_job_idx on referral_asks (job_id);

alter table referral_asks enable row level security;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on referral_asks from anon;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on referral_asks from authenticated;
  end if;
end $$;

comment on table referral_asks is
  'One row per (contact, job) referral request logged from the dashboard. '
  'See db/migrations/0011.';
