"""LAYER 1. Supabase (PostgREST) access, restricted to Layer 1 tables.

Only `companies`, `jobs`, and `run_log` may be touched from here. Reading
`connections`, `applications`, or `job_scores` from Layer 1 breaks the boundary
rule in spec §3 and is rejected at construction time.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Iterable

import requests

from common.company_key import company_key
from common.paging import stable_order

# trigger_log (0016) is pipeline telemetry. alert_inbox (0018) holds job-alert
# mail -- job ads, the input of a Layer 1 source -- forwarded from the
# operator's Gmail; Layer 1 reads the ads out of it and nothing else.
LAYER1_TABLES = {"companies", "jobs", "run_log", "trigger_log", "alert_inbox"}
PAGE = 1000

# run_log columns that a migration adds and that the deployed schema may not
# have yet. Code and schema cannot be deployed together here: a migration is
# applied by hand (Supabase SQL editor, or psycopg2 over the ap-south-1
# pooler), while this file reaches the runner on the next trigger. A run that
# lands between the two must still record everything the schema *can* hold
# rather than losing the whole row.
OPTIONAL_RUN_LOG_COLUMNS = {"boards", "trigger_source", "duration_s"}

# PostgREST's PGRST204 body names the column it could not find, e.g.
# "Could not find the 'boards' column of 'run_log' in the schema cache".
_MISSING_COLUMN = re.compile(r"Could not find the '([^']+)' column")


class MissingCredentials(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, url: str | None = None, key: str | None = None):
        self.url = (url or os.environ.get("SUPABASE_URL", "")).rstrip("/")
        self.key = key or os.environ.get("SUPABASE_SERVICE_KEY", "")
        if not self.url or not self.key:
            raise MissingCredentials(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set "
                "(copy .env.example to .env, or set them as GitHub Actions secrets)"
            )
        self.session = requests.Session()
        self.session.headers.update({
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "User-Agent": "job-agent/0.1",
        })

    # --- plumbing -------------------------------------------------------

    def _path(self, table: str) -> str:
        if table not in LAYER1_TABLES:
            raise PermissionError(
                f"Layer 1 may not access {table!r} (spec §3 boundary rule)"
            )
        return f"{self.url}/rest/v1/{table}"

    def _request(self, method: str, table: str, *, params: str = "",
                 body: Any = None, prefer: str | None = None,
                 headers: dict | None = None) -> requests.Response:
        hdrs = dict(headers or {})
        if prefer:
            hdrs["Prefer"] = prefer
        url = self._path(table) + (f"?{params}" if params else "")
        resp = self.session.request(
            method, url,
            data=json.dumps(body, default=str) if body is not None else None,
            headers=hdrs, timeout=45,
        )
        if resp.status_code >= 300:
            raise RuntimeError(
                f"supabase {method} {table} -> {resp.status_code}: {resp.text[:400]}"
            )
        return resp

    def _select_all(self, table: str, params: str) -> list[dict]:
        """Paged GET. Supabase caps a single response at 1000 rows, and pages
        are only consistent under a unique order (common/paging.py)."""
        params = stable_order(table, params)
        rows: list[dict] = []
        offset = 0
        while True:
            resp = self._request(
                "GET", table, params=params,
                headers={"Range-Unit": "items",
                         "Range": f"{offset}-{offset + PAGE - 1}"},
            )
            batch = resp.json()
            rows.extend(batch)
            if len(batch) < PAGE:
                return rows
            offset += PAGE

    # --- companies ------------------------------------------------------

    def active_companies(self) -> list[dict]:
        return self._select_all(
            "companies",
            "select=id,name,ats,board_token,category,priority,last_ok_at,"
            "consecutive_failures&active=eq.true&ats=neq.aggregator"
            "&order=priority.asc,name.asc",
        )
        # ats=aggregator rows are companies seen only through Indeed or a job
        # alert (db/migrations/0018). There is no board to poll.

    def fill_missing_company_keys(self) -> int:
        """Give every company without a company_key one (db/migrations/0009).

        Runs at the start of each ingest so a newly seeded board is joinable
        to contacts and to outcome emails from its first run, with no separate
        backfill step to forget. Touches only rows where the key is NULL.
        Never raises. It is housekeeping at the head of the ingest run, so any
        failure here would otherwise cost the whole run -- which is what
        happened on 2026-10-15, seconds after 0009 was applied, while
        PostgREST's schema cache still lacked the column. A missed fill is
        retried by the next run.
        """
        filled = 0
        try:
            rows = self._select_all("companies", "select=id,name&company_key=is.null")
            for row in rows:
                self._request("PATCH", "companies", params=f"id=eq.{row['id']}",
                              body={"company_key": company_key(row["name"]) or None},
                              prefer="return=minimal")
                filled += 1
        except Exception as exc:
            print(f"  note: company_key fill stopped after {filled} "
                  f"({type(exc).__name__}: {str(exc)[:160]}); next run retries")
        return filled

    def mark_company_ok(self, company_id: int) -> None:
        self._request("PATCH", "companies", params=f"id=eq.{company_id}",
                      body={"last_ok_at": now_iso(), "consecutive_failures": 0},
                      prefer="return=minimal")

    def mark_company_failure(self, company_id: int, failures: int,
                             max_failures: int) -> bool:
        """-> True if this failure deactivated the board."""
        deactivate = failures >= max_failures
        patch: dict[str, Any] = {"consecutive_failures": failures}
        if deactivate:
            patch["active"] = False
        self._request("PATCH", "companies", params=f"id=eq.{company_id}",
                      body=patch, prefer="return=minimal")
        return deactivate

    # --- jobs -----------------------------------------------------------

    def existing_jobs(self, company_id: int) -> dict[str, dict]:
        rows = self._select_all(
            "jobs",
            f"select=id,ats_job_id,content_hash,closed_at,reopen_count,"
            f"last_seen_at&company_id=eq.{company_id}",
        )
        return {r["ats_job_id"]: r for r in rows}

    def upsert_jobs(self, rows: list[dict]) -> None:
        if not rows:
            return
        # first_seen_at is deliberately absent from every payload: PostgREST
        # updates exactly the columns present, so omitting it preserves the
        # original sighting time that the latency telemetry is measured from.
        for i in range(0, len(rows), 200):
            self._request(
                "POST", "jobs", params="on_conflict=company_id,ats_job_id",
                body=rows[i:i + 200],
                prefer="resolution=merge-duplicates,return=minimal",
            )

    def touch_jobs(self, job_ids: Iterable[int]) -> None:
        ids = list(job_ids)
        if not ids:
            return
        for i in range(0, len(ids), 500):
            chunk = ",".join(str(j) for j in ids[i:i + 500])
            self._request("PATCH", "jobs", params=f"id=in.({chunk})",
                          body={"last_seen_at": now_iso()}, prefer="return=minimal")

    def close_jobs(self, job_ids: Iterable[int]) -> None:
        ids = list(job_ids)
        if not ids:
            return
        stamp = now_iso()
        for i in range(0, len(ids), 500):
            chunk = ",".join(str(j) for j in ids[i:i + 500])
            self._request("PATCH", "jobs", params=f"id=in.({chunk})",
                          body={"closed_at": stamp}, prefer="return=minimal")

    # --- run log --------------------------------------------------------

    def last_automated_run_start(self, worker: str = "ingest") -> datetime | None:
        """Start time of the newest *finished*, *automated* run, for the lease.

        Finished only: a row with no finished_at is either still running (the
        Actions concurrency group already serialises those) or crashed, and a
        crash must not suppress the retry. Automated only: a manual spot check
        with --only polls a handful of boards and must not stop the next real
        poll. `neq.manual` also excludes NULL, i.e. rows from before
        trigger_source existed, which is the conservative direction -- at worst
        the first run after deploy polls when it could have skipped.
        """
        rows = self._request(
            "GET", "run_log",
            params=(f"select=started_at&worker=eq.{worker}"
                    f"&finished_at=not.is.null&trigger_source=neq.manual"
                    f"&order=started_at.desc&limit=1"),
        ).json()
        if not rows:
            return None
        try:
            return datetime.fromisoformat(rows[0]["started_at"].replace("Z", "+00:00"))
        except (KeyError, ValueError, AttributeError):
            return None

    def log_trigger(self, source: str, outcome: str,
                    run_id: int | None = None) -> None:
        """One trigger_log row per fire (db/migrations/0016). Never raises:
        bookkeeping about a trigger must not be what stops the run it fired."""
        try:
            self._request("POST", "trigger_log", prefer="return=minimal",
                          body={"source": source, "outcome": outcome,
                                "run_id": run_id})
        except Exception as exc:
            print(f"  trigger_log: not recorded ({str(exc)[:120]})")

    def start_run(self, worker: str) -> int | None:
        resp = self._request("POST", "run_log", body={"worker": worker},
                             prefer="return=representation")
        rows = resp.json()
        return rows[0]["id"] if rows else None

    def finish_run(self, run_id: int | None, *, jobs_seen: int, jobs_new: int,
                   jobs_closed: int, errors: list[dict],
                   boards: list[dict] | None = None,
                   trigger_source: str | None = None,
                   duration_s: float | None = None) -> None:
        """Close out the run_log row.

        `boards` comes from db/migrations/0004; `trigger_source` and
        `duration_s` from 0005. Any of the three may be missing from the
        deployed schema — see OPTIONAL_RUN_LOG_COLUMNS — so the PATCH degrades
        one column at a time rather than failing the run.
        """
        if run_id is None:
            return
        self._patch_run_log(run_id, {
            "finished_at": now_iso(),
            "jobs_seen": jobs_seen,
            "jobs_new": jobs_new,
            "jobs_closed": jobs_closed,
            "errors": errors or None,
            "boards": boards or None,
            "trigger_source": trigger_source,
            "duration_s": duration_s,
        })

    def _patch_run_log(self, run_id: int, body: dict) -> None:
        """PATCH run_log, dropping whichever columns the schema lacks.

        PostgREST answers PGRST204 naming the one column it could not find, so
        each miss is removed and the PATCH retried. Only columns in
        OPTIONAL_RUN_LOG_COLUMNS may be dropped this way: a PGRST204 naming
        `jobs_seen` means the schema is broken, not merely behind, and must
        still raise. Every non-PGRST204 error raises untouched.
        """
        body = dict(body)
        while True:
            try:
                self._request("PATCH", "run_log", params=f"id=eq.{run_id}",
                              body=body, prefer="return=minimal")
                return
            except RuntimeError as exc:
                match = _MISSING_COLUMN.search(str(exc))
                if "PGRST204" not in str(exc) or match is None:
                    raise
                column = match.group(1)
                if (column not in body
                        or column not in OPTIONAL_RUN_LOG_COLUMNS):
                    raise
                body.pop(column)
                print(f"  note: run_log.{column} is missing, so it was not "
                      f"recorded. Apply the migration in db/migrations/ that "
                      f"adds it.")
