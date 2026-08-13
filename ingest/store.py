"""LAYER 1. Supabase (PostgREST) access, restricted to Layer 1 tables.

Only `companies`, `jobs`, and `run_log` may be touched from here. Reading
`connections`, `applications`, or `job_scores` from Layer 1 breaks the boundary
rule in spec §3 and is rejected at construction time.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Iterable

import requests

LAYER1_TABLES = {"companies", "jobs", "run_log"}
PAGE = 1000


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
        """Paged GET. Supabase caps a single response at 1000 rows."""
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
            "consecutive_failures&active=eq.true&order=priority.asc,name.asc",
        )

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
            f"select=id,ats_job_id,content_hash,closed_at,reopen_count"
            f"&company_id=eq.{company_id}",
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

    def start_run(self, worker: str) -> int | None:
        resp = self._request("POST", "run_log", body={"worker": worker},
                             prefer="return=representation")
        rows = resp.json()
        return rows[0]["id"] if rows else None

    def finish_run(self, run_id: int | None, *, jobs_seen: int, jobs_new: int,
                   jobs_closed: int, errors: list[dict]) -> None:
        if run_id is None:
            return
        self._request("PATCH", "run_log", params=f"id=eq.{run_id}", body={
            "finished_at": now_iso(),
            "jobs_seen": jobs_seen,
            "jobs_new": jobs_new,
            "jobs_closed": jobs_closed,
            "errors": errors or None,
        }, prefer="return=minimal")
