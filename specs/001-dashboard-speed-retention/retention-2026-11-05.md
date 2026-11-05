
## 17:51 UTC

```
retention (dry run: nothing changed)
  windows: {"descriptions_days": 30, "rejected_days": 30, "stale_days": 90, "notifications_days": 30, "run_log_days": 90, "seen_days": 180, "trigger_log_days": 30, "alert_inbox_days": 30}
  result:  {"dry_run": true, "jobs_deleted": 1794, "seen_recorded": 1794, "run_log_deleted": 0, "seen_jobs_expired": 0, "jobs_deleted_stale": 0, "alert_inbox_deleted": 0, "trigger_log_deleted": 0, "daily_stats_upserted": 105, "descriptions_stripped": 86, "jobs_deleted_rejected": 1794, "notifications_deleted": 148}
  database: 87.4 MB -> 87.8 MB (space is reused by Postgres; the file shrinks only after VACUUM FULL)
  tables (rows before -> after, size after):
    jobs                     11525 ->   11525  57.3 MB
    run_log                   3408 ->    3408  4.7 MB
    notifications              848 ->     848  4.6 MB
    alert_inbox                190 ->     190  4.4 MB
    job_scores               11525 ->   11525  2.4 MB
    connections               1118 ->    1118  0.8 MB
    trigger_log               4095 ->    4095  0.6 MB
    companies                 1287 ->    1287  0.4 MB
    application_events         158 ->     158  0.3 MB
    applications               325 ->     325  0.1 MB
    referral_asks                0 ->       0  0.1 MB
    provider_usage              29 ->      29  0.1 MB
    dashboard_snapshot           1 ->       1  0.0 MB
    seen_jobs                    0 ->       0  0.4 MB
```
