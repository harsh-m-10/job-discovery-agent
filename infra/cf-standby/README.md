# cf-standby — second trigger for the ingest workflow

A Cloudflare Worker (free plan) that dispatches `.github/workflows/ingest.yml`
**only when the primary trigger has gone quiet** — when the newest ingest run
is more than 25 minutes old. While the primary (cron-job.org) is healthy it
makes one GET every 15 minutes and nothing else.

Every run it fires is tagged `source=cf-standby`, so `run_log.trigger_source`
shows when the primary is down even though the pipeline keeps running.

## Deploy (once, on a personal machine)

```sh
cd infra/cf-standby
npx wrangler@3 login                        # opens a browser
npx wrangler@3 secret put GH_DISPATCH_PAT   # paste the fine-grained token
npx wrangler@3 deploy
```

The token is the same one the cron-job.org job uses: fine-grained, scoped to
`harsh-m-10/job-discovery-agent`, **Actions: read and write**.

## Check it

```sh
npx wrangler@3 tail          # live logs; each tick prints "decision: ..."
npm test                     # unit tests, no network
```

On a healthy day the log reads `decision: skip (newest run is N min old)` every
15 minutes. A `dispatch` line means the primary missed at least one tick.

## Limits it lives inside

| Free-plan limit | Used |
|---|---|
| 5 cron triggers per account | 1 |
| 50 subrequests per invocation | at most 2 |
| 10 ms CPU per invocation | a few ms |

## Rotating the token

`npx wrangler@3 secret put GH_DISPATCH_PAT` again, then confirm with
`wrangler tail` that the next tick does not log `listing runs failed: HTTP 401`.
The watchdog warns 14 days before expiry (`notify/token_watch.py`).
