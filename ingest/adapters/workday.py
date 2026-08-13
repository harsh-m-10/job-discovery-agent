"""LAYER 1. Workday CXS job board API.

    POST https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs

Public and unauthenticated, but undocumented and materially more fragile than
the other three adapters — hence its own token format and a country facet
lookup. board_token is "tenant|host|site", e.g. "adobe|wd5|external_experienced".

Two things make this adapter different:

1. Boards are enormous. Target lists 2,000 postings; paging all of them at 20 a
   time would be 100 requests per company per run. So the country facet is
   resolved from the board's own response and applied server-side, which cuts
   Target to a few dozen.
2. The listing carries no job description. Only postings that survive the
   location filter get a detail fetch, so description cost scales with what is
   actually relevant rather than with board size.
"""

from __future__ import annotations

import json
import re
from typing import Any

import requests

from ..http import session
from ..models import BoardFetchError, RawJob
from ..normalize import clean_text, html_to_text, parse_iso

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

PAGE = 20                 # Workday caps the listing page size
MAX_PAGES = 40            # guard: a facet that fails to narrow must not run away
INDIA = re.compile(r"\bindia\b|\bbengaluru\b|\bbangalore\b|\bkarnataka\b|"
                   r"\bhyderabad\b|\bchennai\b|\bpune\b|\bgurgaon\b|\bgurugram\b|"
                   r"\bnoida\b|\bmumbai\b", re.I)

# "3 days ago" / "Posted 30+ Days Ago" — Workday gives no absolute posting date
# on the listing, only this label. The detail response carries a real date.
POSTED_DAYS = re.compile(r"(\d+)\+?\s*days?\s*ago", re.I)
POSTED_TODAY = re.compile(r"posted\s+today|today", re.I)


def parse_token(board_token: str) -> tuple[str, str, str]:
    parts = board_token.split("|")
    if len(parts) != 3 or not all(parts):
        raise BoardFetchError(
            f"workday token must be 'tenant|host|site', got {board_token!r}"
        )
    return parts[0], parts[1], parts[2]


class WorkdayAdapter:
    ats_name = "workday"

    def _post(self, url: str, body: dict, timeout: int = 30) -> dict:
        try:
            resp = session().post(
                url, data=json.dumps(body), timeout=timeout,
                headers={"User-Agent": BROWSER_UA, "Accept": "application/json",
                         "Content-Type": "application/json"},
            )
        except requests.RequestException as exc:
            raise BoardFetchError(f"{type(exc).__name__}: {exc}") from exc

        # 404 means the tenant is real but the site name is wrong; 422 means the
        # tenant/host pair itself is wrong. Both are token errors, not outages.
        if resp.status_code == 404:
            raise BoardFetchError("site name wrong for this tenant (404)")
        if resp.status_code == 422:
            raise BoardFetchError("tenant not on this wd host (422)")
        if resp.status_code != 200:
            raise BoardFetchError(f"http {resp.status_code}")
        if "application/json" not in resp.headers.get("content-type", ""):
            raise BoardFetchError("got the SPA shell, not the API")
        try:
            return resp.json()
        except ValueError as exc:
            raise BoardFetchError("non-json response") from exc

    def _india_facet(self, base: str) -> dict[str, list[str]]:
        """Find this tenant's country-facet id for India.

        Facet ids are per-tenant, so they are read from the board's own first
        response rather than hardcoded. Returns an empty dict when no country
        facet exists, in which case paging falls back to local filtering.
        """
        payload = self._post(f"{base}/jobs",
                             {"appliedFacets": {}, "limit": 1, "offset": 0,
                              "searchText": ""})
        for facet in payload.get("facets") or []:
            parameter = facet.get("facetParameter") or ""
            if "country" not in parameter.lower() and "location" not in parameter.lower():
                continue
            for value in facet.get("values") or []:
                descriptor = str(value.get("descriptor") or "")
                if descriptor.strip().lower() == "india" and value.get("id"):
                    return {parameter: [value["id"]]}
        return {}

    def _detail(self, base: str, external_path: str) -> dict[str, Any]:
        try:
            resp = session().get(
                f"{base}{external_path}", timeout=30,
                headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
            )
        except requests.RequestException:
            return {}
        if resp.status_code != 200:
            return {}
        try:
            return resp.json().get("jobPostingInfo") or {}
        except ValueError:
            return {}

    def fetch(self, board_token: str) -> list[RawJob]:
        tenant, host, site = parse_token(board_token)
        base = f"https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
        public_base = f"https://{tenant}.{host}.myworkdayjobs.com/en-US/{site}"

        facets = self._india_facet(base)

        postings: list[dict] = []
        offset = 0
        for _ in range(MAX_PAGES):
            payload = self._post(f"{base}/jobs",
                                 {"appliedFacets": facets, "limit": PAGE,
                                  "offset": offset, "searchText": ""})
            page = payload.get("jobPostings") or []
            postings.extend(page)
            offset += PAGE
            if len(page) < PAGE or offset >= int(payload.get("total") or 0):
                break

        out: list[RawJob] = []
        for posting in postings:
            external_path = posting.get("externalPath") or ""
            location = clean_text(posting.get("locationsText") or "")
            title = clean_text(posting.get("title") or "")
            if not external_path or not title:
                continue

            # When the facet lookup failed the server-side narrowing did not
            # happen, so drop non-India rows before paying for a detail fetch.
            if not facets and not INDIA.search(location):
                continue

            detail = self._detail(base, external_path)
            description = html_to_text(detail.get("jobDescription"))
            posted_at = parse_iso(detail.get("startDate")) or parse_iso(
                detail.get("postedOn"))

            job_id = (detail.get("jobReqId") or posting.get("bulletFields", [None])[0]
                      or external_path.rsplit("/", 1)[-1])

            out.append(RawJob(
                ats_job_id=str(job_id),
                title=title,
                location=location or clean_text(detail.get("location") or ""),
                description=description,
                absolute_url=detail.get("externalUrl") or f"{public_base}{external_path}",
                posted_at=posted_at,
                raw={
                    "externalPath": external_path,
                    "postedOn": posting.get("postedOn"),
                    "timeType": detail.get("timeType"),
                    "jobReqId": detail.get("jobReqId"),
                },
            ))
        return out
