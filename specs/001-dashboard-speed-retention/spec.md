# Feature Specification: Dashboard speed and data retention

**Feature Branch**: `001-dashboard-speed-retention`

**Created**: 2026-11-05

**Status**: Draft

**Input**: User description: "C. PERFORMANCE + DATA RETENTION (dashboard takes ~20s to load). Measure first (per-query timing, slowest queries, total), co-locate compute with the database, precompute a snapshot at the end of each pipeline run, lean paginated queries run in parallel, indexes verified by query plans, a nightly retention job with explicit keep/delete windows and a seen-jobs record so re-appearing jobs never re-ping, and budgets: first load < 1.5 s on 4G, navigation < 300 ms, JS < 200 KB, no chart libraries."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Open the dashboard from a ping and see it at once (Priority: P1)

Harsh taps an ntfy push or a match email on his phone. Today the dashboard takes about
20 seconds to appear, long enough that he gives up or applies blind. He needs the page
up before he has finished reading the notification.

**Why this priority**: The dashboard is the link between a ping and an application; a
20-second wait defeats the 5-minute discovery latency the pipeline already achieves.

**Independent Test**: Open the dashboard's main page and each tab on a phone over 4G
(or a throttled 4G profile) and time first render and tab switches.

**Acceptance Scenarios**:

1. **Given** a cold phone browser on 4G, **When** Harsh opens the dashboard link from a
   ping, **Then** the page with its headline numbers and job list is readable within
   1.5 s.
2. **Given** the dashboard is open, **When** he switches tabs, **Then** the new view
   appears within 300 ms.
3. **Given** 9,000+ open postings in storage, **When** any page loads, **Then** load time
   does not grow with the number of stored postings.

---

### User Story 2 - Know where the time goes (Priority: P1)

Before anything is changed, Harsh gets a report of how long each dashboard data request
takes and the total per page, and the same report after the changes.

**Why this priority**: Constitution Principle V: measure before changing. Without it the
fix is guesswork.

**Independent Test**: Load each page and read its per-request timings from the browser's
network panel and from the server log; compare the baseline and after reports.

**Acceptance Scenarios**:

1. **Given** any dashboard page, **When** it is served, **Then** the response carries the
   duration of every data request and the total, and the same is logged on the server.
2. **Given** the baseline report, **When** the work is done, **Then** an after report
   exists with the same pages and requests for comparison.

---

### User Story 3 - Storage that does not pile up, and no repeat pings (Priority: P2)

The database keeps what matters forever (applications, outcome emails, referral asks,
warm tags) and sheds the rest on fixed windows, so it stays small and fast. A posting
removed by cleanup that later reappears on its board never pings Harsh a second time.

**Why this priority**: Storage grew from 53 MB to 86 MB in three weeks; large tables are a
cause of slow pages. A repeat ping for a job already seen erodes trust in every ping.

**Independent Test**: Run the cleanup against the live data in report-only mode, then for
real; compare size and row counts before and after; replay a deleted posting through
ingestion and confirm no ping.

**Acceptance Scenarios**:

1. **Given** a posting first seen more than 30 days ago that Harsh did not apply to,
   **When** the nightly cleanup runs, **Then** its description and raw payload are removed.
2. **Given** a posting rejected by the rules prefilter more than 30 days ago, **When** the
   cleanup runs, **Then** it is deleted and its identity is kept in the seen record.
3. **Given** a posting closed, or never acted on, for more than 90 days, **When** the
   cleanup runs, **Then** it is deleted and its identity is kept in the seen record.
4. **Given** a posting whose identity is in the seen record, **When** its board lists it
   again, **Then** it is not stored as new and Harsh is not pinged.
5. **Given** any application, outcome email, referral ask or warm tag, **When** the cleanup
   runs, **Then** it is untouched, and so is the posting it refers to (minus description
   after 30 days only when not applied).
6. **Given** pipeline run records older than 90 days, **When** the cleanup runs, **Then**
   their per-day totals are kept in a daily summary before the records are removed.

---

### Edge Cases

- The snapshot is missing or stale (pipeline down): the dashboard still renders, shows
  the snapshot's age, and the health indicator turns amber/red rather than showing old
  numbers as current.
- A job that was pinged, deleted, and reappears with edited content: it is still not
  pinged again (identity, not content, decides "seen").
- A notification row is deleted after 30 days while its job is still open: the job still
  counts as pinged.
- The cleanup runs while the backup failed that night: the cleanup does not run.
- A page with 0 results, or exactly 25: pagination shows no empty next page.
- A seen-record entry older than 180 days: removed; a posting reappearing after that is
  treated as new.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Every dashboard page MUST report the duration of each data request it makes
  and the page total, both in the response and in the server log.
- **FR-002**: A baseline timing report MUST be recorded before any optimisation and an
  after report on completion, with the same pages and requests.
- **FR-003**: The dashboard's server compute MUST run in the same region as the database,
  and the per-request latency before and after the move MUST be reported.
- **FR-004**: Each pipeline run MUST end by writing one summary record holding every
  headline number the dashboard shows (counts, health status, queue counts by role
  family, last-run time); main pages MUST read that record instead of computing
  aggregates on page load.
- **FR-005**: No dashboard request MAY ask the database for an exact count over a large
  table.
- **FR-006**: List views MUST fetch only the fields they display; a posting's full
  description MUST load only when that posting is expanded or opened.
- **FR-007**: Lists MUST be paginated at 25 items per page.
- **FR-008**: Data requests that do not depend on each other MUST run concurrently.
- **FR-009**: Every filter and sort the dashboard and pipeline use on the large tables
  MUST be backed by an index whose use is confirmed by a query plan; the plans before
  and after are recorded.
- **FR-010**: A nightly cleanup MUST run only after that night's backup succeeded and
  MUST apply: never delete applications, outcome emails, referral asks or warm tags;
  remove descriptions and raw payloads from postings first seen over 30 days ago unless
  applied to; delete prefilter-rejected postings after 30 days; delete closed or
  never-acted-on postings after 90 days; delete notification records after 30 days;
  roll pipeline run records into a daily summary, then delete them after 90 days.
- **FR-011**: Every deleted posting's identity (source, company, posting id, content
  fingerprint, first seen, whether it was pinged) MUST be kept in a seen record for 180
  days; ingestion MUST NOT store, score or ping a posting whose identity is in it.
- **FR-012**: Whether a posting was pinged MUST survive deletion of its notification
  records, so no posting is pinged twice.
- **FR-013**: The cleanup MUST report database size and per-table row counts before and
  after, and MUST support a report-only mode that changes nothing.
- **FR-014**: The dashboard MUST meet: first load < 1.5 s on 4G, navigation < 300 ms,
  client script < 200 KB, no chart library (charts, if any, as small inline drawings).

### Key Entities

- **Dashboard snapshot**: one current record of every headline number, health state and
  queue count by role family, with the time it was computed.
- **Seen posting**: the identity of a posting that has left storage: source, company,
  posting id, content fingerprint, first seen, pinged or not, removed at. Kept 180 days.
- **Daily stats**: per day and per pipeline worker: runs, postings seen/new/closed,
  errors, duration totals. Kept indefinitely (a few hundred rows a year).
- **Posting, score, application, notification, outcome email, referral ask, contact**:
  existing; retention windows above.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The main page is readable within 1.5 s of tapping a ping on 4G (from ~20 s).
- **SC-002**: Switching between dashboard tabs takes under 300 ms.
- **SC-003**: Page load time stays flat as stored postings double.
- **SC-004**: Database size after the first cleanup is at least 40% below the 86 MB
  measured on 2026-11-05, and stays under 100 MB over the following month.
- **SC-005**: Zero repeat pings for postings removed by cleanup, verified by replaying
  deleted postings through ingestion.
- **SC-006**: Applications, outcome emails, referral asks and warm tags: identical counts
  before and after every cleanup.

## Assumptions

- "Region" means the dashboard's server functions; Supabase is in ap-south-1 (Mumbai),
  so the dashboard is pinned to Vercel's Mumbai region (bom1), allowed on the Hobby plan.
- The user's index list names columns that do not exist under those names
  (is_open, city, final_score); they map to open = no close time, city = the Bengaluru
  flag on scores, final score = fit score.
- "Unacted" means no application row (applied or skipped) and no referral ask.
- Prefilter-rejected postings that are still open when deleted are covered by FR-011:
  the seen record stops them being re-inserted every run.
- This supersedes the 15-day retention set on 2026-10-16 (descriptions now kept 30 days).
- Feature 002 (UI revamp) builds on this; this feature changes no screen layout beyond
  pagination and lazy description loading.
