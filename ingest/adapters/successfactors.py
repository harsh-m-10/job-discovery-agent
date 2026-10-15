"""LAYER 1. SAP SuccessFactors Career Site Builder (docs/PLAN.md 6.2).

    POST https://{host}/services/recruiting/v1/jobs     -> search, 10 per page
    GET  https://{host}/job/{urlTitle}/{id}-{locale}    -> posting page (HTML)

Token: "host|locale", e.g. "jobs.standardchartered.com|en_GB". The locale
matters twice: it is part of every posting URL, and it decides the date
format in the search results (en_GB is day/month/year).

Found 2026-10-15 while looking for where the operator's referral network
works: Standard Chartered (7 contacts) runs Career Site Builder. Its search
page is rendered by JavaScript and its RSS feed is only the 20 newest
postings worldwide, but the JSON endpoint the page itself calls honours a
location filter: 344 India postings.

The search result carries no description, so each posting page is fetched
and the element marked itemprop="description" (schema.org microdata) is
extracted with a real HTML parser rather than by position. The search gives
only a start *date*, so posted_at_precision is "date".

Same rules as the other detail-fetching adapters: never a partial board, a
wall-clock budget, a few detail fetches in parallel.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import quote

import requests

from ..http import session
from ..models import BoardFetchError, RawJob
from ..normalize import clean_text, html_to_text

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
PAGE = 10                      # fixed by the endpoint
MAX_PAGES = 100                # 1,000 postings; Standard Chartered India is 344
DETAIL_WORKERS = 4
BOARD_BUDGET_S = 420

# Day/month order by locale. Unknown locales fall back to ISO-like parsing and
# then to no date at all -- never to a guess that could swap day and month.
DATE_FORMATS = {"en_GB": "%d/%m/%Y", "en_US": "%m/%d/%Y"}


def parse_token(board_token: str) -> tuple[str, str]:
    host, _, locale = board_token.partition("|")
    host, locale = host.strip(), (locale.strip() or "en_US")
    if not host or "/" in host:
        raise BoardFetchError(f"successfactors token must be 'host|locale', got {board_token!r}")
    return host, locale


def parse_start(value: str | None, locale: str) -> datetime | None:
    if not value:
        return None
    for fmt in filter(None, (DATE_FORMATS.get(locale), "%Y-%m-%d")):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


class _ItempropDescription(HTMLParser):
    """Collect the inner HTML of the element with itemprop="description"."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self.depth:
            self.depth += 1
            self.parts.append(self.get_starttag_text() or "")
        elif dict(attrs).get("itemprop") == "description":
            self.depth = 1

    def handle_endtag(self, tag):
        if not self.depth:
            return
        self.depth -= 1
        if self.depth:
            self.parts.append(f"</{tag}>")

    def handle_startendtag(self, tag, attrs):
        if self.depth:
            self.parts.append(self.get_starttag_text() or "")

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)

    def handle_entityref(self, name):
        if self.depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name):
        if self.depth:
            self.parts.append(f"&#{name};")


def extract_description(page_html: str) -> str:
    parser = _ItempropDescription()
    parser.feed(page_html)
    return clean_text(html_to_text("".join(parser.parts)))


class SuccessFactorsAdapter:
    ats_name = "successfactors"

    def _search(self, host: str, locale: str, page: int) -> dict:
        # sortBy "date" is the only order that pages deterministically. With
        # the default (""), measured 2026-10-15 on Standard Chartered, 344 rows
        # held only ~260 distinct postings and two passes disagreed on 108 of
        # them: pages overlapped and the rest were never returned. title,
        # location and id orders were just as unstable.
        body = {"locale": locale, "pageNumber": page, "sortBy": "date", "keywords": "",
                "location": "India", "facetFilters": {}, "brand": "", "skills": [],
                "categoryId": 0, "alertId": "", "rcmCandidateId": ""}
        try:
            resp = session().post(f"https://{host}/services/recruiting/v1/jobs", json=body,
                                  timeout=30, headers={"User-Agent": BROWSER_UA,
                                                       "Accept": "application/json"})
        except requests.RequestException as exc:
            raise BoardFetchError(f"{type(exc).__name__}: {exc}", transient=True) from exc
        if resp.status_code == 404:
            raise BoardFetchError("career site search not found (404) — host may have changed")
        if resp.status_code >= 500:
            raise BoardFetchError(f"http {resp.status_code}", transient=True)
        if resp.status_code != 200:
            raise BoardFetchError(f"http {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise BoardFetchError("non-json search response", transient=True) from exc

    def _page(self, url: str) -> str:
        try:
            resp = session().get(url, timeout=30, headers={"User-Agent": BROWSER_UA})
        except requests.RequestException as exc:
            raise BoardFetchError(f"{type(exc).__name__}: {exc}", transient=True) from exc
        if resp.status_code != 200:
            raise BoardFetchError(f"posting page http {resp.status_code}", transient=True)
        return resp.text

    def fetch(self, board_token: str) -> list[RawJob]:
        host, locale = parse_token(board_token)
        deadline = time.monotonic() + BOARD_BUDGET_S

        results: list[dict] = []
        total = 0
        for page in range(MAX_PAGES):
            payload = self._search(host, locale, page)
            batch = [r.get("response") or {} for r in payload.get("jobSearchResult") or []]
            total = max(total, int(payload.get("totalJobs") or 0))
            results.extend(batch)
            if len(batch) < PAGE or len(results) >= total:
                break
        else:
            if len(results) < total:
                raise BoardFetchError(f"truncated at {len(results)} of {total} postings")

        # The search's location filter is a geocoded text match, not a country
        # code, so nothing is filtered here on its say-so: rows are kept as
        # returned and normalize.py's India gate judges them like any other.
        results = [r for r in results if r.get("id")]

        def url_of(r: dict) -> str:
            slug = r.get("unifiedUrlTitle") or r.get("urlTitle") or "job"
            return f"https://{host}/job/{quote(slug, safe='%-')}/{r['id']}-{locale}"

        def detail(r: dict) -> str:
            if time.monotonic() > deadline:
                raise BoardFetchError(f"exceeded the {BOARD_BUDGET_S}s per-board budget — "
                                      f"skipped this run rather than diffed partially",
                                      transient=True)
            return extract_description(self._page(url_of(r)))

        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
            descriptions = list(pool.map(detail, results))

        out: list[RawJob] = []
        for r, description in zip(results, descriptions):
            places = [p.strip() for p in (r.get("jobLocationShort") or []) if p and p.strip()]
            countries = [c.strip() for c in (r.get("jobLocationCountry") or []) if c and c.strip()]
            location = ", ".join(dict.fromkeys(places + countries))
            posted = parse_start(r.get("unifiedStandardStart"), locale)
            out.append(RawJob(
                ats_job_id=str(r["id"]),
                title=clean_text(r.get("unifiedStandardTitle") or ""),
                location=location,
                description=description,
                absolute_url=url_of(r),
                posted_at=posted,
                posted_at_precision="date" if posted else None,
                raw={"category": (r.get("mfield1") or [None])[0],
                     "end": r.get("unifiedStandardEnd")},
            ))
        return out
