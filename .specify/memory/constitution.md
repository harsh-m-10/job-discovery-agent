<!--
Sync Impact Report
- Version change: template → 1.0.0 (initial ratification)
- Principles added: I. Operator Decides, II. Sources by Consent, III. Free Tier by Design,
  IV. Nothing Fails Silently, V. Measure Before Changing, VI. Public Repo, Private Data,
  VII. Answers, Not Internals
- Sections added: Operating Constraints, Development Workflow, Governance
- Templates: plan/spec/tasks templates read this file at runtime; no template edits needed
- Deferred TODOs: none
-->
# Job Discovery Agent Constitution

## Core Principles

### I. Operator Decides
The agent finds, scores and notifies; Harsh applies. Nothing submits an application,
form or message on his behalf, in any channel. Features MAY prepare material (drafts,
links, copyable answers); a human action MUST be the step that sends it.
Rationale: an unwanted application to a target company cannot be taken back.

### II. Sources by Consent
Postings come only from public ATS APIs, public careers sites, python-jobspy (Indeed)
and job-alert emails the operator subscribed to. LinkedIn, Naukri and Glassdoor MUST NOT
be scraped or logged into by automation; `scripts/check_boundaries.py` enforces the
banned hosts in CI.
Rationale: account bans and terms violations cost more than any posting is worth.

### III. Free Tier by Design
Everything runs on free tiers: GitHub Actions (public repo), Supabase (500 MB database,
5 GB egress), Vercel Hobby, Cloudflare Workers free, cron-job.org, free LLM tiers. A
design that only works on a paid plan is rejected. Every feature states its cost against
these limits (rows, MB, requests, egress per day).
Rationale: the project must run indefinitely at zero cost.

### IV. Nothing Fails Silently
Every failure MUST become visible: a count in a table, an alert email, a red health dot,
or a failing canary. Empty results are distinguished from broken ones (an empty board, a
parser yielding 0, an LLM chain with only one live provider are alerts, not quiet states).
Rationale: the 2026-11-05 review found four failures that ran unnoticed for days to weeks.

### V. Measure Before Changing
Performance, reliability and retention changes begin with a measurement and end with the
same measurement repeated. Numbers before and after are recorded in the feature's spec
directory. Classifier and parser changes are re-run over all stored real inputs and the
diff is reported.
Rationale: guesses about bottlenecks were wrong before (egress, latency).

### VI. Public Repo, Private Data
The repository is public. Secrets, `.env`, `apps_script/_local_config.gs`, contact lists,
spreadsheets and the internal docs (`docs/DECISIONS.md`, `docs/RUNBOOK.md`,
`docs/PLAN.md`) MUST NOT be committed. Commits carry no AI/assistant attribution. Personal
data is never placed in URLs or query strings beyond the dashboard's secret path.
Rationale: everything pushed is world-readable and permanent.

### VII. Answers, Not Internals
Main screens answer questions Harsh would ask, in his words ("what's in progress", "what's
new for me", "is it running"). System internals (YAML keys, scripts, provider names,
table names) belong only on the Health page. Phone-first: readable at 375 px, light and
dark themes following the system setting with a manual override, one accent colour.
Rationale: the dashboard is opened from a phone notification, in seconds, not studied.

## Operating Constraints

- Location: Bengaluru (Bangalore, BLR, KA) and remote-India only, applied at Layer 2
  (`job_scores.location_ok`); Layer 1 stays India-wide.
- Notifications: one email per run plus one ntfy push per job; a job is never pinged
  twice, including after its row is deleted by retention.
- Dashboard budgets: first load < 1.5 s on 4G, navigation < 300 ms, client JS < 200 KB,
  no chart libraries (inline SVG only), no `count=exact` on large tables, independent
  queries run in parallel.
- Retention runs nightly after the encrypted backup. Applications, application_events,
  referral_asks and warm tags are never deleted.
- Database stays under 50% of the free-tier size; daily egress under 20% of the monthly
  quota divided by 30.

## Development Workflow

- Specs, plans and tasks live under `specs/` (Spec Kit). Each feature records its baseline
  and after measurements there.
- Tests are plain Python scripts (`python tests/test_x.py`) wired into `.github/workflows/ci.yml`;
  dashboard changes MUST pass `next build`. Every bug fix adds a test with the real input
  that broke.
- Database changes are numbered migrations in `db/migrations/`, idempotent, applied to
  the live database only after review, and backed by that night's backup.
- UI revamps start with a static mockup that Harsh approves before building.
- Work lands on `main` in small commits whose messages explain why.

## Governance

This constitution overrides conflicting habits and earlier docs. Amendments are made with
the `speckit-constitution` workflow, recorded in the Sync Impact Report, and versioned:
MAJOR for removed or redefined principles, MINOR for added principles or sections, PATCH
for wording. Every plan includes a Constitution Check against Principles I–VII; a
violation must be justified in the plan's Complexity Tracking table or the plan changes.

**Version**: 1.0.0 | **Ratified**: 2026-11-05 | **Last Amended**: 2026-11-05
