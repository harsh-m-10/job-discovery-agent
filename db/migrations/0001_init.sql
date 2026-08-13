-- 0001_init.sql — full schema (spec §5).
-- Phase 0 needs `companies`; the rest lands here so the DB is created once.

create table if not exists companies (
  id                   bigserial primary key,
  name                 text not null,
  ats                  text not null check (ats in ('greenhouse','lever','ashby','workday')),
  board_token          text not null,
  careers_url          text,
  category             text,
  priority             int  not null default 2,
  active               boolean not null default true,
  last_ok_at           timestamptz,
  consecutive_failures int not null default 0,
  unique (ats, board_token)
);

create table if not exists jobs (
  id             bigserial primary key,
  company_id     bigint not null references companies(id),
  ats_job_id     text not null,
  title          text not null,
  location       text,
  description    text,
  absolute_url   text not null,
  compensation   text,               -- Ashby exposes this reliably; spec §6.1 wants it in the ping
  posted_at      timestamptz,
  first_seen_at  timestamptz not null default now(),
  last_seen_at   timestamptz not null default now(),
  closed_at      timestamptz,
  reopen_count   int not null default 0,
  content_hash   text not null,
  raw            jsonb,
  unique (company_id, ats_job_id)
);
create index if not exists jobs_first_seen_idx on jobs (first_seen_at desc);
create index if not exists jobs_open_idx on jobs (closed_at) where closed_at is null;

create table if not exists job_scores (
  job_id         bigint primary key references jobs(id) on delete cascade,
  fit_score      numeric(3,1),
  min_years      numeric(3,1),
  max_years      numeric(3,1),
  matched_skills text[],
  gap_skills     text[],
  reasoning      text,
  verdict        text check (verdict in ('strong','worth_trying','stretch','reject')),
  reject_reason  text,
  model          text,
  scored_at      timestamptz not null default now()
);

create table if not exists connections (
  id            bigserial primary key,
  full_name     text not null,
  company_raw   text,
  company_norm  text,
  title         text,
  connected_on  date,
  is_batchmate  boolean default false,
  profile_url   text
);
create index if not exists connections_company_norm_idx on connections (company_norm);

create table if not exists applications (
  id                  bigserial primary key,
  job_id              bigint not null unique references jobs(id),
  status              text not null default 'queued'
                        check (status in ('queued','applied','skipped','responded','interviewing','rejected','offer')),
  applied_at          timestamptz,
  hours_since_posted  numeric,
  via_referral        boolean default false,
  referral_contact_id bigint references connections(id),
  responded_at        timestamptz,
  notes               text,
  updated_at          timestamptz not null default now()
);

create table if not exists notifications (
  id         bigserial primary key,
  job_id     bigint references jobs(id),
  channel    text not null,
  sent_at    timestamptz not null default now(),
  ok         boolean not null,
  error      text
);

create table if not exists run_log (
  id            bigserial primary key,
  worker        text not null,
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  jobs_seen     int,
  jobs_new      int,
  jobs_closed   int,
  errors        jsonb
);
