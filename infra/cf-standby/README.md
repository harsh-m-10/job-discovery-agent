# cf-standby -- second dispatcher for the ingest workflow

A Cloudflare Worker (free plan) that dispatches `.github/workflows/ingest.yml`
every 15 minutes at **:07/:22/:37/:52, unconditionally**, tagged
`source=cf`. cron-job.org fires at :00/:15/:30/:45; while it is healthy this
Worker's runs fall inside the 8-minute run lease and collapse (recorded in
`trigger_log` as `lease`). When cron-job.org dies the pipeline keeps a
15-minute cadence with no detection delay.

(The directory keeps its old name; until 2026-10-15 the Worker only dispatched
when the newest run was stale. That role now belongs to the Apps Script.)

## Deploy (once, on a personal machine)

```sh
cd infra/cf-standby
npx wrangler@3 login                        # opens a browser
npx wrangler@3 secret put GH_DISPATCH_PAT   # the token the other triggers use
npx wrangler@3 deploy
```

## Check it

```sh
npx wrangler@3 tail          # live logs; each tick prints "dispatch: HTTP 204"
npm test                     # unit tests, no network
```

`dispatch: HTTP 401 (token rejected)` means the token is dead (RUNBOOK 6).

## Limits it lives inside

| Free-plan limit | Used |
|---|---|
| 5 cron triggers per account | 1 |
| 50 subrequests per invocation | 1 |
| 10 ms CPU per invocation | a few ms |
