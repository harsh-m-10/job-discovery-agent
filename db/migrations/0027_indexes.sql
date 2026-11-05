-- 0027_indexes.sql -- specs/001-dashboard-speed-retention FR-009.
--
-- Measured first (explain-before.md, 2026-11-05): every hot query ran in
-- under 15 ms; the sequential scans were on small tables. The dashboard's
-- 4.5-8.4 s was round trips, not query plans. These three cover the filters
-- that grow with the data and that feature 002's pipeline view sorts on:
--
--   job_scores (fit_score desc) where location_ok   queue, notify, snapshot
--   applications (status, updated_at desc)          pipeline by stage
--   notifications (job_id) where ok                 daily/weekly reports
--
-- The rest of the requested list already exists: jobs(company_id, ats_job_id),
-- jobs(first_seen_at desc), jobs(closed_at) where open, job_scores(job_id) PK,
-- applications(job_id), connections(company_norm), companies(company_key).

create index if not exists job_scores_bengaluru_fit_idx
  on job_scores (fit_score desc) where location_ok;

create index if not exists applications_status_updated_idx
  on applications (status, updated_at desc);

create index if not exists notifications_ok_job_idx
  on notifications (job_id) where ok;
