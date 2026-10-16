"""LAYER 1. Societe Generale careers (a Sinequa search behind the careers site).

    token = search keyword, e.g. "Bangalore"

    GET  https://careers.societegenerale.com/en/search           -> csrfToken in the page
    GET  .../sg-careers-offers/get-token  (x-csrf-token, session cookie) -> {"token"}
    POST .../search-proxy.php  (authorization-api: Bearer <token>,
                                x-proxy-url: <the Sinequa search-profile endpoint>)

Found 2026-10-16 by recording the site's requests. The token endpoint answers
403 to any request without the page's CSRF token and session cookie, which is
why plain fetches failed and why this adapter starts from the page. Apply links
go to socgen.taleo.net. As with Avature, the keyword doubles as the location
filter: "Bangalore" -> 231 postings on the day.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import requests

from ..models import BoardFetchError, RawJob
from ..normalize import html_to_text

SITE = "https://careers.societegenerale.com"
SEARCH_PROFILE = ("https://api.socgen.com/business-support/it-for-it-support/"
                  "cognitive-service-knowledge/api/v1/search-profile")
PAGE = 50
MAX_PAGES = 20
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")


def _stamp(value: str | None) -> datetime | None:
    try:
        d = datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def to_job(doc: dict) -> RawJob | None:
    job_id = doc.get("sourcestr4") or ""
    if not job_id:
        return None
    desc = "\n\n".join(p for p in (
        html_to_text(doc.get("sourcestr13")), html_to_text(doc.get("sourcevarchar1")),
        html_to_text(doc.get("sourcevarchar2")), html_to_text(doc.get("sourcevarchar3"))) if p)
    return RawJob(
        ats_job_id=job_id,
        title=(doc.get("resulttitle") or doc.get("title") or "").strip(),
        location=doc.get("sourcestr7") or "",
        description=desc,
        absolute_url=doc.get("url1") or doc.get("resulturl") or "",
        # sourcedatetime2 is the publication date, sourcedatetime1 the last update.
        posted_at=_stamp(doc.get("sourcedatetime2") or doc.get("sourcedatetime1")),
        posted_at_precision="date",
        raw={"entity": doc.get("sourcestr15"), "contract": doc.get("sourcestr8"),
             "apply": doc.get("url3")},
    )


class SocGenAdapter:
    ats_name = "socgen"

    def fetch(self, board_token: str) -> list[RawJob]:
        s = requests.Session()
        h = {"User-Agent": UA, "Referer": f"{SITE}/en/search", "Origin": SITE}
        try:
            page = s.get(f"{SITE}/en/search", headers=h, timeout=40).text
            m = re.search(r'"csrfToken":"([0-9a-f]+)"', page)
            if not m:
                raise BoardFetchError("no csrfToken on the search page -- site changed")
            tok = s.get(f"{SITE}/sg-careers-offers/get-token", timeout=40,
                        headers={**h, "x-csrf-token": m.group(1),
                                 "x-requested-with": "XMLHttpRequest"})
            if tok.status_code != 200:
                raise BoardFetchError(f"get-token http {tok.status_code}",
                                      transient=tok.status_code >= 500)
            bearer = tok.json()["token"]
            out: dict[str, RawJob] = {}
            for n in range(MAX_PAGES):
                q = {"profile": "ces_profile_sgcareers", "lang": "en",
                     "responseType": "SearchResult",
                     "query": {"advanced": [{"type": "simple", "name": "sourcestr6",
                                             "op": "eq", "value": "job"}],
                               "text": board_token, "skipCount": PAGE, "skipFrom": n * PAGE,
                               "sort": "sourcedatetime1.desc"}}
                r = s.post(f"{SITE}/search-proxy.php", data=json.dumps(q), timeout=40,
                           headers={**h, "Content-Type": "application/json",
                                    "authorization-api": f"Bearer {bearer}",
                                    "x-proxy-url": SEARCH_PROFILE})
                if r.status_code != 200:
                    raise BoardFetchError(f"search http {r.status_code}",
                                          transient=r.status_code >= 500)
                d = r.json()
                docs = (d.get("Result") or {}).get("Docs") or []
                for doc in docs:
                    job = to_job(doc)
                    if job:
                        out.setdefault(job.ats_job_id, job)
                if len(docs) < PAGE or (n + 1) * PAGE >= int(d.get("TotalCount") or 0):
                    break
            return list(out.values())
        except requests.RequestException as exc:
            raise BoardFetchError(f"{type(exc).__name__}: {exc}", transient=True) from exc
        except (ValueError, KeyError) as exc:
            raise BoardFetchError(f"unexpected response: {exc}", transient=True) from exc
