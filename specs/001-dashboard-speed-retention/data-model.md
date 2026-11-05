# Data model: Dashboard speed and data retention

Migration `db/migrations/0026_snapshot_seen_retention.sql` (idempotent).

## New: dashboard_snapshot (one row)

| column | type | notes |
|---|---|---|
| id | smallint PK, check id = 1 | single row |
| computed_at | timestamptz | staleness shown on the dashboard |
| data | jsonb | see contracts/snapshot.schema.md |

Written only by `refresh_dashboard_snapshot()` (security definer, service_role only).

## New: seen_jobs

| column | type | notes |
|---|---|---|
| company_id | bigint | PK part; no FK (company rows are never deleted, but keep it loose) |
| ats_job_id | text | PK part |
| content_hash | text | fingerprint at removal |
| source | text | ats / indeed / alert-email |
| first_seen_at | timestamptz | from jobs |
| pinged_at | timestamptz null | carried from jobs.pinged_at |
| removed_at | timestamptz default now() | 180-day window counts from here |

Index: (removed_at) for expiry.

## New: daily_stats

| column | type |
|---|---|
| day | date, PK part |
| worker | text, PK part |
| runs, jobs_seen, jobs_new, jobs_closed, error_runs | int |
| duration_s_sum, duration_s_max | numeric |

Upserted for every finished UTC day still in run_log, then run_log rows over 90 days go.

## Changed: jobs

- `pinged_at timestamptz null`, backfilled as min(notifications.sent_at where ok).
- Trigger `jobs_skip_seen` BEFORE INSERT: return NULL when (company_id, ats_job_id) is in
  seen_jobs.

## New view: job_keys

`jobs` rows (id, company_id, ats_job_id, content_hash, closed_at, reopen_count,
seen_only=false) UNION ALL `seen_jobs` rows (null id …, seen_only=true) for keys not in
jobs.

## Changed view: v_funnel

`first_ping_at` from `jobs.pinged_at` instead of notifications (survives the 30-day
notification window).

## Retention rules (prune_old_data_v2)

| data | rule |
|---|---|
| applications, application_events, referral_asks, warm tags | never deleted |
| jobs referenced by the above | never deleted; description/raw kept if applied |
| description, raw | null when first_seen > 30 d and no application |
| rejected / excluded, never pinged, unreferenced | delete when first_seen > 30 d |
| closed > 90 d, or unacted and first_seen > 90 d, unreferenced | delete |
| deleted jobs | identity → seen_jobs first (same transaction) |
| seen_jobs | delete removed_at > 180 d |
| notifications | delete sent_at > 30 d |
| run_log | roll into daily_stats; delete started_at > 90 d |
| trigger_log | delete fired_at > 30 d |
| alert_inbox | delete processed_at > 30 d |

State: a posting goes open → (closed) → stripped → deleted → seen record → expired.
