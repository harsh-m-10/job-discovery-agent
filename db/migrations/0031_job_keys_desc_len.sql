-- 0031_job_keys_desc_len.sql -- database egress (2026-11-06).
--
-- ingest/aggregate.py compares an Indeed or alert posting's description with
-- the stored one only by length, but read the stored text to do it: every
-- stored description at every company in the batch, megabytes per run. The
-- view now carries the length; the text is fetched by id only when needed.
-- Same columns and order as 0026, desc_len appended.

create or replace view job_keys as
  select j.id, j.company_id, j.ats_job_id, j.content_hash, j.closed_at, j.reopen_count,
         j.last_seen_at, j.description, j.source, false as seen_only,
         length(j.description) as desc_len
    from jobs j
  union all
  select null::bigint, s.company_id, s.ats_job_id, s.content_hash, null::timestamptz,
         0, null::timestamptz, null::text, s.source, true, null::integer
    from seen_jobs s
   where not exists (select 1 from jobs j
                      where j.company_id = s.company_id and j.ats_job_id = s.ats_job_id);

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on job_keys from anon;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on job_keys from authenticated;
  end if;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant select on job_keys to service_role;
  end if;
end $$;
