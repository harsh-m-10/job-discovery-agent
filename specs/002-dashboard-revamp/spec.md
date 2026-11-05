# Feature Specification: Phone-first dashboard revamp

**Feature Branch**: `002-dashboard-revamp`

**Created**: 2026-11-05

**Status**: Draft (waiting on Q1 and mockup approval)

**Input**: User description: "D. UI REVAMP (after A and C; phone-first, since pings open it on the phone). Principle: every number answers a question Harsh would actually ask, in his words. No system internals on the main screens, no instructions to edit YAML or run scripts. TODAY (in progress, new for you, this week, health dot), JOBS (Bengaluru + target families, filter chips, collapsed Older section, cards with actions), PIPELINE (stages with one-tap change, conversion at the bottom), PEOPLE (contacts, warm toggle, asks), HEALTH (ops strip, boards and review). Clean, light/dark following the system setting, one accent colour, readable at 375px. Show a static mockup of TODAY and JOBS first and wait for OK." Follow-up: "do keep light and dark options."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - TODAY: what needs me right now (Priority: P1)

Harsh opens the dashboard (usually from a ping) and the first screen tells him, top to
bottom: the processes in flight and their next step (e.g. Visa round 2, Bureau next round,
Razorpay form), how many new matches arrived since his last visit and what they are, this
week's plain numbers (applied, responses, referral asks sent), and one dot saying whether
the agent is running.

**Why this priority**: Live interview processes are the scarcest and most valuable thing
he tracks; today they are buried under the queue and system banners.

**Independent Test**: With seeded applications at different stages and new matches,
open TODAY at 375 px and check each block's content and order.

**Acceptance Scenarios**:

1. **Given** applications at screen, assessment or interview stage, **When** TODAY opens,
   **Then** "In progress" shows one card each with company, role, stage, next step and
   date, soonest next step first.
2. **Given** 4 new core/secondary matches since his last visit, **When** TODAY opens,
   **Then** "New for you" says "4 new" and lists them; on the next visit the count resets.
3. **Given** the last pipeline run finished 6 minutes ago with no failure, **When** TODAY
   opens, **Then** a green dot reads "All running · last run 6 min ago".
4. **Given** a failure (e.g. canary red, no run for 45 min, one live scoring model),
   **When** TODAY opens, **Then** the dot is amber or red with one plain sentence, and
   tapping it opens Health.
5. **Given** no process in flight, **When** TODAY opens, **Then** "In progress" says so in
   one line instead of an empty box.

---

### User Story 2 - JOBS: the right jobs, quickly actionable (Priority: P1)

A list of Bengaluru jobs in his target families, newest first, filterable with chips
(family, source: direct / Indeed / alert, company tier, freshness). Jobs older than 5 days
sit in a collapsed "Older" section at the bottom. Each card shows title, company, tier,
posted age, score, one-line reason and warm contacts, with Apply, Skip (with a reason) and
Ask referral.

**Why this priority**: This is where pings turn into applications.

**Independent Test**: Seed jobs across families, sources, tiers and ages; toggle each
chip and check the list; act on a card.

**Acceptance Scenarios**:

1. **Given** jobs in and out of target families, **When** JOBS opens, **Then** only
   Bengaluru jobs in target families appear by default.
2. **Given** the "Indeed" source chip is tapped, **When** the list updates, **Then** only
   Indeed jobs show and the chip reads as selected.
3. **Given** jobs posted 2 and 9 days ago, **When** JOBS opens, **Then** the 2-day job is in
   the main list and the 9-day job is inside a collapsed "Older (n)" section.
4. **Given** a card, **When** Harsh taps Skip, **Then** he picks a reason from a short list
   (or types one) and the card leaves the list.
5. **Given** a card with a warm contact at that company, **When** he taps Ask referral,
   **Then** a ready-to-send message and the contact's profile link are shown; nothing is
   sent automatically.

---

### User Story 3 - PIPELINE: where every application stands (Priority: P2)

Applications grouped by stage: applied → screen → assessment → interview → offer, plus
rejected. One tap moves an application to another stage. Conversion by channel, family and
tier sits at the bottom. Replaces the Funnel tab.

**Independent Test**: Move an application through stages and check the counts and
conversion figures change.

**Acceptance Scenarios**:

1. **Given** an application at "applied", **When** he taps it and picks "interview",
   **Then** it moves group immediately and appears in TODAY's "In progress".
2. **Given** outcome emails moved an application to "rejected", **When** PIPELINE opens,
   **Then** it is in the rejected group with the date.

---

### User Story 4 - PEOPLE: who can refer me (Priority: P3)

Contacts at tracked companies, a warm toggle per contact, and asks sent / replied.

**Acceptance Scenarios**:

1. **Given** a contact, **When** he toggles warm, **Then** the contact moves to the top of
   that company's group and is named first on that company's job cards.

---

### User Story 5 - HEALTH: the system, for when something is wrong (Priority: P3)

Runs in the last 24 h, boards, scoring-model mix, yield monitors, canary, last backup,
and the board list and outcome-email review queue. Not in the top navigation; reached
from the health dot or as a badge.

**Acceptance Scenarios**:

1. **Given** 2 outcome emails need review, **When** any page shows, **Then** a small badge
   signals it and leads to the review list in Health.

---

### Edge Cases

- No new matches since the last visit: "Nothing new since <time>", not an empty list.
- First ever visit (no last-visit time): "New for you" counts the last 24 hours.
- Very long titles or company names: truncate to two lines at 375 px, never horizontal
  scroll.
- Summary data stale (pipeline down): numbers show their age; the health dot is red.
- Theme: system setting by default; a manual light/dark choice persists per device and
  can be reset to "system".

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The default page MUST be TODAY with, in order: In progress, New for you,
  This week, Health dot.
- **FR-002**: "In progress" MUST list every application at screen, assessment or interview
  stage with company, role, stage, next step and date; next step and date MUST be
  editable by Harsh and pre-filled from outcome emails where possible.
- **FR-003**: "New for you" MUST count and list core and secondary matches first seen
  since the previous visit from this device.
- **FR-004**: "This week" MUST show applied, responses and referral asks sent as plain
  numbers (no streak bars).
- **FR-005**: The health indicator MUST be one dot with one sentence: green "All running
  (last run X min ago)", or amber/red with what is wrong in plain words; tapping it opens
  Health.
- **FR-006**: JOBS MUST default to Bengaluru jobs in target families, with filter chips for
  family, source, tier and freshness, and jobs older than 5 days in a collapsed "Older"
  section at the bottom.
- **FR-007**: Each job card MUST show title, company, tier, posted age, score, one-line
  reason and warm contacts, with Apply, Skip-with-reason and Ask referral.
- **FR-008**: PIPELINE MUST group applications by stage with one-tap stage change and show
  conversion by channel, family and tier.
- **FR-009**: PEOPLE MUST list contacts at tracked companies with a warm toggle and asks
  sent / replied.
- **FR-010**: HEALTH MUST hold the operational view (runs/24 h, boards, scoring-model
  mix, yield monitors, canary, last backup) and the board and review lists; these MUST
  NOT appear in the top navigation.
- **FR-011**: Main screens (TODAY, JOBS, PIPELINE, PEOPLE) MUST NOT show system internals:
  no file names, settings keys, scripts, table or provider names.
- **FR-012**: Every screen MUST be readable at 375 px wide without horizontal scrolling.
- **FR-013**: The dashboard MUST offer light and dark themes, follow the system setting by
  default, and let Harsh override it per device (light / dark / system).
- **FR-014**: One accent colour across the interface; generous spacing.
- **FR-015**: A static mockup of TODAY and JOBS (both themes, 375 px) MUST be approved by
  Harsh before any of this feature is built.
- **FR-016**: Core vs secondary target families: [NEEDS CLARIFICATION: which role families
  are CORE and which SECONDARY? Not defined in the repo; it may come from part A, which
  was not shared.]

### Key Entities

- **Application stage**: applied, screen, assessment, interview, offer, rejected (plus
  skipped, outside the pipeline), with a next step and next-step date.
- **Last visit**: per device, the time TODAY was last opened.
- **Skip reason**: a short fixed list plus free text.
- **Target families**: core and secondary role families (FR-016).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From tapping a ping, Harsh can see whether the job is new and act on it
  (Apply, Skip, Ask) within 10 seconds.
- **SC-002**: Every live process and its next step is visible on the first screen without
  scrolling past anything else.
- **SC-003**: No main screen contains a file name, command, setting key or provider name.
- **SC-004**: All screens pass at 375 px in both themes with no horizontal scroll and text
  contrast at WCAG AA.
- **SC-005**: The performance budgets of feature 001 still hold after the revamp.

## Assumptions

- Depends on feature 001 (snapshot, pagination, lazy descriptions).
- Existing statuses map to stages: applied → applied, responded → screen, interviewing →
  interview, rejected → rejected; "assessment" and "offer" are new stages.
- "Since last visit" is per device (no accounts; single operator).
- Ask referral prepares text and a link only (Constitution I).
- Navigation: TODAY, JOBS, PIPELINE, PEOPLE in the bar; Health via the dot and a badge.
- The current Funnel, Companies and Review tabs are folded into PIPELINE and HEALTH.
