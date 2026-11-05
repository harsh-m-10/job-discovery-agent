"""LAYER 1. Greenhouse job board API.

    GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true

Unauthenticated. The endpoint has no search or filtering — the whole board comes
back and filtering happens locally. Greenhouse publishes no rate limit but does
block hammering, so the polling cadence is the throttle.
"""

from __future__ import annotations

from common.geo import names_india

from ..http import get_json
from ..models import RawJob
from ..normalize import html_to_text, parse_iso

URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


class GreenhouseAdapter:
    ats_name = "greenhouse"

    def fetch(self, board_token: str) -> list[RawJob]:
        payload = get_json(URL.format(token=board_token))
        out: list[RawJob] = []
        for job in payload.get("jobs", []) or []:
            job_id = str(job.get("id") or "")
            if not job_id:
                continue
            offices = ", ".join(
                o.get("name", "") for o in (job.get("offices") or []) if o.get("name")
            )
            location = (job.get("location") or {}).get("name") or offices
            # Some boards put a work mode in location.name and the city only in
            # offices: Cloudflare lists 19 "Remote India" roles as "Hybrid",
            # so the India gate never saw them (found 2026-11-05 by the roster
            # audit). Offices are appended only when they name India and the
            # location does not, so no stored posting's location changes.
            if offices and not names_india(location) and names_india(offices):
                location = f"{location} · {offices}" if location else offices
            out.append(
                RawJob(
                    ats_job_id=job_id,
                    title=(job.get("title") or "").strip(),
                    location=location.strip(),
                    description=html_to_text(job.get("content")),
                    absolute_url=job.get("absolute_url") or "",
                    # first_published is the real posting date; updated_at moves
                    # whenever anyone edits the req, which would fake the latency
                    # signal that hours_since_posted depends on.
                    posted_at=parse_iso(job.get("first_published"))
                    or parse_iso(job.get("updated_at")),
                    raw={
                        "requisition_id": job.get("requisition_id"),
                        "departments": [d.get("name") for d in (job.get("departments") or [])],
                        "offices": [o.get("name") for o in (job.get("offices") or [])],
                        "updated_at": job.get("updated_at"),
                    },
                )
            )
        return out
