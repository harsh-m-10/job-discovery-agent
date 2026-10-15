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
        # A unique order is what keeps Range pages from overlapping: on
        # 2026-10-15 open_jobs' order=posted_at.desc (not unique) returned
        # duplicates across pages once >1,000 jobs were unscored, and the
        # job_scores upsert failed the whole pipeline run.
        from common.paging import stable_order
        params = stable_order(table, params)
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
            "compensation,posted_at,first_seen_at,content_hash,role_family,"
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

    # --- Phase 7: calibration, provisional re-scoring, provider ledger ----

    def llm_history(self) -> list[tuple[str | None, float]]:
        """(role_family, fit_score) of every real LLM score, for score/rules.py."""
        rows = self._get("job_scores", "select=fit_score,provider,jobs(role_family)"
                                       "&fit_score=not.is.null")
        return [((r.get("jobs") or {}).get("role_family"), float(r["fit_score"]))
                for r in rows if r.get("provider") != "rules"]

    def provisional_job_ids(self) -> set[int]:
        try:
            rows = self._get("job_scores", "select=job_id&provisional=is.true")
        except requests.HTTPError:
            return set()          # column predates 0014
        return {r["job_id"] for r in rows}

    def cooldowns(self) -> dict[str, str]:
        """provider -> cooldown_until, for cooldowns still in force."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            rows = self._get("provider_usage",
                             f"select=provider,cooldown_until&cooldown_until=gt.{now}")
        except requests.HTTPError:
            return {}
        out: dict[str, str] = {}
        for r in rows:
            out[r["provider"]] = max(out.get(r["provider"], ""), r["cooldown_until"])
        return out

    def record_usage(self, usage: dict[str, dict]) -> None:
        """Add this run's counts to today's provider_usage rows. Never raises:
        bookkeeping must not fail a scoring run that already did its work."""
        from datetime import datetime, timezone
        if not usage:
            return
        day = datetime.now(timezone.utc).date().isoformat()
        try:
            prior = {r["provider"]: r for r in self._get(
                "provider_usage", f"select=*&day=eq.{day}")}
            rows = []
            for name, u in usage.items():
                p = prior.get(name, {})
                rows.append({
                    "provider": name, "day": day,
                    **{k: int(p.get(k) or 0) + int(u.get(k) or 0)
                       for k in ("requests", "successes", "rate_limited",
                                 "quota_exhausted", "errors", "jobs_scored")},
                    "last_success_at": (u["last_success_at"].isoformat()
                                        if u.get("last_success_at") else p.get("last_success_at")),
                    "last_error": u.get("last_error") or p.get("last_error"),
                    "cooldown_until": (u["cooldown_until"].isoformat()
                                       if u.get("cooldown_until") else p.get("cooldown_until")),
                })
            resp = self.session.post(
                f"{self.url}/rest/v1/provider_usage?on_conflict=provider,day",
                data=json.dumps(rows, default=str), timeout=60,
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
            if resp.status_code >= 300:
                print(f"  provider ledger not written: {resp.status_code} {resp.text[:160]}")
        except Exception as exc:
            print(f"  provider ledger not written: {type(exc).__name__}: {exc}")

    def sync_location_flags(self, rule) -> int:
        """Set job_scores.location_ok = rule(location, description) for every
        open, scored job; PATCH only the rows that change. -> rows changed."""
        rows = self._get("job_scores", "select=job_id,location_ok,"
                         "jobs!inner(location,description,closed_at)"
                         "&fit_score=not.is.null&jobs.closed_at=is.null")
        flip: dict[bool, list[int]] = {True: [], False: []}
        for r in rows:
            job = r.get("jobs") or {}
            want = bool(rule(job.get("location"), job.get("description") or ""))
            if want != bool(r.get("location_ok", True)):
                flip[want].append(r["job_id"])
        for value, ids in flip.items():
            for i in range(0, len(ids), 300):
                chunk = ",".join(str(j) for j in ids[i:i + 300])
                resp = self.session.patch(
                    f"{self.url}/rest/v1/job_scores?job_id=in.({chunk})",
                    data=json.dumps({"location_ok": value}),
                    headers={"Prefer": "return=minimal"}, timeout=60)
                resp.raise_for_status()
        return len(flip[True]) + len(flip[False])

    def upsert_scores(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        # Every row states whether it is provisional. An upsert only writes
        # the columns present, so an LLM score replacing a rules score must
        # carry provisional=false explicitly or the row stays provisional and
        # is re-scored forever (db/migrations/0014).
        rows = [{**r, "provisional": bool(r.get("provisional", False))} for r in rows]
        for i in range(0, len(rows), 200):
            resp = self.session.post(
                f"{self.url}/rest/v1/job_scores?on_conflict=job_id",
                data=json.dumps(rows[i:i + 200], default=str),
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                timeout=60,
            )
            if resp.status_code >= 300:
                raise RuntimeError(f"job_scores upsert {resp.status_code}: {resp.text[:400]}")
