"""LAYER 2 storage. Reads Layer 1 tables, writes the personalization ones.

The boundary runs one way: Layer 2 may read `jobs` and `companies`, but nothing
in `ingest/` may read anything written here.
"""

from __future__ import annotations

import json
import os
from typing import Any

import requests

PAGE = 1000


class ScoreStore:
    def __init__(self) -> None:
        self.url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        self.key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        if not self.url or not self.key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        self.session = requests.Session()
        self.session.headers.update({
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "User-Agent": "job-agent/0.1",
        })

    def _get(self, table: str, params: str) -> list[dict]:
        rows: list[dict] = []
        offset = 0
        while True:
            resp = self.session.get(
                f"{self.url}/rest/v1/{table}?{params}",
                headers={"Range": f"{offset}-{offset + PAGE - 1}"}, timeout=60,
            )
            resp.raise_for_status()
            batch = resp.json()
            rows.extend(batch)
            if len(batch) < PAGE:
                return rows
            offset += PAGE

    def open_jobs(self, only_unscored: bool = True) -> list[dict]:
        """Open postings joined to their company name.

        PostgREST embeds the parent row through the foreign key, so this stays
        one request rather than a per-job lookup.
        """
        jobs = self._get(
            "jobs",
            "select=id,ats_job_id,title,location,description,absolute_url,"
            "compensation,posted_at,first_seen_at,content_hash,"
            "companies(name,category,headcount_band)&closed_at=is.null"
            "&order=posted_at.desc",
        )
        if not only_unscored:
            return jobs
        scored = {r["job_id"] for r in self._get("job_scores", "select=job_id")}
        return [j for j in jobs if j["id"] not in scored]

    def scores(self, verdicts: list[str] | None = None) -> list[dict]:
        params = ("select=job_id,fit_score,verdict,min_years,max_years,"
                  "matched_skills,gap_skills,reasoning,reject_reason,model,provider,scored_at")
        if verdicts:
            params += f"&verdict=in.({','.join(verdicts)})"
        return self._get("job_scores", params + "&order=fit_score.desc.nullslast")

    def upsert_scores(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        for i in range(0, len(rows), 200):
            resp = self.session.post(
                f"{self.url}/rest/v1/job_scores?on_conflict=job_id",
                data=json.dumps(rows[i:i + 200], default=str),
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                timeout=60,
            )
            if resp.status_code >= 300:
                raise RuntimeError(f"job_scores upsert {resp.status_code}: {resp.text[:400]}")
