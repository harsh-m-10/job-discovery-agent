"""LAYER 1. Avature career portals (server-rendered HTML; no public JSON API).

    token = "<host>/<portal path>|<search keyword>"
            e.g. "jobs.siemens.com/en_US/externaljobs|Bangalore"

    GET https://{host}/{portal}/SearchJobs/?search=<kw>&folderOffset=<n>   6 per page, fixed
    GET https://{host}/{portal}/JobDetail/<id>                             one per posting

Found 2026-10-16 for Siemens. The portal's location filters load through a
private API, but its keyword search matches the location line, so the search
keyword is the location filter: "Bangalore" -> 178 postings on the day, "India"
-> 855. The keyword is the Bengaluru city rather than India because every
posting costs a detail page: at India width this board alone would be ~1,000
requests per poll. Boards here therefore run at priority 3 (daily).
"""

from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote

import requests

from ..http import session
from ..models import BoardFetchError, RawJob
from ..normalize import html_to_text

PAGE_SIZE = 6            # fixed by the portal; jobRecordsPerPage is ignored
MAX_PAGES = 200
DETAIL_WORKERS = 6      # 12 measured no faster (283 s vs 275 s): the portal throttles
ITEM = re.compile(r'<a class="link" href="(https://[^"]+/JobDetail/(\d+))"[^>]*>\s*(.*?)\s*</a>', re.S)
FIELD = re.compile(r'<div class="article__content__view__field([^"]*)"[^>]*>(.*?)'
                   r'(?=<div class="article__content__view__field[ "]|<div class="article__content__separator|$)',
                   re.S)
TOTAL = re.compile(r'aria-label="(\d[\d,]*) results"')
LABEL = re.compile(r'<div class="article__content__view__field__label"[^>]*>(.*?)</div>', re.S)


def parse_token(token: str) -> tuple[str, str]:
    portal, _, keyword = token.partition("|")
    if not portal or not keyword:
        raise BoardFetchError(f"avature token must be host/portal|keyword, got {token!r}")
    return portal.strip("/"), keyword


def _get(url: str) -> str:
    try:
        resp = session().get(url, timeout=40, headers={"Accept": "text/html"})
    except requests.RequestException as exc:
        raise BoardFetchError(f"{type(exc).__name__}: {exc}", transient=True) from exc
    if resp.status_code == 404:
        raise BoardFetchError("portal not found (404)")
    if resp.status_code != 200:
        raise BoardFetchError(f"http {resp.status_code}", transient=resp.status_code >= 500)
    return resp.text


def parse_listing(page: str) -> list[tuple[str, str, str]]:
    """-> [(url, job_id, title)] in page order."""
    return [(url, jid, " ".join(html.unescape(re.sub(r"<[^>]+>", " ", title)).split()))
            for url, jid, title in ITEM.findall(page)]


def parse_detail(page: str) -> dict:
    """Label/value field blocks -> location, posted date and description."""
    out = {"location": "", "posted_at": None, "description": ""}
    longest = ""
    for classes, body in FIELD.findall(page):
        label_m = LABEL.search(body)
        label = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", label_m.group(1))).split()) if label_m else ""
        value = html_to_text(LABEL.sub("", body))
        if "tf_locations" in classes or label.lower().startswith("location"):
            out["location"] = "; ".join(l.strip(" -") for l in value.splitlines() if l.strip(" -"))
        elif label.lower().startswith("posted"):
            m = re.search(r"(\d{1,2}-[A-Za-z]{3}-\d{4})", value)
            if m:
                d = datetime.strptime(m.group(1), "%d-%b-%Y")
                out["posted_at"] = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        elif not label and len(value) > len(longest):
            longest = value                # the unlabelled field is the job description
    out["description"] = longest
    # Some templates put "Posted since" in the header block instead of a field.
    if out["posted_at"] is None:
        m = re.search(r"Posted since\s*</?[^>]*>?\s*(\d{1,2}-[A-Za-z]{3}-\d{4})", page)
        if m:
            d = datetime.strptime(m.group(1), "%d-%b-%Y")
            out["posted_at"] = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return out


class AvatureAdapter:
    ats_name = "avature"

    def fetch(self, board_token: str) -> list[RawJob]:
        portal, keyword = parse_token(board_token)
        base = f"https://{portal}"
        listed: dict[str, tuple[str, str]] = {}

        def page_url(n: int) -> str:
            return f"{base}/SearchJobs/?search={quote(keyword)}&folderOffset={n * PAGE_SIZE}"

        # The first page states the total ("aria-label=\"178 results\""), so the
        # rest can be fetched in parallel: serially, Siemens' 30 pages took
        # ~150 s on their own.
        first = _get(page_url(0))
        m = TOTAL.search(first)
        total = int(m.group(1).replace(",", "")) if m else PAGE_SIZE
        pages = min(MAX_PAGES, -(-total // PAGE_SIZE))
        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
            bodies = [first] + list(pool.map(lambda n: _get(page_url(n)), range(1, pages)))
        for body in bodies:
            for u, j, t in parse_listing(body):
                listed.setdefault(j, (u, t))

        def detail(item):
            jid, (url, title) = item
            try:
                d = parse_detail(_get(url))
            except BoardFetchError:
                d = {"location": "", "posted_at": None, "description": ""}
            return RawJob(ats_job_id=jid, title=title, location=d["location"] or keyword,
                          description=d["description"], absolute_url=url,
                          posted_at=d["posted_at"],
                          posted_at_precision="date" if d["posted_at"] else None,
                          raw={"search": keyword})

        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
            return list(pool.map(detail, listed.items()))
