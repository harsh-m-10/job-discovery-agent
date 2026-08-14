# Low-Level Design — Job Discovery & Referral Agent

Companion to [HLD.md](./HLD.md). Signatures below are copied from the code, not
paraphrased. Where the implementation is weak, it is called out inline and
collected in [§11](#11-known-limitations).

---

## 1. Module map

| Module | Key exports | Depends on |
|---|---|---|
| `ingest/models.py` | `RawJob`, `ATSAdapter`, `BoardFetchError` | — |
| `ingest/http.py` | `session()`, `get_json(url, timeout=20)` | `models` |
| `ingest/normalize.py` | `html_to_text`, `clean_text`, `content_hash`, `is_india_relevant`, `truncate_for_llm`, `parse_iso`, `parse_epoch_ms` | — |
| `ingest/lifecycle.py` | `Plan`, `plan(company_id, seen, existing)` | `models`, `normalize` |
| `ingest/store.py` | `Store`, `MissingCredentials` | — |
| `ingest/adapters/` | `get_adapter(ats)`, 4 adapters | `http`, `models`, `normalize` |
| `ingest/run.py` | `main()`, `is_due`, `fetch_board` | all of the above |
| `score/prefilter.py` | `prefilter`, `check_title`, `extract_experience`, `Prefiltered` | — |
| `score/llm.py` | `score_batch`, `build_system_prompt`, `parse_scores`, `normalize_entry`, `verdict_for`, `apply_headcount_penalty`, `AllProvidersExhausted` | `ingest.normalize`, `score.providers` |
| `score/providers.py` | `ChatProvider`, `ProviderConfig`, `Pacer`, `build_providers`, 6 exception classes | — |
| `score/healthcheck.py` | `check`, `run`, `Health` | `score.providers` |
| `notify/alerts.py` | `send_alert`, `alert_ingest_errors`, `alert_providers_exhausted`, `check_pipeline_dry` | `notify.email` |
| `notify/watchdog.py` | `main`, `latest_run`, `stranded_jobs` | `notify.alerts` |
| `score/referral.py` | `normalize_company`, `match_company`, `rank_matches`, `Contact`, `Match` | `rapidfuzz` (optional) |
| `score/store.py` | `ScoreStore` | — |
| `score/run.py` | `main()`, `persist()` | all Layer 2 + `ingest.normalize` |
| `notify/whatsapp.py` | `format_message`, `send`, `configured` | — |
| `notify/email.py` | `transport()`, `send` | — |
| `dashboard/lib/db.ts` | `select`, `upsert`, `patch`, `effectivePostedAt`, `hoursSince`, `formatAge` | `server-only` |
| `dashboard/lib/queries.ts` | `getQueue`, `getQueueMeta`, `getJob`, `getFunnel`, `getCompanies` | `db.ts` |

---

## 2. The adapter protocol

```mermaid
classDiagram
    class ATSAdapter {
        <<Protocol>>
        +str ats_name
        +fetch(board_token: str) list~RawJob~
    }
    class RawJob {
        +str ats_job_id
        +str title
        +str location
        +str description
        +str absolute_url
        +datetime|None posted_at
        +str|None compensation
        +dict raw
    }
    class GreenhouseAdapter {
        +ats_name = "greenhouse"
        +fetch(token)
    }
    class LeverAdapter {
        +ats_name = "lever"
        -_description(posting) str
        +fetch(token)
    }
    class AshbyAdapter {
        +ats_name = "ashby"
        -_compensation(job) str|None
        +fetch(token)
    }
    class WorkdayAdapter {
        +ats_name = "workday"
        -_post(url, body) dict
        -_india_facet(base) dict
        -_detail(base, path) dict
        +fetch(token)
    }
    ATSAdapter <|.. GreenhouseAdapter
    ATSAdapter <|.. LeverAdapter
    ATSAdapter <|.. AshbyAdapter
    ATSAdapter <|.. WorkdayAdapter
    ATSAdapter ..> RawJob : returns
```

### Per-adapter field mapping

| | Greenhouse | Lever | Ashby | Workday |
|---|---|---|---|---|
| Endpoint | `GET boards-api…/jobs?content=true` | `GET api.lever.co/v0/postings/{t}?mode=json` | `GET posting-api/job-board/{t}` | `POST wday/cxs/{tenant}/{site}/jobs` |
| Auth | none | none | none | none (browser UA required) |
| `ats_job_id` | `id` | `id` | `id` | `jobReqId` ‖ `bulletFields[0]` ‖ path tail |
| `title` | `title` | `text` | `title` | `title` |
| `posted_at` | `first_published` ‖ `updated_at` | `createdAt` (epoch ms) | `publishedAt` | `startDate` (detail call) |
| `description` | `content` (HTML-escaped) | assembled from 5 fields | `descriptionPlain` | `jobDescription` (detail call) |
| Compensation | — | — | `compensationTierSummary` | — |
| Requests/run | 1 | 1 | 1 | 1 + 1 per India posting |

Three details that are easy to get wrong:

- **Greenhouse uses `first_published`, not `updated_at`.** `updated_at` moves
  whenever anyone edits the req, which would fake the latency signal that
  `hours_since_posted` depends on (`adapters/greenhouse.py`).
- **Lever splits a JD across five fields.** `lists` holds the bulleted
  requirements — exactly where the years-of-experience line lives. Dropping it
  blinds the prefilter (`adapters/lever.py:_description`).
- **Lever's `country` is a real ISO code**, so `"IN"` can be expanded to
  `"India"` safely there — but never in the shared location regex, where
  `\bIN\b` under `re.I` would match the English word "in".

---

## 2b. Provider abstraction

Six vendors behind one contract. All expose an OpenAI-compatible
`/chat/completions`, so vendor differences reduce to configuration —
`score/providers.py` hardcodes **no** rate limit.

```mermaid
classDiagram
    class ProviderConfig {
        +str name
        +str model
        +str base_url
        +str api_key_env
        +int priority
        +int rpm
        +int tpm
        +int batch_size
        +int max_chars
        +bool json_mode
        +bool enabled
        +int timeout
    }
    class ChatProvider {
        +ProviderConfig cfg
        +Pacer pacer
        +available() bool
        -_base_url() str
        +complete(system, user, max_output) str
    }
    class Pacer {
        -float _last_call
        +float|None remaining_tokens
        +before(estimated_tokens)
        +after(headers)
    }
    class ProviderError {
        <<exception>>
    }
    ChatProvider --> ProviderConfig
    ChatProvider --> Pacer
    ProviderError <|-- RequestTooLarge
    ProviderError <|-- RateLimited
    ProviderError <|-- QuotaExhausted
    ProviderError <|-- ServerError
    ProviderError <|-- PaymentRequired
    ProviderError <|-- NotConfigured
```

### Chain as configured

| # | provider | model | measured on an 8,040-token payload |
|---|---|---|---|
| 1 | google | `gemini-flash-lite-latest` | 3.7s |
| 2 | mistral | `mistral-small-latest` | 4.5s |
| 3 | google-flash3 | `gemini-3-flash-preview` | 10.7s |
| 4 | cloudflare | `@cf/openai/gpt-oss-120b` | 13.3s |
| 5 | openrouter | `nvidia/nemotron-3-super-120b-a12b:free` | 15.0s |
| 6 | nvidia | `openai/gpt-oss-120b` | 20.3s |
| 7 | groq | `llama-3.3-70b-versatile` | `enabled: false` — account shared with another project |
| 8 | cerebras | `gpt-oss-120b` | 402 payment required |

`base_url` supports `${VAR}` expansion (`ChatProvider._base_url`) because
Cloudflare carries the account id in the path, and an account id does not belong
in committed config. `available()` returns False when such a variable is missing,
however valid the API key is.

### Error taxonomy — the distinction that matters

Every vendor signals the same conditions differently, and **the status code
alone is not enough**; the body has to be read:

| condition | detection | in-place recovery | then |
|---|---|---|---|
| `RequestTooLarge` | 413, or 429/400 whose body matches `TOO_LARGE_MARKERS` | split batch, halve `char_limit` | next provider |
| `RateLimited` | 429, no daily marker | sleep `retry_after × 2^attempt`, 3 tries | next provider |
| `QuotaExhausted` | 429 whose body matches `DAILY_MARKERS` | none — waiting cannot help | next provider immediately |
| `ServerError` | 5xx, `requests.Timeout`, `ConnectionError` | sleep `4 × 2^attempt`, 3 tries | next provider |
| `PaymentRequired` | 402 | none | next provider |
| `NotConfigured` | missing key or missing `${VAR}` | none | next provider |

`_retry_hint_seconds` parses both dialects — Groq writes
`"Please try again in 33m40.032s"`, Google returns `retryDelay: "27s"`. Reading
the per-minute reset *header* instead of the body is what once burned an entire
daily budget in minutes.

---

## 3. Schema

```mermaid
erDiagram
    companies ||--o{ jobs : "has"
    jobs ||--o| job_scores : "scored by"
    jobs ||--o| applications : "actioned as"
    jobs ||--o{ notifications : "pinged via"
    connections ||--o{ applications : "referred by"

    companies {
        bigserial id PK
        text name
        text ats "CHECK greenhouse|lever|ashby|workday"
        text board_token "UNIQUE(ats, board_token)"
        text category
        int priority "1=every run 2=hourly 3=daily"
        text headcount_band "micro|small|mid|large|unknown — hand-seeded"
        boolean active "false after 5 failures"
        timestamptz last_ok_at "NULL until first success"
        int consecutive_failures
    }
    jobs {
        bigserial id PK
        bigint company_id FK
        text ats_job_id "UNIQUE(company_id, ats_job_id)"
        text title
        text location "NULL when ATS omits it"
        text description "NULL if unfetched"
        text absolute_url
        text compensation "NULL — only Ashby supplies it"
        timestamptz posted_at "NULL when ATS gives no date"
        timestamptz first_seen_at "NEVER updated"
        timestamptz last_seen_at
        timestamptz closed_at "NULL = open"
        int reopen_count
        text content_hash
        jsonb raw
    }
    job_scores {
        bigint job_id PK "ON DELETE CASCADE"
        numeric fit_score "NULL = prefiltered or failed"
        numeric min_years "NULL = unstated in JD"
        numeric max_years
        text[] matched_skills
        text[] gap_skills
        text reasoning
        text verdict "CHECK strong|worth_trying|stretch|reject"
        text reject_reason "set when LLM was skipped"
        text model "'prefilter' or a vendor model id"
        text provider "google|mistral|cloudflare|openrouter|nvidia|prefilter"
        timestamptz scored_at
    }
    applications {
        bigserial id PK
        bigint job_id FK "UNIQUE — one per job"
        text status "CHECK 7 values"
        timestamptz applied_at
        numeric hours_since_posted "FROZEN at first apply"
        boolean via_referral
        bigint referral_contact_id FK "never written today"
        timestamptz responded_at
        text notes
    }
    connections {
        bigserial id PK
        text full_name
        text company_raw
        text company_norm "indexed — the join key"
        text title
        date connected_on
        boolean is_batchmate "set by hand"
        text profile_url
    }
    notifications {
        bigserial id PK
        bigint job_id FK
        text channel "whatsapp|email"
        boolean ok "dedupe key: ok=true means never resend"
        text error
    }
    run_log {
        bigserial id PK
        text worker
        int jobs_seen
        int jobs_new
        int jobs_closed
        jsonb errors
    }
```

### Constraint rationale

| Object | Why |
|---|---|
| `UNIQUE(companies.ats, board_token)` | The natural key. Same token can legitimately exist on two ATS platforms (`ashby/redis` and a hypothetical `greenhouse/redis`). |
| `UNIQUE(jobs.company_id, ats_job_id)` | ATS ids are only unique *within* a board. This is the upsert conflict target. |
| `jobs_open_idx … WHERE closed_at IS NULL` | Partial index. Almost every query filters on open jobs; indexing closed rows wastes space that counts against the 500 MB tier. |
| `jobs_first_seen_idx DESC` | The queue orders by recency. |
| `connections_company_norm_idx` | The referral join key; the raw column is never joined on. |
| `applications.job_id UNIQUE` | One application record per job — makes the status write an upsert rather than an insert-or-update dance. |
| `job_scores.job_id` as PK | One score per job. Re-scoring overwrites rather than accumulating history. **Trade-off:** score history is lost; rejected because calibration drift is tracked via `scored_at` windows instead. |
| `ON DELETE CASCADE` on `job_scores` | A deleted job has no meaningful score. Deliberately *not* applied to `applications` — application history must survive. |

### Nullability that carries meaning

- `jobs.posted_at IS NULL` → the ATS gave no date; age falls back to
  `first_seen_at` (`effectivePostedAt` in `dashboard/lib/db.ts`) and the UI shows
  an "approx" marker.
- `job_scores.fit_score IS NULL` → never LLM-scored. Combined with
  `reject_reason` this distinguishes *prefiltered* from *transport failure*.
- `companies.last_ok_at IS NULL` → never successfully polled; `is_due` treats it
  as due immediately.

---

## 4. State machines

### 4.1 Job lifecycle (Layer 1)

```mermaid
stateDiagram-v2
    [*] --> Open : first fetch, INSERT
    Open --> Open : seen + hash unchanged<br/>(touch last_seen_at)
    Open --> Open : seen + hash changed<br/>(UPDATE, n_changed++)
    Open --> Closed : NOT in fetch<br/>(closed_at = now)
    Closed --> Open : reappears<br/>(closed_at = NULL, reopen_count++)
    Closed --> Closed : still absent (no-op)
```

Enforced entirely by `ingest/lifecycle.py:plan()`. The critical precondition:
**the fetch must be a whole board.** The `Open → Closed` edge is triggered by
absence, so diffing against a partial or filtered response would close jobs
that still exist.

### 4.2 Application status (Layer 2)

```mermaid
stateDiagram-v2
    [*] --> queued : no applications row exists
    queued --> applied : Applied — freezes hours_since_posted
    queued --> skipped
    applied --> responded : sets responded_at
    applied --> rejected : sets responded_at
    applied --> interviewing : sets responded_at
    responded --> interviewing
    interviewing --> offer
    interviewing --> rejected
    skipped --> queued : re-actioned
```

Enforced by `dashboard/app/api/status/route.ts` plus the `CHECK` constraint on
`applications.status`. Two rules live only in the route handler:

```ts
if (status === "applied" && !prior?.applied_at) {
  row.applied_at = now;
  row.hours_since_posted = Number(hoursSince(effectivePostedAt(job)).toFixed(2));
}
if ((RESPONDED.has(status) || status === "rejected") && !prior?.responded_at) {
  row.responded_at = now;
}
```

A **rejection counts as a response**. Excluding it would inflate the response
rate by dropping the negative cases.

**Gap:** the API accepts any transition in `ALLOWED`. `queued → offer` directly
is legal today. The diagram above is intent; the code enforces only the
value set, not the edges.

---

## 5. Algorithms

### 5.1 Content hashing

```python
# ingest/normalize.py
def content_hash(title: str, location: str, description: str) -> str:
    payload = f"{title.strip()}|{location.strip()}|{description.strip()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

Pipe-delimited so that moving text across field boundaries changes the hash.
All three inputs pass through `html_to_text` or `clean_text` first, so a JD
whose only change is markup does not read as edited. `clean_text` exists
precisely because Ashby and Lever serve pre-rendered plain text that skips
`html_to_text` and would otherwise keep its non-breaking spaces, producing a
different hash for identical content.

### 5.2 Lifecycle diffing

```python
# ingest/lifecycle.py
for job in seen:
    seen_ids.add(job.ats_job_id)
    digest = content_hash(job.title, job.location, job.description)
    prior = existing.get(job.ats_job_id)
    if prior is None:
        result.upserts.append(_payload(company_id, job, digest, 0)); result.n_new += 1
        continue
    was_closed = prior.get("closed_at") is not None
    changed = prior.get("content_hash") != digest
    if was_closed or changed:
        bump = (prior.get("reopen_count") or 0) + (1 if was_closed else 0)
        result.upserts.append(_payload(company_id, job, digest, bump))
        ...
    else:
        result.touch_ids.append(prior["id"])

result.close_ids = [row["id"] for ats_job_id, row in existing.items()
                    if ats_job_id not in seen_ids and row.get("closed_at") is None]
```

`_payload` builds an **identical key set for every row** — PostgREST derives the
`ON CONFLICT DO UPDATE` column list from the request body, so a ragged batch
would silently update different columns for different rows. Pinned by
`tests/test_lifecycle.py:test_upsert_payloads_share_one_key_set`.

### 5.3 Experience-range parsing

The most-iterated code in the repo; two real bugs were found against live data.

```python
# score/prefilter.py — patterns
RANGE            = rf"{NUM}\s*\+?\s*(?:-|–|—|to)\s*{NUM}\s*{YEARS}"
RANGE_BOTH_UNITS = rf"{NUM}\s*{YEARS}\s*(?:-|–|—|to)\s*{NUM}\s*{YEARS}"
AT_LEAST         = rf"(?:minimum(?:\s+of)?|at\s+least|min\.?|over|more\s+than)\s+{NUM}\s*\+?\s*{YEARS}"
PLUS             = rf"{NUM}\s*\+\s*{YEARS}"
PLAIN            = rf"{NUM}\s*{YEARS}"
```

**Bug 1 — unit repeated on both ends.** A real Cisco title,
`"ASIC Design Verification Engineer - 5 years to 13Years"`, matched none of the
original patterns and read as *unstated*, so a 5–13 year role passed the gate.
Fixed by `RANGE_BOTH_UNITS`, which is tried **first** because it spans the widest
text and `consider()` keeps whichever pattern claims a span first.

**Bug 2 — body text overriding an explicit title.** Originally the lowest figure
anywhere won. For `"Thermal Engineer - 7 to 10 yrs"` whose body mentioned
"2 years" in passing, the gate read 2 and let it through. The fix makes the
title authoritative:

```python
title_min, title_max = extract_experience(title, require_context=False)
if title_min is not None:
    min_years, max_years = title_min, title_max
else:
    min_years, max_years = extract_experience(description)
```

`require_context=False` for titles because the body heuristic demands an
experience-ish word within ±70 characters — prose JDs have one, titles do not.
In body text the lowest figure still wins, deliberately: over-reading the
requirement would reject jobs the operator should apply to, which is the exact
failure this system exists to avoid.

Guards: figures above `MAX_PLAUSIBLE_YEARS = 15` are ignored, and
`NOT_A_REQUIREMENT` rejects windows containing `ago`, `history`, `founded`,
`revenue`, `grown`, etc. — because *"we have grown 10x in 3 years"* is not a
requirement.

### 5.4 Title gating

Order matters and is load-bearing (`check_title`):

1. `HARD_OFF_FUNCTION` — sales/support roles that satisfy the positive filter by
   accident. `"business development"` matches on *development*;
   `"Technical Support Engineer"` on *engineer*. Without this list the survivors
   were majority GTM roles.
2. `DOMAIN_OFF_FUNCTION` **and not** `STRONG_ENGINEERING` — `finance`, `marketing`
   etc. only disqualify a title with no engineering noun, so
   `"Analytics Engineer – Finance"` survives while
   `"AI Specialist, Treasury Finance Operations"` does not.
3. `MTS` — handled **before** the function and seniority checks. The bare title
   "Member of Technical Staff" contains no engineering noun to match and its
   "staff" would trip the seniority rule; the exemption strips only the phrase,
   so "Senior Member of Technical Staff" is still rejected on the remainder.
4. `TARGET_FUNCTION`, then `SENIOR_TITLE`.

Measured effect on live data: **474 India-relevant postings → 22 survivors (5%)**.

### 5.5 Company-name normalization

```python
# score/referral.py
def normalize_company(raw):
    # NFKD → strip accents → lowercase → drop parentheticals (if content remains)
    # → strip to [a-z0-9& ] → repeatedly apply NOISE until stable
```

The loop is required because noise nests: `"technologies india private limited"`
needs several passes. Matching is exact-first, fuzzy-fallback:

```python
exact = [Match(c, "exact", 100) for c in contacts if c.company_norm == target]
if exact: return exact
...
if ratio < FUZZY_THRESHOLD: continue          # 85
if target.split()[0] != contact.company_norm.split()[0]: continue
```

The leading-token guard exists because `token_set_ratio` treats a subset as a
perfect match — `"walmart"` scores 100 against `"walmart labs"`, but the same
generosity flatters unrelated short strings. **The expensive failure is a false
positive:** it sends the operator to message a stranger about a job at a company
they never worked for. Verified on real data — Atlantis≠Atlan, Whatfix≠Fi Money,
News Corp≠New Relic, WizzyBox≠Wiz all correctly rejected.

### 5.6 Groq pacing

Three distinct 429-ish conditions that look identical unless the body is read:

```python
if resp.status_code == 413: raise RequestTooLarge(...)
if resp.status_code == 429:
    if "Request too large" in body: raise RequestTooLarge(body)
    hint = _retry_hint_seconds(body)          # "try again in 33m40.032s"
    if "tokens per day" in body.lower(): raise DailyQuotaExhausted(hint, body)
    raise RateLimited(min(hint + 2.0, 90.0), body)
```

**This distinction was learned the hard way.** The first implementation read
`x-ratelimit-reset-tokens` — which reports the *per-minute* window and returned
`229ms` — while the actual failure was the *per-day* quota with a 33-minute
reset. It retried immediately and burned the remaining daily budget writing
failure rows over good scores. `tests/test_llm.py` pins every step of that chain.

`RequestTooLarge` recovery is recursive:

```python
if len(jobs) > 1:
    mid = len(jobs) // 2
    return score_batch(jobs[:mid], ...) + score_batch(jobs[mid:], ...)
if char_limit > 1200:
    return score_batch(jobs, char_limit=char_limit // 2, ...)
```

---

## 6. Sequence diagrams

### 6.1 Scoring batch with provider failover

```mermaid
sequenceDiagram
    autonumber
    participant RUN as score/run.py
    participant HC as healthcheck
    participant SB as score_batch
    participant P1 as provider[i]
    participant DB as Supabase

    RUN->>HC: run(providers, fail_fast=True)
    HC->>P1: minimal completion per provider
    alt none live
        HC-->>RUN: RuntimeError → exit 2
    end
    HC-->>RUN: live set (unhealthy providers dropped)
    RUN->>RUN: prefilter every open unscored job
    RUN->>DB: upsert prefilter rejects (fit_score NULL)

    loop each batch
        RUN->>SB: score_batch(batch, providers)
        loop providers in priority order
            SB->>P1: complete(system, user, max_output)
            alt RequestTooLarge
                SB->>SB: split batch / halve char_limit, retry
            else RateLimited or ServerError
                SB->>SB: sleep(backoff × 2^attempt), 3 tries
            else QuotaExhausted / PaymentRequired
                SB->>SB: no wait — next provider
            else 200
                SB->>SB: parse_scores → normalize_entry(provider, model)
            end
        end
        alt every provider refused
            SB-->>RUN: AllProvidersExhausted
            RUN->>RUN: log ERROR
            RUN->>DB: mark remaining scoring_failed:providers_exhausted
            RUN->>RUN: alert_providers_exhausted() → email
            RUN->>RUN: break
        end
        RUN->>RUN: apply_headcount_penalty(row, band) → re-derive verdict
        RUN->>DB: persist() — never overwrite a numeric score with a failure
    end
```

### 6.2 Dashboard status write

```mermaid
sequenceDiagram
    autonumber
    participant UI as JobCard (client)
    participant MW as middleware
    participant API as /api/status
    participant DB as Supabase

    UI->>MW: POST + x-dashboard-secret
    MW->>MW: constantTimeEquals
    alt mismatch
        MW-->>UI: 404
    end
    MW->>API: allow
    API->>API: validate job_id integer, status in ALLOWED
    API->>DB: SELECT jobs (posted_at, first_seen_at)
    API->>DB: SELECT applications WHERE job_id
    alt applied and prior.applied_at is null
        API->>API: hours_since_posted = hoursSince(effectivePostedAt(job))
        Note over API: frozen forever — never recomputed
    end
    API->>DB: upsert on_conflict=job_id
    API-->>UI: {ok, application}
    UI->>UI: play card out, then router.refresh()
```

### 6.3 Referral match

```mermaid
sequenceDiagram
    autonumber
    participant IMP as import_connections.py
    participant FS as ~/.job-agent/private
    participant DB as Supabase
    participant Q as lib/queries.ts

    IMP->>IMP: resolve_csv_path()
    alt path inside repo
        IMP-->>IMP: raise UnsafePath, exit 2
    end
    IMP->>FS: read Connections.csv
    IMP->>IMP: locate header (skip LinkedIn preamble)
    IMP->>IMP: normalize_company per row
    IMP->>DB: DELETE all, then INSERT (full snapshot)
    Q->>DB: SELECT connections
    Q->>Q: index by company_norm
    Q->>Q: normalizeCompany(job company) → lookup
    Q->>Q: batchmates first, top 3
```

**Note the duplication:** `normalizeCompany` is reimplemented in TypeScript in
`dashboard/lib/queries.ts` alongside the Python original. They can drift. See
[§11](#11-known-limitations).

---

## 7. Error taxonomy

| Class | Raised in | Caught in | Retry | Persisted |
|---|---|---|---|---|
| `BoardFetchError` | `ingest/http.py`, `workday.py` | `ingest/run.py:fetch_board` | no | `run_log.errors`, `consecutive_failures++` |
| bare `Exception` (adapter bug) | any adapter | `fetch_board` | no | same, prefixed `adapter error:` |
| `MissingCredentials` | `ingest/store.py.__init__` | `ingest/run.py:main` | no | exit 2 |
| `RuntimeError` (PostgREST ≥300) | `Store._request` | `run.py` per-company | no | `run_log.errors` |
| `PermissionError` | `Store._path` | nothing — **crashes** | no | — (a boundary violation should be loud) |
| `RequestTooLarge` | `_call_groq` | `score_batch` | split/shrink | — |
| `RateLimited` | `_call_groq` | `score_batch` | 3× header-driven | — |
| `DailyQuotaExhausted` | `_call_groq` | `score/run.py:main` | no — stops run | nothing written |
| `ScoringError` | `parse_scores`, `_call_groq` | `score_batch` | 1 retry | `reject_reason=scoring_failed:…` |
| `UnsafePath` | `resolve_csv_path` | `import_connections:main` | no | exit 2 |
| `NotConfigured` | `notify/whatsapp.py`, `email.py` | `notify/run.py` | no | `notifications.ok=false` |

---

## 8. Invariants

| # | Invariant | Enforced by |
|---|---|---|
| 1 | `hours_since_posted` is frozen at first apply and never recomputed | `api/status/route.ts` guard `!prior?.applied_at`; verified live (698.93 held across a status change) |
| 2 | `first_seen_at` is never updated after insert | Absent from `_payload`; `tests/test_lifecycle.py:test_first_seen_at_never_written` |
| 3 | Compensation and notice period never pass through an LLM | `build_system_prompt` filters out `compensation`; asserted at build time |
| 4 | Layer 1 never references Layer 2 tables | `Store._path` at runtime + `check_boundaries.py` in CI |
| 5 | No banned job platform is contacted | `check_boundaries.py` URL scan across all source types |
| 6 | One successful ping per job, ever | `notify/run.py` filters on `notifications.ok=true` |
| 7 | A transport failure never overwrites a real score | `score/run.py:persist()` |
| 8 | Every upsert batch shares one key set | `_payload`; `test_upsert_payloads_share_one_key_set` |
| 9 | `verdict` is always consistent with `fit_score` | `normalize_entry` recomputes it, ignoring the model's own verdict |
| 10 | PII is never read from inside the repo | `resolve_csv_path`; `test_path_inside_the_repo_is_refused` |
| 11 | The service key never reaches the browser | `server-only` import; verified with a planted client import |
| 12 | A run never starts with zero usable providers | `healthcheck.run(fail_fast=True)` → exit 2 |
| 13 | Provider exhaustion is never silent | ERROR log + `scoring_failed` rows + email alert + dashboard banner |
| 14 | Alerts do not spam | `notifications` rows keyed by synthetic negative job_id, per-kind cooldown |
| 15 | A headcount penalty moves `fit_score` by exactly the configured delta | `apply_headcount_penalty`, tested at bounds |

---

## 9. Configuration reference

### Environment variables

| Var | Type | Default | Blast radius if wrong |
|---|---|---|---|
| `SUPABASE_URL` | URL | — | Everything fails at startup |
| `SUPABASE_SERVICE_KEY` | JWT | — | Total DB compromise if leaked |
| `GOOGLE_API_KEY` | string | — | Loses providers 1 and 3 — the two fastest |
| `MISTRAL_API_KEY` | string | — | Loses provider 2 |
| `CLOUDFLARE_API_KEY` + `CLOUDFLARE_ACCOUNT_ID` | string | — | Loses provider 4; the account id is expanded into `base_url` |
| `OPENROUTER_API_KEY` | string | — | Loses provider 5 |
| `NVIDIA_API_KEY` | string | — | Loses provider 6 |
| `GROQ_API_KEY` | string | — | Currently unused — `enabled: false` |
| `SMTP_HOST/PORT/USER/PASS`, `ALERT_EMAIL_TO/FROM` | string | gmail:587 | **No alerting at all** — every silent failure stays silent |
| `DASHBOARD_SECRET` | 32-char hex | — | **Dashboard fully public if empty** — middleware returns 404 for all, so an empty value locks *you* out rather than opening it |
| `CONNECTIONS_CSV_PATH` | path | `~/.job-agent/private/**/Connections.csv` | Refuses to run if inside repo |
| `PING_THRESHOLD` (Vercel) | float | `6.5` | Too low floods the queue; too high hides jobs |
| `DASHBOARD_URL` | URL | `""` | Pings link to the raw ATS URL instead |
| `CALLMEBOT_PHONE` / `_APIKEY` | string | — | WhatsApp disabled; email fallback used |
| `RESEND_API_KEY` / `ALERT_EMAIL_TO` | string | — | No fallback channel at all |
| `SMTP_HOST/PORT/USER/PASS` | string | `smtp.gmail.com:587` | Alternative to Resend |

### `config/settings.yaml`

| Key | Default | Blast radius |
|---|---|---|
| `ping_threshold` | `6.5` | Queue inclusion cutoff |
| `poll_interval_minutes` | `15` | Documentation only — the cron in `ingest.yml` is authoritative |
| `llm_providers[]` | 8 entries | The whole scoring chain. Each carries its own `rpm`/`tpm`/`batch_size`/`max_chars`/`timeout`/`enabled`; nothing is hardcoded |
| `headcount_penalties` | `micro: -2.0`, rest `0.0` | Applied in code after scoring, then the verdict is re-derived |
| `llm_batch_size` | `4` | Fallback only — a provider's own `batch_size` wins |
| `description_char_limit` | `2600` | Fallback only — a provider's own `max_chars` wins |
| `max_experience_years` | `4` | The core hypothesis knob |
| `location_keywords` | list | **Currently unused** — `normalize.py` hardcodes its own regex |
| `fetch_concurrency` | `8` | Higher risks ATS blocking |
| `max_consecutive_failures` | `5` | Auto-deactivation threshold |

### `config/candidate_profile.yaml`

Single source of truth for résumé facts, calibration and compensation. The
`scoring_rules` block is injected **verbatim** into the prompt — it is the only
lever controlling score inflation, so calibration is tuned there, not in code.
The `compensation` block is stripped before the prompt is built and materialised
separately into `dashboard/lib/screening.generated.json` by
`scripts/sync_screening.py`.

---

## 10. Extension guide — adding a fifth ATS

Worked example: SmartRecruiters, whose public API is already confirmed
(`GET https://api.smartrecruiters.com/v1/companies/{id}/postings?limit=100`).

1. **Probe manually first.** Confirm the endpoint is unauthenticated and check
   what a *nonsense* id returns — the whole Workday discovery failed initially
   because 404 and 422 mean the opposite of what they appear to.
2. **Add the adapter** `ingest/adapters/smartrecruiters.py` with
   `ats_name = "smartrecruiters"` and `fetch(board_token) -> list[RawJob]`.
   Use `ingest.http.get_json`; raise `BoardFetchError` on any non-200.
   Descriptions must go through `html_to_text` or `clean_text`.
3. **Register it** in `ingest/adapters/__init__.py:ADAPTERS`.
4. **Relax the DB check constraint:**
   ```sql
   ALTER TABLE companies DROP CONSTRAINT companies_ats_check;
   ALTER TABLE companies ADD CONSTRAINT companies_ats_check
     CHECK (ats IN ('greenhouse','lever','ashby','workday','smartrecruiters'));
   ```
5. **Teach the verifier.** Add the endpoint to `ENDPOINTS` in
   `scripts/verify_boards.py` and a branch in `extract()` returning
   `[(title, location)]`. Compound tokens need their own `probe_*` like Workday's.
6. **Print identity, not just liveness.** Every hit must show company name plus a
   sample title and location. A live board is not proof it is the right company —
   probing "Pine Labs" found a Canadian mortgage brokerage, and "Fractal" a US
   venture studio. Both looked like clean hits on job count alone.
7. **Seed and verify:** add to a `seeds/*.yaml`, run
   `verify_boards.py --seeds … --write-db`.
8. **Run `scripts/check_boundaries.py`** — a new adapter must not import Layer 2.
9. **Add a paging test** if the API paginates; note requests-per-run in the table
   in [§2](#2-the-adapter-protocol).

---

## 11. Known limitations

1. **`normalizeCompany` is duplicated** in `dashboard/lib/queries.ts` (TypeScript)
   and `score/referral.py` (Python), with no shared fixture pinning them
   together. They will drift, and the dashboard will show different referral
   matches than the notifier would.
2. **The dashboard's referral matcher has no fuzzy fallback** — it is exact-match
   only on `company_norm`, whereas Python has a guarded `token_set_ratio` path.
3. **Status transitions are unconstrained** beyond the `CHECK` value set; the
   state machine in [§4.2](#42-application-status-layer-2) is intent, not enforcement.
4. **`settings.yaml:location_keywords` is dead configuration** — `normalize.py`
   hardcodes its own `INDIA`/`REMOTE` regexes. Editing the YAML does nothing.
5. **`poll_interval_minutes` is also inert.** The real schedule is the cron
   expression in `ingest.yml`; the two can silently disagree.
6. **No pagination guard on `getQueue`** — it loads all open jobs and all scores
   into serverless memory on every request.
7. **`applications.referral_contact_id` is never written.** `via_referral` is a
   boolean with no link to *which* contact.
8. **Workday's `ats_job_id` fallback chain is fragile.** If `jobReqId` is absent
   it falls back to `bulletFields[0]` and then the URL path tail; a tenant
   changing its URL shape would orphan every stored row for that board and
   present them as new.
9. **Each provider owns a `Pacer` instance**, mutated in place. Fine
   single-threaded; wrong if scoring is ever parallelised across threads.
10. **No test covers `ingest/store.py` or `score/store.py`** — every PostgREST
    interaction is exercised only through live runs.
11. **`healthcheck` costs one live completion per provider per run.** Eight
    providers means eight extra calls before any scoring; cheap on free tiers,
    but it is not free.
12. **Alert cooldowns live in code**, not config (`notify/alerts.py:COOLDOWN_HOURS`).
13. **`companies.headcount_band` is scaffolding** — all 40 boards are `unknown`
    because none is under 25 employees, so the penalty never fires today.
