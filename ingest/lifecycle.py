"""LAYER 1. Board diffing: what is new, edited, still there, gone, or back.

Pure functions over plain dicts — no network, no DB — so the rules that produce
reopen_count and closed_at can be tested directly.

Rules (spec §6.3), applied per company against a full board fetch:
  1. seen and unknown            -> insert
  2. seen, known, hash changed   -> update and re-score
  3. seen, known, was closed     -> reopen, increment reopen_count
  4. seen, known, unchanged      -> bump last_seen_at only
  5. known, open, not seen       -> close
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import RawJob
from .normalize import content_hash


@dataclass
class Plan:
    upserts: list[dict] = field(default_factory=list)
    touch_ids: list[int] = field(default_factory=list)
    close_ids: list[int] = field(default_factory=list)
    n_new: int = 0
    n_changed: int = 0
    n_reopened: int = 0

    @property
    def n_closed(self) -> int:
        return len(self.close_ids)


def _payload(company_id: int, job: RawJob, digest: str, reopen_count: int) -> dict:
    """Every payload carries an identical key set — PostgREST derives the
    ON CONFLICT update column list from the request body, so a ragged batch
    would silently update different columns for different rows."""
    return {
        "company_id": company_id,
        "ats_job_id": job.ats_job_id,
        "title": job.title,
        "location": job.location or None,
        "description": job.description or None,
        "absolute_url": job.absolute_url,
        "compensation": job.compensation,
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
        "closed_at": None,
        "reopen_count": reopen_count,
        "content_hash": digest,
        "raw": job.raw or {},
    }


def plan(company_id: int, seen: list[RawJob], existing: dict[str, dict]) -> Plan:
    """Diff a freshly fetched board against what the DB already holds.

    `existing` maps ats_job_id -> {id, content_hash, closed_at, reopen_count}.
    """
    result = Plan()
    seen_ids: set[str] = set()

    for job in seen:
        seen_ids.add(job.ats_job_id)
        digest = content_hash(job.title, job.location, job.description)
        prior = existing.get(job.ats_job_id)

        if prior is None:
            result.upserts.append(_payload(company_id, job, digest, 0))
            result.n_new += 1
            continue

        was_closed = prior.get("closed_at") is not None
        changed = prior.get("content_hash") != digest
        if was_closed or changed:
            bump = (prior.get("reopen_count") or 0) + (1 if was_closed else 0)
            result.upserts.append(_payload(company_id, job, digest, bump))
            result.n_reopened += 1 if was_closed else 0
            result.n_changed += 1 if changed and not was_closed else 0
        else:
            result.touch_ids.append(prior["id"])

    # A board fetch is authoritative: anything still open that the board no
    # longer lists is gone. This only holds because the whole board is fetched
    # every time — never diff against a partial or filtered response.
    result.close_ids = [
        row["id"]
        for ats_job_id, row in existing.items()
        if ats_job_id not in seen_ids and row.get("closed_at") is None
    ]
    return result
