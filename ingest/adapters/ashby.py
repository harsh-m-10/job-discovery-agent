"""LAYER 1. Ashby job board posting API.

    GET https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true

Cleanest of the three: plain-text descriptions are served directly, and it is the
only one that reliably exposes compensation.
"""

from __future__ import annotations

from ..http import get_json
from ..models import RawJob
from ..normalize import clean_text, html_to_text, parse_iso

URL = "https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"


def _compensation(job: dict) -> str | None:
    comp = job.get("compensation") or {}
    summary = comp.get("compensationTierSummary")
    if summary and job.get("shouldDisplayCompensationOnJobPostings"):
        return str(summary)
    return None


class AshbyAdapter:
    ats_name = "ashby"

    def fetch(self, board_token: str) -> list[RawJob]:
        payload = get_json(URL.format(token=board_token))
        out: list[RawJob] = []
        for job in payload.get("jobs", []) or []:
            job_id = str(job.get("id") or "")
            if not job_id or job.get("isListed") is False:
                continue
            locations = [job.get("location") or ""]
            locations += [
                s.get("location", "") for s in (job.get("secondaryLocations") or [])
            ]
            if job.get("isRemote"):
                locations.append("Remote")
            location = ", ".join(dict.fromkeys(l for l in locations if l))
            out.append(
                RawJob(
                    ats_job_id=job_id,
                    title=(job.get("title") or "").strip(),
                    location=location,
                    description=clean_text(job.get("descriptionPlain"))
                    or html_to_text(job.get("descriptionHtml")),
                    absolute_url=job.get("jobUrl") or job.get("applyUrl") or "",
                    posted_at=parse_iso(job.get("publishedAt")),
                    compensation=_compensation(job),
                    raw={
                        "department": job.get("department"),
                        "team": job.get("team"),
                        "employmentType": job.get("employmentType"),
                        "workplaceType": job.get("workplaceType"),
                        "isRemote": job.get("isRemote"),
                    },
                )
            )
        return out
