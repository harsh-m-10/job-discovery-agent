# Research: Dashboard speed and data retention

All findings measured on 2026-11-05 against the live system unless stated.

## R1. Where the ~20 s probably goes (to be confirmed by the T001 baseline)

- **Decision**: Treat the baseline as the authority. The code reading below is a hypothesis.
- **Findings from code**:
  - `app/d/[secret]/layout.tsx` calls `getQueueMeta()`, and the queue page calls it a
    second time alongside `getQueue()` and `getStatus()`.
  - `getQueueMeta()` reads every open job (9,246 rows), every score (11,507) and every
    application. `lib/db.ts:select` pages at 1,000 rows and walks the pages
    sequentially, so that is about 10 + 12 + 1 round trips, twice per page view.
  - The functions most likely run in Vercel's default region, iad1 (Washington). Supabase
    is in ap-south-1 (Mumbai), about 200 ms RTT apart, so each round trip costs at
    least 0.2 s plus transfer. That gives roughly 50 round trips × 0.3 s ≈ 15–20 s, which
    matches the report.
  - `getFunnel()` reads `v_funnel`, all scores and all open job ids. `getContacts()` reads
    all 1,118 connections.
- **Verification**: The `x-vercel-id` response header names the function region (last
  segment before the id). This is checked in T001.

## R2. Timing instrumentation

- **Decision**: A per-request collector in `lib/db.ts`, using AsyncLocalStorage keyed by
  page. It records table, query shape, pages fetched, rows and ms, logs one line per page
  render (`[timing] page=/ total=… q1=…`), and exposes the result through
  `GET /api/perf?page=<name>`. That route runs the page's loaders and returns a
  `Server-Timing` header plus JSON.
- **Rationale**: Server components cannot set response headers in Next 16, so the
  header lives on a route that runs the same loaders. The log covers real page views.
- **Alternatives**: OpenTelemetry/Vercel Observability (overkill, and it hides
  per-query detail); an HTML comment in the page (not a header, and awkward to parse).

## R3. Region

- **Decision**: Add `dashboard/vercel.json` with `{"regions": ["bom1"]}`.
- **Rationale**: Hobby allows one function region, and bom1 (Mumbai) sits next to
  Supabase ap-south-1. The setting is versioned in git.
- **Alternatives**: Changing the project setting in the UI (not versioned); edge runtime
  (no gain, because the latency is to the DB, not to the user).

## R4. Precomputed snapshot

- **Decision**: Add a table `dashboard_snapshot` (a single row, id = 1) and a SQL function
  `refresh_dashboard_snapshot()`. The function computes every headline number in one
  statement and upserts it as jsonb with `computed_at`. It is called by the last ingest
  step via PostgREST RPC, and by the nightly retention job.
- **Rationale**: The aggregates run inside Postgres in milliseconds, next to the data.
  The dashboard reads one small row instead of about 25 pages.
- **Alternatives**: A materialised view (needs a refresh call anyway, and has no
  single-row semantics); computing in Python (ships rows over the wire, which costs
  egress).

## R5. Never ping twice after deletion

- **Finding**: `notify/run.py` de-duplicates by reading every `notifications` job_id.
  Deleting notifications after 30 days would re-ping jobs still open. `v_funnel` derives
  `first_ping_at` from notifications too.
- **Decision**: Add `jobs.pinged_at`, backfilled from notifications. notify filters on
  `pinged_at is null` and sets it after sending. `v_funnel.first_ping_at` reads
  `jobs.pinged_at`. Notifications become a 30-day delivery log.

## R6. Seen postings without per-run cost

- **Finding**: Ingest calls `existing_jobs(company_id)` once per board per run, and the
  aggregator calls `existing_for(company_ids)`. If a deleted posting that is still on its
  board looked "new", it would be re-sent every 15 minutes. With ~6,000 prefilter-rejected
  open postings, that would mean thousands of rows with descriptions per run.
- **Decision**: Add a table `seen_jobs(company_id, ats_job_id, content_hash, source,
  first_seen_at, pinged_at, removed_at)` with primary key (company_id, ats_job_id).
  - The two "existing" reads go through a view `job_keys`, which is jobs plus seen_jobs
    with a `seen_only` flag. Ingest skips `seen_only` keys entirely: no insert, no close,
    no score.
  - That is the same number of requests as today, and the payload shrinks because the
    deleted rows' columns are mostly gone.
  - A `BEFORE INSERT` trigger on jobs also drops any row whose key is in seen_jobs, as a
    safety net.
- **Alternatives**:
  - Load all seen keys once per run: about 30 MB/day of egress, which breaks the
    Principle III budget.
  - Strip but never delete open rejected postings: contradicts the operator's rule.

## R7. Retention v2 (replaces 0025's 15-day rule)

- **Decision**: Add `prune_old_data_v2(...)` with these windows: descriptions 30 d,
  rejected/excluded 30 d, closed or unacted 90 d, notifications 30 d, run_log 90 d after
  rolling into `daily_stats`, trigger_log 30 d, seen_jobs 180 d.
  - Every deletion of jobs first inserts their identity into seen_jobs, in one
    transaction.
  - Protected rows: applications, application_events, referral_asks, connections.warm,
    and jobs referenced by any of them.
  - It has a report-only mode (`p_dry_run`) returning the same counts without changes.
  - backup.yml already runs retention only after the backup step succeeds.
- **"Unacted"** means no applications row and no referral_asks row.
- **"Excluded"** means `job_scores.verdict = 'reject'` or `location_ok = false`, and never
  pinged.

## R8. Indexes

Existing indexes: jobs(closed_at) partial on open, jobs(company_id, ats_job_id),
jobs(first_seen_at desc), job_scores(job_id), applications(job_id),
connections(company_norm), companies(company_key).

To add only after EXPLAIN ANALYZE shows a sequential scan on a hot path:
- job_scores(location_ok, fit_score desc): the queue.
- applications(status, updated_at desc): pipeline.
- notifications(job_id) where ok: still used by the weekly report.
- jobs(pinged_at): retention and notify.

job_scores showed 96,580 sequential scans against 80,049 index scans, which is the
primary suspect. The user's names map as follows: is_open → closed_at is null, city →
job_scores.location_ok, final_score → fit_score.

## R9. Budgets check

- `next build` prints First Load JS per route, which is the < 200 KB gate.
- First load on 4G is measured with Chrome's "Fast 4G" throttling via Claude in Chrome,
  or with curl TTFB plus payload size as a proxy.
- No chart library is in package.json today.
