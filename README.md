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
config/     candidate_profile.example.yaml (template; the real profile lives
            outside this repo) and settings.yaml (thresholds)
seeds/      companies.yaml (hand-maintained) -> companies.verified.yaml (generated)
tests/
```

## The operator profile

The candidate profile is the single source of truth for both scoring and
screening answers, and it is **not in this repository**. It carries
compensation, employer, education and domain-preference facts; this repo is
public, because private repos bill Actions against a 2,000 min/month allowance
that a 15-minute schedule exhausts in nine days.

```bash
mkdir -p ~/.job-agent/private
cp config/candidate_profile.example.yaml ~/.job-agent/private/candidate_profile.yaml
$EDITOR ~/.job-agent/private/candidate_profile.yaml
```

`score/llm.py:profile_path()` resolves, in order:

| # | location | used by |
|---|---|---|
| 1 | `$CANDIDATE_PROFILE_PATH` | CI |
| 2 | `~/.job-agent/private/candidate_profile.yaml` | local runs |
| 3 | `config/candidate_profile.yaml` | gitignored local convenience |

If none exists it raises `ProfileMissing` rather than falling back. A silent
fallback would score every posting against no résumé facts at all and quietly
produce meaningless numbers.

For CI, add the file as a repository secret named `CANDIDATE_PROFILE_B64`:

```bash
base64 -w0 ~/.job-agent/private/candidate_profile.yaml
```

The workflow decodes it into the runner temp directory — outside the working
tree, so no later step can commit it — and logs only the byte count.

Its `scoring_rules` block is injected verbatim into the prompt: the only lever
controlling score inflation, so tune calibration there, not in code. Its
`compensation` block is stripped before the prompt is built and never reaches a
model. `tests/test_llm.py` asserts that, reading the expected values out of the
profile rather than hardcoding them — writing the real CTC into a public test to
prove it never leaks would itself leak it.

The dashboard renders those compensation values in its screening panel and
cannot read the profile at runtime, so they travel as an environment variable:

```bash
python scripts/sync_screening.py            # prints the one-line JSON
vercel env add SCREENING_JSON production    # paste it
```

## LLM providers

Scoring runs against three vendors behind one interface
(`score/providers.py`), chosen by `config/settings.yaml:llm_providers` and tried
in `priority` order with automatic failover on 429 / 413 / 5xx:

| # | Provider | Model | Key | Measured on a real 8,040-token payload |
|---|---|---|---|---|
| 1 | Google AI Studio | `gemini-flash-lite-latest` | `GOOGLE_API_KEY` | **3.7s** |
| 2 | Mistral | `mistral-small-latest` | `MISTRAL_API_KEY` | **4.5s** |
| 3 | Google AI Studio | `gemini-3-flash-preview` | `GOOGLE_API_KEY` | 10.7s |
| 4 | Cloudflare Workers AI | `@cf/openai/gpt-oss-120b` | `CLOUDFLARE_API_KEY` + `CLOUDFLARE_ACCOUNT_ID` | 13.3s |
| 5 | OpenRouter | `nvidia/nemotron-3-super-120b-a12b:free` | `OPENROUTER_API_KEY` | 15.0s |
| 6 | NVIDIA NIM | `openai/gpt-oss-120b` | `NVIDIA_API_KEY` | 20.3s |
| 7 | Groq | `llama-3.3-70b-versatile` | `GROQ_API_KEY` | disabled — account shared |
| 8 | Cerebras | `gpt-oss-120b` | `CEREBRAS_API_KEY` | 402 payment required |

Rejected during that test, and why — every one of these looked fine on a toy
request:

| candidate | outcome |
|---|---|
| GitHub Models (any model) | HTTP 410 `github_models_retirement_brownout` — **being retired** |
| `openrouter/google/gemma-4-31b:free` | 429 from the upstream provider |
| `openrouter/nvidia/nemotron-3-nano-30b` | 200 but unparseable JSON |
| `openrouter/openai/gpt-oss-20b` | worked, but **102s** — too slow to sit in the chain |
| `nvidia/meta/llama-3.3-70b-instruct` | read timeout at 150s |
| `mistral/open-mistral-nemo` | 200 but unparseable JSON |

Model choice was measured, not assumed. On a real scoring payload
`gemini-flash-latest` read-timed-out at 76s with persistent 503s, and
`gemini-2.0-flash` / `gemini-2.5-flash` return 404 "no longer available" on this
key. `gemini-flash-lite-latest` answers in ~2.6s and is the only one worth
leading with.

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
> contains the full contents of the candidate profile — résumé, project
> descriptions, employer names and education.
>
> Compensation is stripped before the prompt is built and is asserted in
> `tests/test_llm.py`, so CTC and notice period never leave this machine. The
> rest of the résumé does.
>
> If that is not acceptable, either enable billing on the Google project (paid
> tiers are not used for training), or set `enabled: false` on the `google`
> entry in `settings.yaml` and let Groq take priority.

### Model names rot — check before blaming the code

Provider model ids are **not stable**, and they disappear without a deprecation
window. Already observed on this project's own key:

| model | what happened |
|---|---|
| `gemini-2.0-flash` | 404 "no longer available" |
| `gemini-2.5-flash`, `gemini-2.5-flash-lite` | 404 "no longer available" |
| `gemini-flash-latest` | read timeout at 76s, persistent 503 |
| `gemini-3-flash-preview` | **preview** — will vanish without notice |
| `llama-3.3-70b` (Cerebras) | never existed on that account |

`python -m score.healthcheck` pings every enabled provider and cross-checks the
configured model against the vendor's own model list, logging an ERROR when a
model is no longer listed. It runs daily as its own workflow
(`.github/workflows/healthcheck.yml`) and fails fast if nothing is usable.

It used to run on every 15-minute ingest tick, where it cost 167s of a ~395s
run — 42% of the entire Actions budget spent asking eight vendors whether their
models still existed, a question that changes on a scale of weeks.

When scoring suddenly stops, run the healthcheck **first** — a rotted model id
looks exactly like a broken integration.

```bash
python -m score.healthcheck      # which providers and models are live
python -m notify.watchdog --dry-run   # is the pipeline actually flowing
python -m notify.watchdog --test      # prove the alert email path works
```

## Polling cadence — measured, not configured

`ingest.yml` declares `cron: "*/15 * * * *"`. **That is not what happens.**
GitHub deprioritises scheduled workflows on free plans regardless of visibility.
Gaps measured on this repo over one evening:

```
18:40 -> 19:44   64 min      21:21 -> 22:10   49 min
19:44 -> 20:23   39 min      22:10 -> 23:03   52 min
20:23 -> 21:21   57 min      23:03 -> 23:58   54 min
                             23:58 -> 02:35  156 min
```

**Measured polling cadence: ~53 minutes average, worst observed 2h36m.**

This matters because the latency edge is one of the three things the system
exists to buy. ~53 minutes still beats aggregators that refresh daily, so the
edge is real — it is just smaller than "*/15" implies. Do not quote 15 minutes
anywhere.

**Documented fix, deliberately not implemented:** point an external pinger
(cron-job.org, or a Vercel cron) at the GitHub `workflow_dispatch` API on a
real 15-minute schedule. Manual dispatches are not deprioritised the way
`schedule` events are. This is worth doing **only if** the funnel's apply-latency
breakdown eventually shows that speed converts — until there is application
data, it is optimisation without evidence.

### Why this repository is public

Private repositories bill Actions against a **2,000 min/month** allowance. This
schedule cost ~4.7 billed minutes per tick and burned the whole month in nine
days, after which every run failed in three seconds with no steps and no logs:

> The job was not started because recent account payments have failed or your
> spending limit needs to be increased.

That failure mode is worth recognising on sight: **a run that dies in seconds
with an empty step list is a billing problem, not a code problem.** No amount of
reading application logs will explain it, because no runner was ever allocated.

Public repositories get unmetered Actions minutes. The fix was to make this repo
public and move the one file carrying PII out of it — see
[The operator profile](#the-operator-profile) — rather than to slow the poll
down. Splitting the provider healthcheck into its own daily workflow cut another
42% off every run.

## Alerting

`notify/watchdog.py` runs after every ingestion and emails on three conditions,
each of which otherwise produces **no symptom except an empty queue**:

| condition | cooldown |
|---|---|
| the last ingestion run recorded board errors or deactivated a board | 6h |
| no posting seen for the first time in 72h | 24h |
| jobs stranded in `scoring_failed` because every provider refused | 6h |

Alerts deduplicate through the `notifications` table using synthetic negative
job ids, so a recurring failure emails once per cooldown rather than every
fifteen minutes. **It needs an email transport**: set `RESEND_API_KEY` +
`ALERT_EMAIL_TO`, or `SMTP_USER` + `SMTP_PASS` + `ALERT_EMAIL_TO`. Without one,
alerts are logged as ERROR but never delivered — verify with
`python -m notify.watchdog --test`.

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
