---
description: "Task list for feature 002: phone-first dashboard revamp"
---

# Tasks: Phone-first dashboard revamp

**Input**: `/specs/002-dashboard-revamp/` (spec.md, plan.md, approved mockup)

## Phase 1: Foundational

- [ ] T001 `db/migrations/0029_revamp.sql`:
  - applications.status adds `screen`, `assessment`;
  - add `next_step text`, `next_step_at date`, `skip_reason text`;
  - v_queue appends `company_tier` (companies.priority);
  - `refresh_dashboard_snapshot` v2 adds `in_progress`, `new_24h.by_source` and `ops` (latest canary/backup rows);
  - apply to scratch and live.
- [ ] T002 [P] `common/role.py`: data-science titles ("data scien*", "applied/decision/research scientist", "data sci" misspellings) → `ml`; test in `tests/test_role.py`; backfill the stored role_family for matching titles
- [ ] T003 [P] `config/settings.yaml` `target_families` (core: backend, genai, ml; secondary: platform, data, fullstack) and `scripts/gen_dashboard_settings.py` → `dashboard/lib/settings.generated.ts`
- [ ] T004 [P] `scripts/log_worker.py` and the steps in `.github/workflows/canary.yml` and `backup.yml` (if: always(), ok/failed)
- [ ] T005 `outcomes/run.py`: assessment → `assessment`; next_step prefill for assessment/interview; update `tests/test_outcomes.py`
- [ ] T006 `dashboard/app/api/status/route.ts`: allow `screen`, `assessment`; accept `skip_reason`, `next_step`, `next_step_at`
- [ ] T007 Theme: tokens for light, dark and system in `dashboard/app/globals.css`; `system|light|dark` cookie in `dashboard/app/layout.tsx`; `ThemeSwitch.tsx`

## Phase 2: US1 TODAY (P1)

- [ ] T008 [US1] `lib/queries.ts`: `getInProgress()` (screen, responded, assessment, interviewing, offer; soonest next step first) and `getNewSince(iso)` (core + secondary families, Bengaluru, queued, above threshold)
- [ ] T009 [US1] `app/d/[secret]/page.tsx` TODAY: In progress (with the Update sheet for stage, next step and date), New for you, This week, HealthDot; `MarkVisit.tsx` sets `seen_at`
- [ ] T010 [US1] `layout.tsx` + `Nav.tsx`: brand, ThemeSwitch, review badge; nav Today/Jobs/Pipeline/People

## Phase 3: US2 JOBS (P1)

- [ ] T011 [US2] `lib/queries.ts` `getJobs({fam, src, tier, fresh, page})` on v_queue: card fields only, 25 per page; older-than-5-days page separately
- [ ] T012 [US2] `app/d/[secret]/jobs/page.tsx`: chip rows as links, count line, cards, a collapsed `<details>` "Older than 5 days (n)"
- [ ] T013 [US2] `jobs/JobItem.tsx`: Apply (opens the posting, then "Applied?" confirm), Skip with reasons, Ask referral (link to the job page's referral list)

## Phase 4: US3 PIPELINE (P2)

- [ ] T014 [US3] `getPipeline()`: applications by stage with company, role, date, next step; conversion by channel, family and tier from v_funnel
- [ ] T015 [US3] `pipeline/page.tsx` + `StageSelect.tsx` (one tap: a native select posting to /api/status)

## Phase 5: US4 PEOPLE, US5 HEALTH (P3)

- [ ] T016 [US4] `people/page.tsx` (contacts with warm toggle, asks sent/replied); `/contacts` redirects
- [ ] T017 [US5] `health/page.tsx`:
  - the reasons behind the dot;
  - runs/24 h;
  - last run;
  - failing and inactive boards;
  - scoring-model mix;
  - new 24 h by source;
  - canary, last backup;
  - links to Boards, Review, Detailed stats.

## Phase 6: Polish

- [ ] T018 Remove internals from the main screens (old queue banners with commands move to Health)
- [ ] T019 Update the snapshot contract and test; run `next build`; run perf_report into `specs/002-dashboard-revamp/perf-after.md`
- [ ] T020 Commit in steps; push; check 375 px in both themes

## Dependencies

T001 → T008, T011, T014, T017. T003 → T008, T011. T007 → all UI. US1 and US2 can proceed in
parallel after Phase 1.
