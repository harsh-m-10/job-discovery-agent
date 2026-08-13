# Job Discovery & Referral Agent

Polls company ATS boards directly, scores postings against one operator's
profile, and pings when something worth applying to appears within the hour.

Built to spec. Phase status:

| Phase | | |
|---|---|---|
| 0 | Board verifier | done — 37 boards live |
| 1 | Ingestion | done, verified live (close + reopen proven against the DB) |
| 2 | Scoring | done — prefilter + Groq batch scoring, calibrated at 18% scoring 8+ |
| 3 | Notification | done — CallMeBot + email fallback, needs CallMeBot creds |
| 4 | Dashboard | done — queue, detail, write-back, funnel, companies |
| 5 | Referral join | not started |
| 6 | Workday adapter | done — Cisco, Target, Adobe |

## Local setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # POSIX: .venv/bin/python
cp .env.example .env                                      # then fill it in
```

Nothing below needs credentials except where noted.

```bash
python scripts/verify_boards.py --seeds seeds/companies.yaml seeds/companies_batch2.yaml
python scripts/find_token.py --unresolved   # guess tokens the verifier failed on
python scripts/verify_lifecycle_live.py     # prove closed_at/reopen against the real DB
python -m ingest.run --dry-run         # fetch + normalize live boards, no DB
python -m score.prefilter --live       # stage-1 survival rate on live postings
python -m score.run                    # prefilter + Groq scoring of open jobs
python -m score.run --report           # re-print the scored table
python tests/test_lifecycle.py         # lifecycle + normalization tests
python tests/test_prefilter.py         # prefilter tests
python scripts/check_boundaries.py     # enforce the Layer 1 / Layer 2 split
```

`--live` on the prefilter is the calibration tool. Run it after touching any
pattern: synthetic examples always look fine, and real JDs are where
"10 years of category leadership" lives.

## Supabase setup (required before ingestion writes anything)

1. Create a free project at supabase.com — no card required.
2. SQL Editor → paste `db/migrations/0001_init.sql` → Run.
3. Settings → API → copy the Project URL and the **service_role** key.
4. Put both in `.env` locally, and add them as GitHub Actions repository secrets
   named `SUPABASE_URL` and `SUPABASE_SERVICE_KEY`.

The service-role key bypasses row-level security. It is server-side only: it
belongs in GitHub Actions secrets and Vercel env vars, never in the dashboard
bundle. All browser reads and writes go through `/api/*` route handlers.

Then:

```bash
python scripts/verify_boards.py --write-db   # populate `companies`
python -m ingest.run                         # first real ingestion run
```

## Repo layout

```
ingest/     LAYER 1 — shared, PII-free. Never imports from score/ or notify/,
            never touches connections / applications / job_scores.
score/      LAYER 2 — single-operator personalization (phase 2)
notify/     delivery (phase 3)
scripts/    verify_boards, find_token, check_boundaries
db/         SQL migrations
config/     candidate_profile.yaml (resume facts, calibration rules,
            compensation) and settings.yaml (thresholds)
seeds/      companies.yaml (hand-maintained) -> companies.verified.yaml (generated)
tests/
```

`config/candidate_profile.yaml` is the single source of truth for both scoring
and screening answers. Its `scoring_rules` block is injected verbatim into the
prompt — it is the only lever controlling score inflation, so tune calibration
there, not in code. Its `compensation` block is stripped out before the prompt
is built and never reaches a model; there is deliberately no second file holding
a CTC figure that could drift out of sync.

## Groq free tier

Tokens-per-minute is capped per model and counts requested output against the
limit: 12,000 on `llama-3.3-70b-versatile` (the default, chosen for exactly
this reason), 8,000 on `gpt-oss-120b` and `qwen3.6-27b`, 6,000 on
`llama-3.1-8b-instant`. The spec's batch of 8 at 6,000 chars needs ~15,000 and
is rejected outright, so `config/settings.yaml` ships a batch of 4 at 2,600
chars. The scorer paces itself from the rate-limit headers and halves a batch
that still comes back too large, so a run completes unattended — it just takes
about 30s per batch once the budget is saturated. Raise both settings if the
key is ever upgraded.

## Dashboard

```bash
cd dashboard && npm install
cp .env.local.example .env.local     # fill in, then:
npm run dev                          # http://localhost:3000/d/<DASHBOARD_SECRET>
```

Everything lives under `/d/<32-char secret>`; every other path returns 404, and
API routes authenticate on an `x-dashboard-secret` header so the secret never
appears in a server access log. The service-role key is server-side only —
`lib/db.ts` imports `server-only`, so an accidental client import fails the
build rather than leaking the key.

Re-run `python scripts/sync_screening.py` after changing any compensation value
in `candidate_profile.yaml`: the dashboard deploys from `dashboard/` and cannot
read `../config` at runtime, so those values are materialised into
`dashboard/lib/screening.generated.json`.

## Workday boards

Workday tokens are compound — `tenant|wdHost|site` — and all three parts are
per-tenant and published nowhere. `scripts/find_workday.py` brute-forces them.
The status codes are the opposite of the intuitive reading, and getting them
backwards makes the search silently return nothing:

| response | meaning |
|---|---|
| `404` | tenant is real, **site name** is wrong |
| `422` | wrong wd host — a nonsense tenant returns 422 on *every* host |
| `200` + JSON | correct |

A browser User-Agent is mandatory; the bot filter rejects anything else. Boards
are also huge (Target lists 2,000 postings), so the adapter reads the tenant's
own country facet out of the first response and applies it server-side, then
fetches descriptions only for postings that survive the location filter.

## Board inventory

`seeds/companies.yaml` is hand-maintained and unverified by definition — tokens
are published nowhere and change when a company switches ATS. The verifier is
the source of truth; `seeds/companies.verified.yaml` is generated, not edited.

To add a company: open its careers page, click Apply on any role, and read the
token out of the URL (`job-boards.greenhouse.io/<token>`, `jobs.lever.co/<token>`,
`jobs.ashbyhq.com/<token>`). If the listings are embedded inline, find the XHR to
the ATS API in DevTools → Network. Add it to `seeds/companies.yaml`, rerun the
verifier. About 90 seconds each; `scripts/find_token.py "<Company>"` often skips
the manual step.

## Constraints this codebase is built around

- No scraping of LinkedIn, Naukri, Indeed, or Glassdoor. `check_boundaries.py`
  fails the build if any of them appears in a URL.
- No automated application submission or messaging, anywhere. The human presses
  the final button.
- Layer 1 stays publishable as a PII-free public job board. Enforced by
  `check_boundaries.py`, which runs in CI.
