"""LAYER 1. Oracle Recruiting Cloud (Candidate Experience) public REST API.

    token = "<host prefix>|<siteNumber>|<location>"   e.g. "eeho.fa.us2|CX_45001|India"

    GET https://{host}.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions
        ?onlyData=true&expand=requisitionList.secondaryLocations
        &finder=findReqs;siteNumber=..,location=..,limit=..,offset=..,sortBy=POSTING_DATES_DESC
    GET .../recruitingCEJobRequisitionDetails?expand=all&onlyData=true
        &finder=ById;Id="<id>",siteNumber=..

Unauthenticated. The search filters server-side by location, which is what
keeps this one fetch from being the whole company: Oracle's own site
(CX_45001; CX_1 serves the same data) lists ~2,350 postings, 15 in India on
2026-10-16. Descriptions need one detail call per posting, so boards here are
kept at priority 2 or lower (see seeds/companies_batch6.yaml for the reason).
"""

from __future__ import annotations

from urllib.parse import quote

from ..http import get_json
from ..models import BoardFetchError, RawJob
from ..normalize import html_to_text, parse_iso

PAGE = 50
MAX_PAGES = 20


def parse_token(token: str) -> tuple[str, str, str]:
    parts = token.split("|")
    if len(parts) != 3 or not all(parts):
        raise BoardFetchError(f"oracle token must be host|siteNumber|location, got {token!r}")
    return parts[0], parts[1], parts[2]


class OracleAdapter:
    ats_name = "oracle"

    def fetch(self, board_token: str) -> list[RawJob]:
        host, site, location = parse_token(board_token)
        base = f"https://{host}.oraclecloud.com/hcmRestApi/resources/latest"
        reqs: list[dict] = []
        for page in range(MAX_PAGES):
            url = (f"{base}/recruitingCEJobRequisitions?onlyData=true"
                   f"&expand=requisitionList.secondaryLocations"
                   f"&finder=findReqs;siteNumber={site},location={quote(location)},"
                   f"limit={PAGE},offset={page * PAGE},sortBy=POSTING_DATES_DESC")
            items = (get_json(url, timeout=40) or {}).get("items") or []
            if not items:
                break
            batch = items[0].get("requisitionList") or []
            reqs.extend(batch)
            total = int(items[0].get("TotalJobsCount") or 0)
            if len(batch) < PAGE or len(reqs) >= total:
                break
        out: list[RawJob] = []
        for r in reqs:
            job_id = str(r.get("Id") or "")
            if not job_id:
                continue
            locs = [r.get("PrimaryLocation") or ""] + [
                s.get("Name") or "" for s in (r.get("secondaryLocations") or [])]
            desc = r.get("ShortDescriptionStr") or ""
            try:
                d = (get_json(f'{base}/recruitingCEJobRequisitionDetails?expand=all&onlyData=true'
                              f'&finder=ById;Id="{job_id}",siteNumber={site}', timeout=40)
                     or {}).get("items") or []
                if d:
                    desc = "\n\n".join(html_to_text(d[0].get(k)) for k in (
                        "ExternalDescriptionStr", "ExternalResponsibilitiesStr",
                        "ExternalQualificationsStr") if d[0].get(k)) or desc
            except BoardFetchError:
                pass          # the short description is still a usable posting
            out.append(RawJob(
                ats_job_id=job_id,
                title=(r.get("Title") or "").strip(),
                location="; ".join(l for l in locs if l),
                description=desc,
                absolute_url=(f"https://{host}.oraclecloud.com/hcmUI/CandidateExperience/en/"
                              f"sites/{site}/job/{job_id}"),
                posted_at=parse_iso(r.get("PostedDate")),
                posted_at_precision="date",
                raw={"job_family": r.get("JobFamily"), "workplace": r.get("WorkplaceTypeCode")},
            ))
        return out
