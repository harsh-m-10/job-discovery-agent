"""LAYER 1. Atlassian's careers listing (iCIMS behind it).

    GET https://www.atlassian.com/endpoint/careers/listings

The JSON www.atlassian.com/company/careers/all-jobs renders from, found
2026-10-16 by recording the page's requests. One response, every posting
worldwide (~350) with full HTML sections; apply links go to
globalcareers-atlassian.icims.com. iCIMS itself has no public API, so this
endpoint is the board. The token is unused and must be "atlassian".
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..http import get_json
from ..models import BoardFetchError, RawJob
from ..normalize import html_to_text

URL = "https://www.atlassian.com/endpoint/careers/listings"


def _updated(value) -> datetime | None:
    """'2026-10-15 10:17 AM' -> that day, midnight UTC (a date, not a time)."""
    try:
        d = datetime.strptime(str(value).strip(), "%Y-%m-%d %I:%M %p")
    except ValueError:
        return None
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


class AtlassianAdapter:
    ats_name = "atlassian"

    def fetch(self, board_token: str) -> list[RawJob]:
        if board_token != "atlassian":
            raise BoardFetchError(f"atlassian adapter serves one board, got {board_token!r}")
        out: dict[str, RawJob] = {}
        for job in get_json(URL, timeout=40) or []:
            job_id = str(job.get("id") or "")
            if not job_id:
                continue
            # The same req appears once per iCIMS portal it is posted to (18
            # repeats on 2026-10-16). One response, no paging, so a repeat is
            # not instability: merge its locations into the first sighting.
            if job_id in out:
                extra = [" ".join(str(l).split()) for l in (job.get("locations") or [])]
                known = out[job_id].location.split("; ")
                out[job_id].location = "; ".join(known + [l for l in extra if l not in known])
                continue
            post = job.get("portalJobPost") or {}
            # "Bengaluru - India -   Bengaluru,  560071 India" -> one tidy string.
            locs = [" ".join(str(l).split()) for l in (job.get("locations") or [])]
            desc = "\n\n".join(html_to_text(job.get(k)) for k in
                               ("overview", "responsibilities", "qualifications") if job.get(k))
            out[job_id] = RawJob(
                ats_job_id=job_id,
                title=(job.get("title") or "").strip(),
                location="; ".join(locs),
                description=desc,
                absolute_url=job.get("applyUrl") or post.get("portalUrl") or "",
                # Only an update time is published, not a posting time. It is
                # used as a date-precision stand-in so the age gates still see
                # a months-old req as old; "date" keeps it out of latency stats.
                posted_at=_updated(post.get("updatedDate")),
                posted_at_precision="date",
                raw={"category": job.get("category"), "type": job.get("type"),
                     "updated": post.get("updatedDate")},
            )
        return list(out.values())
