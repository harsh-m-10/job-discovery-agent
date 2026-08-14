# Job Discovery & Referral Agent

Polls company ATS boards directly, scores postings against one operator's
profile, and pings when something worth applying to appears within the hour.

Built to spec. Phase status:

| Phase | | |
|---|---|---|
| 0 | Board verifier | done — 37 boards live |
| 1 | Ingestion | done, verified live (close + reopen proven against the DB) |
| 2 | Scoring | done — prefilter + multi-provider LLM scoring with failover |
| 3 | Notification | done — CallMeBot + email fallback, needs CallMeBot creds |
| 4 | Dashboard | done — queue, detail, write-back, funnel, companies |
| 5 | Referral join | done — importer, normalization, matching (1,118 contacts) |
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

## LLM providers

Scoring runs against three vendors behind one interface
(`score/providers.py`), chosen by `config/settings.yaml:llm_providers` and tried
in `priority` order with automatic failover on 429 / 413 / 5xx:

| Priority | Provider | Model | Key | Notes |
|---|---|---|---|---|
| 1 | Google AI Studio | `gemini-2.0-flash` | `GOOGLE_API_KEY` | largest free allowance |
| 2 | Groq | `llama-3.3-70b-versatile` | `GROQ_API_KEY` | 12k TPM, 100k TPD |
| 3 | Cerebras | `llama-3.3-70b` | `CEREBRAS_API_KEY` | third fallback |

**Why multiple providers:** Groq's free tier is limited **per account, not per
key**, so any other project on the same account competes for the same
tokens-per-minute and tokens-per-day budget. Spreading load across independent
vendors is the only fix that does not involve paying, and it removes the single
point of failure that took scoring down repeatedly.

Every rate limit lives in `settings.yaml` — `rpm`, `tpm`, `batch_size`,
`max_chars` are per provider, because the binding constraint differs by an
order of magnitude between them. A fallback with a tighter ceiling re-chunks
automatically. `job_scores.provider` records which vendor scored each row.

> ### ⚠️ Google's free tier trains on your data
>
> Google AI Studio's **free** tier uses submitted prompts and responses to
> improve their products, and human reviewers may read them. The scoring prompt
> contains the full contents of `config/candidate_profile.yaml` — résumé,
> project descriptions, employer names and education.
>
> Compensation is stripped before the prompt is built and is asserted in
> `tests/test_llm.py`, so CTC and notice period never leave this machine. The
> rest of the résumé does.
>
> If that is not acceptable, either enable billing on the Google project (paid
> tiers are not used for training), or set `enabled: false` on the `google`
> entry in `settings.yaml` and let Groq take priority.

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
