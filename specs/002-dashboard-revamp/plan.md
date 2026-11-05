# Implementation Plan: Phone-first dashboard revamp

**Branch**: `002-dashboard-revamp` (work lands on `main`) | **Date**: 2026-11-05 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-dashboard-revamp/spec.md`; approved
mockup https://claude.ai/artifact/52xWCXuBVxC6Qna35aVuKP

## Summary

Five screens replace the five old tabs:
- **TODAY**: in progress, new for you, this week, and the health dot.
- **JOBS**: filter chips and a collapsed "Older" section.
- **PIPELINE**: applications by stage with one-tap moves, plus conversion.
- **PEOPLE**: contacts and asks.
- **HEALTH**: everything operational.

Everything reads the 001 snapshot or paginated, lean queries, so the 001 budgets hold.
The theme follows the system setting, with a manual Light/Dark override.

## Technical Context

- **Language/version**: TypeScript 5.7, Next.js 16 App Router, React 19 (server
  components by default, client components only for taps); Python 3.11 for the
  pipeline pieces; PostgreSQL 15.
- **Dependencies**: none new. Inline SVG icons, no chart library.
- **Storage**: migration 0029:
  - application stages `screen` and `assessment`;
  - `next_step`, `next_step_at` and `skip_reason` on applications;
  - `company_tier` appended to v_queue;
  - snapshot v2 with ops status (canary, backup), new-24h by source and in-progress count.
- **Testing**:
  - `next build` plus a type check;
  - Python scripts for the role, outcomes and snapshot contract;
  - snapshot contract updated.
- **Constraints**: 001 budgets; 375 px; WCAG AA contrast in both themes; no internals on
  the main screens.

## Constitution Check

| Principle | Check | Result |
|---|---|---|
| I Operator decides | "Ask referral" prepares text and a link only; Apply opens the posting; Harsh confirms "applied" | Pass |
| III Free tier | No new services; reads go through the snapshot | Pass |
| IV Nothing fails silently | Health dot amber/red with a reason; canary and backup status now visible | Pass |
| V Measure | perf_report re-run after the revamp | Pass |
| VI Public repo | No data in the repo; the secret path stays in the URL only | Pass |
| VII Answers, not internals | Main screens carry no file, script, setting or provider names; those live on Health | Pass |

## Design decisions

- **Stages.** The pipeline shows applied → screen → assessment → interview → offer, plus
  rejected.
  - `responded` is shown as "Screen", and new moves write `screen`.
  - Outcome emails map assessment → `assessment` (previously `responded`) and interview →
    `interviewing`.
  - Moving a job to assessment or interview pre-fills `next_step` when it is empty.
- **Last visit.** A per-device cookie `seen_at` is read by TODAY. A small client component
  rewrites it after render. The first visit counts the last 24 h.
- **Target families.** These go in `config/settings.yaml:target_families`, generated into
  `lib/settings.generated.ts` like the other dashboard settings.
- **JOBS filters.** They live in the URL search params (`fam`, `src`, `tier`, `fresh`), so
  the chips are plain links and need no client JS. The "Older" section is a
  `<details>` element.
- **Theme.** A cookie stores `system | light | dark`. `system` writes no `data-theme`
  attribute, and the CSS uses `prefers-color-scheme`. The palette is the mockup's: one
  blue accent.
- **Old routes.**
  - `/funnel` stays reachable from Health as "Detailed stats".
  - `/contacts` redirects to `/people`.
  - `/companies` and `/review` stay as pages linked from Health.
- **Ops status.** canary.yml and backup.yml write a `run_log` row (worker `canary` /
  `backup`) through `scripts/log_worker.py`, and the snapshot reports the latest of each.

## Project structure

```text
db/migrations/0029_revamp.sql
common/role.py                       # data-science titles -> ml
config/settings.yaml                 # target_families
scripts/gen_dashboard_settings.py    # emits CORE_FAMILIES / SECONDARY_FAMILIES
scripts/log_worker.py                # NEW canary/backup status rows
outcomes/run.py                      # assessment stage, next_step prefill
dashboard/app/globals.css            # tokens: light / dark / system; new components
dashboard/app/layout.tsx             # theme cookie system|light|dark
dashboard/app/d/[secret]/
  layout.tsx  Nav.tsx  ThemeSwitch.tsx  HealthDot.tsx  MarkVisit.tsx
  page.tsx (TODAY)  jobs/page.tsx  jobs/JobItem.tsx
  pipeline/page.tsx  pipeline/StageSelect.tsx
  people/page.tsx  health/page.tsx  contacts/page.tsx (redirect)
dashboard/lib/queries.ts             # getInProgress, getNewSince, getJobs(filters), getPipeline
dashboard/app/api/status/route.ts    # stages, skip_reason, next_step
```

## Complexity Tracking

None.
