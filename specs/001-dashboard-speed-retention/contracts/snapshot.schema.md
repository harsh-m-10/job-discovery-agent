# Contract: dashboard_snapshot.data (jsonb)

Producer: `refresh_dashboard_snapshot()` (SQL). Consumers: dashboard layout, TODAY, Health.
Additive changes only; consumers must tolerate missing keys (render "—").

```json
{
  "version": 1,
  "last_run": {"started_at": "ISO", "finished_at": "ISO", "trigger": "cronjob", "errors": 0},
  "runs_24h": 96,
  "open_jobs": 9246,
  "unscored": 0,
  "stranded": 0,
  "queue": {"total": 120, "by_family": {"backend": 40, "genai": 30}},
  "new_24h": {"total": 18, "by_family": {"backend": 7}},
  "week": {"applied": 23, "responses": 4, "referral_asks": 0},
  "applied_this_week": 23,
  "providers_24h": [{"provider": "google", "count": 80}],
  "inactive_boards": ["Postman"],
  "health": {"level": "green|amber|red", "reasons": ["plain sentence"]},
  "in_progress": 3,
  "ops": {"canary": {"at": "ISO", "ok": true}, "backup": {"at": "ISO", "ok": true}}
}
```

v2 (0029, spec 002) adds `in_progress`, `ops` and `new_24h.by_source`; health turns
amber on a failed canary or a failed or overdue (30 h) backup.

Health level rules (SQL):
- red: no finished ingest run in 45 min, or stranded > 0.
- amber: unscored > 0 for the latest run, a board error in 3+ consecutive runs, or fewer
  than 2 scoring providers succeeded in 24 h.
- green otherwise.

# Contract: GET /api/perf?page=<queue|funnel|contacts|review|companies|job&id=>

Auth: same secret as the dashboard (proxy.ts). Response: `Server-Timing:
q0;desc="snapshot";dur=12.3, …, total;dur=40.1` and JSON
`{"page":"queue","total_ms":40.1,"queries":[{"name":"…","ms":12.3,"pages":1,"rows":1}]}`.
