# Feature Specification: Wider board coverage and the fastest affordable polling

**Feature Branch**: `003-coverage-max-cadence`

**Created**: 2026-11-06

**Status**: In progress

**Input**: User description: "we still need to improve coverage in our companies in the boards that we have and poll and also we need max possible scheduling". Also: "do not remove indeed from job parsing … indeed very good source for pipeline".

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See a posting minutes after it goes up (Priority: P1)

Every board is checked as often as the free tiers allow. The busiest boards are checked
every 5 minutes instead of every 15, the rest every 15 minutes instead of hourly, and the
slow portals every 2 hours instead of daily. Indeed is searched twice an hour instead of
hourly.

**Why this priority**: Applying early is the one lever the agent controls. Indeed applications
were made 12.9 h after posting against 2.6 h for direct ones.

**Independent Test**: Over 24 h, count runs per hour and polls per board by priority, and
measure posted → pinged latency for timestamped boards. Compare with the 3-week baseline.

**Acceptance Scenarios**:

1. **Given** cron-job.org healthy, **When** an hour passes, **Then** about 12 ingest runs
   complete and every priority-1 board is polled each run.
2. **Given** cron-job.org down, **When** an hour passes, **Then** the Cloudflare Worker still
   produces a 5-minute cadence.
3. **Given** a board whose postings did not change since the last poll, **When** it is polled,
   **Then** its stored rows are not read from the database.
4. **Given** a month at the new cadence, **When** database egress is totalled, **Then** it stays
   under the free tier (5 GB) with room to spare.

---

### User Story 2 - Matches from companies we did not poll (Priority: P1)

Companies where Indeed keeps finding Bengaluru matches but where we have no board of our
own get a board when one can be read without logging in. Indeed stays as a source in every
case.

**Why this priority**: 163 companies produced at least one Bengaluru match on Indeed in 30
days with no board of ours. Among them: Amazon (14 pinged), Apple (11), JPMorganChase (10),
Deloitte (7), Google (6) and SAP (5).

**Independent Test**: For each added board, the first poll returns India postings for that
company, and its jobs no longer arrive only through Indeed.

**Acceptance Scenarios**:

1. **Given** a company with a public Workday, Oracle or SuccessFactors board, **When** it is
   seeded, **Then** it is verified (right employer, India postings) before it is polled.
2. **Given** the same posting found by Indeed and by the company's own board, **When** both
   arrive, **Then** it is stored once. Company names written differently ("Hewlett Packard
   Enterprise | HPE") still match.

### Edge Cases

- A board whose fetch returns nothing never takes the "unchanged" shortcut; the empty-fetch
  guard still applies.
- A diff that fails part-way does not store the fingerprint, so the next poll diffs in full.
- Indeed rate-limits at the higher cadence: errors are recorded and the next half-hour tries
  again; no back-to-back retries.
- Boards with thousands of worldwide postings (JLL, ABB) start at the slowest cadence.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Ingest MUST run every 5 minutes from the primary trigger, with a second
  independent trigger 2 minutes offset that collapses into the run lease while the primary
  is healthy.
- **FR-002**: Board cadence MUST be: priority 1 every run, priority 2 every 15 minutes,
  priority 3 every 2 hours.
- **FR-003**: A board whose postings and content are unchanged since its last full diff MUST
  NOT have its stored rows read; its open postings are marked seen with one write.
- **FR-004**: Every run MUST record how much it read from the database.
- **FR-005**: Indeed MUST be searched twice an hour and MUST remain a source.
- **FR-006**: New boards MUST be verified (employer identity and India postings) before
  activation, and seeded with a priority that matches their weight.
- **FR-007**: Company identity MUST treat "Name | Abbreviation" spellings as the same company.

### Key Entities

- **Board fingerprint**: a digest of a board's postings and their content after the last
  complete diff.

## Success Criteria *(mandatory)*

- **SC-001**: Median posted → pinged latency for timestamped postings falls from 0.4 h.
- **SC-002**: Database egress stays under 3 GB a month at the new cadence.
- **SC-003**: At least 8 new company boards are polled, each with India postings.
- **SC-004**: No duplicate storage of the same posting from Indeed and the company's board.

## Assumptions

- The free tiers bind as measured on 2026-11-05: Supabase egress (5 GB a month) is the
  limit; GitHub Actions minutes are free for this public repo; Cloudflare allows 5 crons.
- Indeed alert emails stay as they are until the operator says otherwise (see the question
  in the conversation).
- Infosys stays excluded (operator's earlier decision).
