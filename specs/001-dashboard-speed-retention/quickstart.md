# Quickstart: validate feature 001

Run from the repo root. `$DASH` is the dashboard base URL including the secret path,
which comes from `.env`. It is never pasted anywhere.

1. **Timing (FR-001/002).** Run `python scripts/perf_report.py --out specs/001-dashboard-speed-retention/perf-<label>.md`.
   It times each page by TTFB, total and size, reads `/api/perf` for the per-query
   breakdown, and records the function region from `x-vercel-id`.
   - Expected after: every page TTFB under 600 ms from India, region bom1, no query over
     150 ms.
2. **Snapshot (FR-004).** Run `select computed_at, data->'health' from dashboard_snapshot`.
   `computed_at` is within 15 min of the last ingest run.
3. **Retention report-only (FR-013).** Run `python scripts/prune.py --dry-run`. It prints
   the size and row counts and what would change.
4. **Retention for real.** Run `python scripts/prune.py`. The before/after sizes are
   printed and appended to `specs/001-dashboard-speed-retention/retention-<date>.md`.
   - Counts of applications, application_events, referral_asks and warm contacts are
     identical before and after (SC-006).
5. **No re-ping (SC-005).** Run `python tests/test_seen_jobs.py`. It runs against a
   throwaway Postgres, as CI does for the backup round-trip: a deleted posting replayed
   through ingest is neither inserted nor pinged.
6. **Budgets (FR-014).** In `cd dashboard && npm run build`, First Load JS is under
   200 KB on every route. A phone or Fast-4G throttled load of `$DASH` renders in under
   1.5 s.
