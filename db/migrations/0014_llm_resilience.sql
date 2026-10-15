-- 0014_llm_resilience.sql — provisional scores, and a ledger of provider use.
--
-- docs/PLAN.md Phase 7 (gap G9). Until now an LLM outage did two silent things:
-- jobs past the point of failure were marked scoring_failed and no ordinary run
-- ever retried them, and if every provider failed the healthcheck the scoring
-- step exited non-zero, which in the workflow skipped Notify too -- so an outage
-- also stopped pings for jobs scored earlier. Measured 2026-10-15: one provider
-- (google) had scored every LLM job in the previous 7 days.
--
-- job_scores.provisional: true when the score came from score/rules.py, the
-- deterministic fallback, because no LLM was available. Such rows flow to the
-- queue and pings (tagged provisional) and are re-scored by the next run that
-- has a working provider.
--
-- provider_usage: one row per provider per day, written at the end of every
-- scoring run. Also carries cooldown_until -- the circuit breaker: a provider
-- that reports its quota exhausted is not called again until then, by this run
-- or the next ones.

alter table job_scores
  add column if not exists provisional boolean not null default false;

create table if not exists provider_usage (
  provider          text not null,
  day               date not null,
  requests          int  not null default 0,
  successes         int  not null default 0,
  rate_limited      int  not null default 0,
  quota_exhausted   int  not null default 0,
  errors            int  not null default 0,
  jobs_scored       int  not null default 0,
  last_success_at   timestamptz,
  last_error        text,
  cooldown_until    timestamptz,
  primary key (provider, day)
);

alter table provider_usage enable row level security;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on provider_usage from anon;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on provider_usage from authenticated;
  end if;
end $$;

comment on table provider_usage is
  'Per provider per day: calls, outcomes, and a circuit-breaker cooldown. '
  'Written by score/run.py. See db/migrations/0014.';
