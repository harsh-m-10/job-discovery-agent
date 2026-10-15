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
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests

from ..http import session
from ..models import BoardFetchError, RawJob
from common.geo import names_india

from ..normalize import clean_text, html_to_text, is_date_only, parse_iso

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

PAGE = 20                 # Workday caps the listing page size

# Wall-clock ceiling for one board (Phase 2.1). Workday is the only adapter that
# makes many sequential requests -- up to MAX_PAGES listing calls plus one
# detail call per posting, each with its own 30s timeout -- so a tenant that
# answers slowly rather than not at all could hold a worker for hours, and the
# job-level timeout would then kill every board's run instead of one. Micron,
# the largest board, takes ~210s locally and less on Actions; 420s leaves 2x
# headroom. Exceeding it raises a *transient* error: a slow day must not count
# toward deactivation, and a partial board is never returned for diffing.
BOARD_BUDGET_S = 420
DETAIL_WORKERS = 4
MULTI_LOCATION = re.compile(r"^\s*\d+\s+locations?\s*$", re.I)
MAX_PAGES = 40            # guard: a facet that fails to narrow must not run away
# The private city list that used to live here is gone: the one place it was
# used -- the fallback row filter in fetch(), for when a tenant exposes no
# location facet -- now asks common.geo.names_india, so there is a single
# vocabulary in config/settings.yaml.
#
# INDIA_FACET below is NOT the same question and deliberately stays narrow; see
# its own note.

# "3 days ago" / "Posted 30+ Days Ago" — Workday gives no absolute posting date
# on the listing, only this label. The detail response carries a real date.
POSTED_DAYS = re.compile(r"(\d+)\+?\s*days?\s*ago", re.I)
POSTED_TODAY = re.compile(r"posted\s+today|today", re.I)

# India as a whole word, so "Bengaluru, India" and "India Remote" match while
# "Indiana" (232 postings on Target's state facet) does not: after "india" the
# "n" is a word character, so \b fails. Exact equality was the previous rule and
# matched only tenants whose facet is a bare country name.
INDIA_FACET = re.compile(r"\bindia\b", re.I)


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
            raise BoardFetchError(f"{type(exc).__name__}: {exc}",
                                  transient=True) from exc

        # 404 means the tenant is real but the site name is wrong; 422 means the
        # tenant/host pair itself is wrong. Both are token errors, not outages.
        if resp.status_code == 404:
            raise BoardFetchError("site name wrong for this tenant (404)")
        if resp.status_code == 422:
            raise BoardFetchError("tenant not on this wd host (422)")
        if resp.status_code >= 500:
            raise BoardFetchError(f"http {resp.status_code}", transient=True)
        if resp.status_code != 200:
            raise BoardFetchError(f"http {resp.status_code}")
        if "application/json" not in resp.headers.get("content-type", ""):
            # Workday's maintenance window: the tenant answers 200 with the SPA
            # shell for every request. It hits a whole wd host at once and is
            # gone by the next run. See BoardFetchError for the dates.
            raise BoardFetchError("got the SPA shell, not the API", transient=True)
        try:
            return resp.json()
        except ValueError as exc:
            raise BoardFetchError("non-json response", transient=True) from exc

    @staticmethod
    def _facet_levels(facet: dict):
        """Yield (parameter, values) for a facet and its one nested level.

        Tenants expose location two different ways. Target answers with a flat
        `Location_Country` whose values are country names. Cisco and Micron
        answer with `locationMainGroup`, a single container value whose own
        `values` are city-level entries ("Bengaluru, India") under the nested
        parameter `locations`. Reading only the top level found nothing for the
        second shape, which is what sent those boards down the unfiltered path.
        """
        parameter = facet.get("facetParameter") or ""
        yield parameter, facet.get("values") or []
        for value in facet.get("values") or []:
            nested = value.get("values")
            if nested:
                yield (value.get("facetParameter") or parameter), nested

    def _india_facet(self, base: str) -> dict[str, list[str]]:
        """Find this tenant's location-facet ids for India.

        Facet ids are per-tenant, so they are read from the board's own first
        response rather than hardcoded. **Every** matching id under the first
        parameter that yields one is returned: a city-level facet needs all of
        Bengaluru, Hyderabad, Chennai and the rest, and taking one would filter
        the board down to a single city.

        Returns an empty dict when no location facet exists, in which case
        paging falls back to local filtering — see `fetch` for why that is now
        a loud failure rather than a silent truncation.
        """
        payload = self._post(f"{base}/jobs",
                             {"appliedFacets": {}, "limit": 1, "offset": 0,
                              "searchText": ""})
        for facet in payload.get("facets") or []:
            for parameter, values in self._facet_levels(facet):
                lowered = parameter.lower()
                if "country" not in lowered and "location" not in lowered:
                    continue
                ids = [v["id"] for v in values
                       if v.get("id")
                       and INDIA_FACET.search(str(v.get("descriptor") or ""))]
                if ids:
                    return {parameter: ids}
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

        deadline = time.monotonic() + BOARD_BUDGET_S

        def over_budget(stage: str) -> None:
            if time.monotonic() > deadline:
                raise BoardFetchError(
                    f"exceeded the {BOARD_BUDGET_S}s per-board budget {stage} "
                    f"— skipped this run rather than diffed partially",
                    transient=True)

        facets = self._india_facet(base)

        postings: list[dict] = []
        offset = 0
        total = 0
        complete = False
        for _ in range(MAX_PAGES):
            over_budget(f"while listing (offset {offset})")
            payload = self._post(f"{base}/jobs",
                                 {"appliedFacets": facets, "limit": PAGE,
                                  "offset": offset, "searchText": ""})
            page = payload.get("jobPostings") or []
            # Tenants on wd5 (HPE, Visa, Target, Adobe) report `total` on the
            # first page only and 0 on every later one. Taking each page's
            # value at face value made `offset >= total` true on page two, so
            # those boards stopped at 40 postings and were marked complete:
            # measured 2026-10-15, Visa 93 India postings -> 40 ingested,
            # Target 64 -> 40, Adobe 56 -> 40. Pages also come back in varying
            # order, so each run saw a different 40 -- closing and reopening
            # jobs. The first page's total is the board's total.
            total = max(total, int(payload.get("total") or 0))
            postings.extend(page)
            offset += PAGE
            if len(page) < PAGE or offset >= total:
                complete = True
                break

        # A board cut off at the page cap is a *partial* response, and
        # lifecycle.plan documents that it must never diff against one: every
        # posting past the cut-off looks withdrawn and is closed, then reopens
        # on the next run whose ordering differs. That is the whole of the
        # observed reopen churn on Cisco and Micron, whose boards carry 1,303
        # and 3,075 postings against an 800 cap. Failing here costs one board
        # for one run; diffing against it corrupts the history.
        if not complete:
            raise BoardFetchError(
                f"truncated at {len(postings)} of {total} postings "
                f"({MAX_PAGES} pages x {PAGE}) — "
                + ("the India facet did not resolve, so the whole board is "
                   "being paged" if not facets else
                   "raise MAX_PAGES or narrow the facet"))

        todo: list[tuple[str, str, str, dict]] = []
        for posting in postings:
            external_path = posting.get("externalPath") or ""
            location = clean_text(posting.get("locationsText") or "")
            title = clean_text(posting.get("title") or "")
            if not external_path or not title:
                continue

            # When the facet lookup failed the server-side narrowing did not
            # happen, so drop non-India rows before paying for a detail fetch.
            if not facets and not names_india(location):
                continue
            todo.append((external_path, location, title, posting))

        # Detail calls are the whole cost of a Workday board (~0.7s each), and
        # were made one at a time: Micron took 208s and HPE would take ~235s,
        # which made the slowest Workday tenant the critical path of every run.
        # A few in parallel cut that by about the worker count. Kept modest,
        # because _detail turns any failure (a 429 included) into an empty
        # posting, so hammering a tenant would cost data quality, not just
        # politeness.
        def fetch_detail(item: tuple[str, str, str, dict]) -> dict[str, Any]:
            over_budget(f"while fetching {len(todo)} details")
            return self._detail(base, item[0])

        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
            details = list(pool.map(fetch_detail, todo))

        out: list[RawJob] = []
        for (external_path, location, title, posting), detail in zip(todo, details):
            # A multi-location posting is listed as just "2 Locations", which
            # names no city, so the India gate dropped it even though Workday's
            # own India facet had selected it: 34 postings on Adobe and HPE
            # alone, measured 2026-10-15. The detail has the real places.
            # Only these strings are rewritten -- every other posting keeps its
            # listing location, so no stored content_hash changes.
            if MULTI_LOCATION.match(location):
                places = [detail.get("location") or "",
                          *(detail.get("additionalLocations") or []),
                          (detail.get("country") or {}).get("descriptor") or ""]
                location = ", ".join(dict.fromkeys(p.strip() for p in places if p and p.strip())) \
                    or location
            description = html_to_text(detail.get("jobDescription"))
            # startDate is a bare calendar day on every tenant measured
            # (1,470 of 1,470 rows at midnight UTC), so record it as such:
            # without the flag, every age and apply-latency computed from it
            # carries up to 24h of invented precision.
            raw_posted = detail.get("startDate")
            posted_at = parse_iso(raw_posted)
            if posted_at is None:          # same fallback order as before
                raw_posted = detail.get("postedOn")
                posted_at = parse_iso(raw_posted)
            precision = (("date" if is_date_only(raw_posted) else "timestamp")
                         if posted_at else None)

            job_id = (detail.get("jobReqId") or posting.get("bulletFields", [None])[0]
                      or external_path.rsplit("/", 1)[-1])

            out.append(RawJob(
                ats_job_id=str(job_id),
                title=title,
                location=location or clean_text(detail.get("location") or ""),
                description=description,
                absolute_url=detail.get("externalUrl") or f"{public_base}{external_path}",
                posted_at=posted_at,
                posted_at_precision=precision,
                raw={
                    "externalPath": external_path,
                    "postedOn": posting.get("postedOn"),
                    "timeType": detail.get("timeType"),
                    "jobReqId": detail.get("jobReqId"),
                },
            ))
        return out
