-- Synthetic rows for the backup self-test in .github/workflows/ci.yml.
-- Applied after db/migrations/*.sql to a throwaway database. One or more rows
-- in every table, with the foreign keys and the awkward column types (arrays,
-- jsonb, numeric, the 0006 check constraint) exercised, because those are what
-- a dump/restore round trip could plausibly mangle.

insert into companies (name, ats, board_token, priority)
values ('Acme', 'greenhouse', 'acme', 1),
       ('Initech', 'workday', 'initech|wd1|External', 3);

insert into jobs (company_id, ats_job_id, title, location, description,
                  absolute_url, posted_at, posted_at_precision, content_hash, raw)
values (1, 'g-1', 'Backend Engineer', 'Bengaluru, India', 'Build things — with unicode ✓',
        'https://example.invalid/1', '2026-10-15T08:30:00Z', 'timestamp', 'h1', '{"k": [1, 2]}'),
       (2, 'w-1', 'Platform Engineer', 'Delhi', 'Run things.',
        'https://example.invalid/2', '2026-10-15T00:00:00Z', 'date', 'h2', '{}'),
       (1, 'g-2', 'Undated Role', null, null,
        'https://example.invalid/3', null, null, 'h3', '{}');

insert into job_scores (job_id, fit_score, min_years, matched_skills, gap_skills,
                        reasoning, verdict, provider)
values (1, 8.5, 2, array['python', 'postgres'], array['go'], 'Good fit.', 'strong', 'google'),
       (2, 4.0, 6, array[]::text[], null, 'Too senior.', 'reject', 'cloudflare');

insert into connections (full_name, company_raw, company_norm, title, connected_on, profile_url)
values ('Test Person', 'Acme Inc.', 'acme', 'Engineer', '2024-01-02', 'https://example.invalid/p');

insert into applications (job_id, status, applied_at, hours_since_posted, via_referral,
                          referral_contact_id)
values (1, 'applied', '2026-10-15T10:00:00Z', 1.5, true, 1);

insert into notifications (job_id, channel, ok, error)
values (1, 'email', true, null),
       (2, 'email', false, 'synthetic failure');

insert into run_log (worker, finished_at, jobs_seen, jobs_new, jobs_closed, errors,
                     boards, trigger_source, duration_s)
values ('ingest', now(), 3, 3, 0, null, '[{"company": "Acme", "seen": 2}]', 'cronjob', 12.5);
