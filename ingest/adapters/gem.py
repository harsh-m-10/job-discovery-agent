"""LAYER 1. Gem job board API.

    GET https://api.gem.com/job_board/v0/{token}/job_posts/

Unauthenticated, the whole board in one response, Greenhouse-shaped. Found
2026-10-16: Hasura is now PromptQL, and promptql.io/careers links
jobs.gem.com/promptql.
"""

from __future__ import annotations

from ..http import get_json
from ..models import RawJob
from ..normalize import clean_text, html_to_text, parse_iso

URL = "https://api.gem.com/job_board/v0/{token}/job_posts/"


class GemAdapter:
    ats_name = "gem"

    def fetch(self, board_token: str) -> list[RawJob]:
        payload = get_json(URL.format(token=board_token))
        out: list[RawJob] = []
        for job in payload or []:
            job_id = str(job.get("id") or "")
            if not job_id:
                continue
            loc = job.get("location") or {}
            location = (loc.get("name") if isinstance(loc, dict) else str(loc)) or ", ".join(
                o.get("name", "") for o in (job.get("offices") or []) if o.get("name"))
            if job.get("location_type") == "remote" and "remote" not in location.lower():
                location = f"{location} (Remote)".strip()
            out.append(RawJob(
                ats_job_id=job_id,
                title=(job.get("title") or "").strip(),
                location=location.strip(),
                description=clean_text(job.get("content_plain")) or html_to_text(job.get("content")),
                absolute_url=job.get("absolute_url") or "",
                posted_at=parse_iso(job.get("first_published_at")) or parse_iso(job.get("created_at")),
                raw={"requisition_id": job.get("requisition_id"),
                     "departments": [d.get("name") for d in (job.get("departments") or [])],
                     "updated_at": job.get("updated_at")},
            ))
        return out
