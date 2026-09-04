-- 0003_enable_rls.sql — close the public read/write hole on every table.
--
-- Supabase's security advisor flagged `rls_disabled_in_public` on 2026-08-31.
-- Verified 2026-09-04 with the project's own anon key: all seven tables were
-- readable, and a no-match PATCH and DELETE against run_log both returned 204,
-- so writes were open too. Anyone holding the project URL and the anon key —
-- which is a public credential by design, safe to ship to browsers precisely
-- because RLS is expected to be on — could read, edit, or drop every row.
--
-- `connections` made this worse than a scraped-jobs leak: it holds the
-- operator's LinkedIn export, so real third parties' names, titles and
-- employers were exposed alongside their own data.
--
-- Enabling RLS with NO policies denies anon and authenticated outright. That is
-- the intended end state here, not an oversight:
--
--   * ingest/, score/ and notify/ all authenticate with SUPABASE_SERVICE_KEY.
--   * dashboard/lib/db.ts is `server-only` and also uses the service key.
--   * SUPABASE_ANON_KEY appears nowhere in the codebase — grepped across .py,
--     .ts, .tsx, .yaml and .json before writing this.
--
-- The service role bypasses RLS entirely, so every legitimate caller is
-- unaffected. Should a browser-side reader ever be wanted, add a narrow
-- SELECT policy per table then — do not disable RLS again.

-- ENABLE only, deliberately not FORCE. FORCE would subject the table owner to
-- RLS as well, which buys nothing against the anon key — PostgREST never
-- connects as the owner — while risking a lockout of migration and admin
-- tooling that legitimately connects as `postgres`.

alter table companies     enable row level security;
alter table jobs          enable row level security;
alter table job_scores    enable row level security;
alter table connections   enable row level security;
alter table applications  enable row level security;
alter table notifications enable row level security;
alter table run_log       enable row level security;
