"""LAYER 1. SmartRecruiters public Posting API (docs/PLAN.md 6.1).

    GET https://api.smartrecruiters.com/v1/companies/{company}/postings
        ?country=in&limit=100&offset=N              -> listing, India only
    GET .../postings/{id}                           -> one posting, with the JD

The token is the company identifier as it appears in jobs.smartrecruiters.com
URLs (BoschGroup, Freshworks, ServiceNow).

India is filtered server-side with `country=in`: the country field is an ISO
code, as reliable as Lever's, unlike free-text location strings. Probed
2026-10-15: Bosch 626 India postings, ServiceNow 76, Experian 37, Freshworks 37
-- Freshworks had silently left Lever for SmartRecruiters.

Two properties this shares with the Workday adapter, because it has the same
shape (a listing, then one detail call per posting for the description):

  * Never a partial board. lifecycle.plan closes every posting it does not
    see, so a board with one failed detail call must fail as a whole
    (transiently) rather than come back short.
  * A wall-clock budget, so one slow tenant cannot hold the run.

Details are fetched in parallel, because Bosch alone is 626 calls.

Note: an unknown company identifier is not a 404 here -- the API answers 200
with totalFound 0. A mistyped token therefore looks like an empty board; the
daily roster audit (scripts/audit_roster.py) is what catches it.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

from ..http import get_json
from ..models import BoardFetchError, RawJob
from ..normalize import clean_text, html_to_text, parse_iso

API = "https://api.smartrecruiters.com/v1/companies/{company}/postings"
PAGE = 100
MAX_PAGES = 20                 # 2,000 India postings; Bosch is 626
DETAIL_WORKERS = 8
BOARD_BUDGET_S = 420           # same ceiling as the Workday adapter

SECTION_ORDER = ("jobDescription", "qualifications", "additionalInformation",
                 "companyDescription")


def _description(detail: dict) -> str:
    """The JD sections, requirements first. Company boilerplate goes last, so
    truncate_for_llm's head-and-tail keeps what matters."""
    sections = ((detail.get("jobAd") or {}).get("sections")) or {}
    parts = []
    for key in SECTION_ORDER:
        sec = sections.get(key) or {}
        text = html_to_text(sec.get("text"))
        if text:
            parts.append(f"{sec.get('title') or key}\n{text}")
    return clean_text("\n\n".join(parts))


def _compensation(detail: dict) -> str | None:
    comp = detail.get("compensation") or {}
    lo, hi = comp.get("min") or 0, comp.get("max") or 0
    if not lo and not hi:
        return None                     # the API reports 0/0 when undisclosed
    cur = comp.get("currency") or ""
    per = (comp.get("period") or "").lower()
    span = f"{lo:,}–{hi:,}" if lo and hi else f"{lo or hi:,}"
    return " ".join(p for p in (cur, span, per and f"/ {per}") if p)


def _location(posting: dict) -> str:
    loc = posting.get("location") or {}
    full = loc.get("fullLocation") or ", ".join(
        p for p in (loc.get("city"), loc.get("region")) if p)
    # fullLocation arrives as "Hyderabad, , India" when the region is blank.
    full = ", ".join(p.strip() for p in full.split(",") if p.strip())
    bits = [full]
    if (loc.get("country") or "").lower() == "in" and "india" not in full.lower():
        bits.append("India")           # ISO code: safe to expand
    if loc.get("remote"):
        bits.append("Remote")
    elif loc.get("hybrid"):
        bits.append("Hybrid")
    return ", ".join(dict.fromkeys(b for b in bits if b))


class SmartRecruitersAdapter:
    ats_name = "smartrecruiters"

    def fetch(self, board_token: str) -> list[RawJob]:
        company = quote(board_token.strip(), safe="")
        if not company:
            raise BoardFetchError("empty SmartRecruiters company identifier")
        deadline = time.monotonic() + BOARD_BUDGET_S
        base = API.format(company=company)

        postings: list[dict] = []
        total = 0
        for page in range(MAX_PAGES):
            payload = get_json(f"{base}?country=in&limit={PAGE}&offset={page * PAGE}")
            batch = payload.get("content") or []
            total = int(payload.get("totalFound") or 0)
            postings.extend(batch)
            if len(batch) < PAGE or len(postings) >= total:
                break
        else:
            if len(postings) < total:
                raise BoardFetchError(
                    f"truncated at {len(postings)} of {total} postings "
                    f"({MAX_PAGES} pages x {PAGE}) — raise MAX_PAGES")

        def detail(posting: dict) -> dict:
            if time.monotonic() > deadline:
                raise BoardFetchError(
                    f"exceeded the {BOARD_BUDGET_S}s per-board budget — skipped "
                    f"this run rather than diffed partially", transient=True)
            ref = posting.get("ref") or f"{base}/{posting.get('id')}"
            return get_json(ref)

        try:
            with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
                details = list(pool.map(detail, postings))
        except BoardFetchError as exc:
            # One missing detail must not shorten the board (see module note).
            raise BoardFetchError(f"detail fetch failed: {exc}", transient=True) from exc

        out: list[RawJob] = []
        for posting, d in zip(postings, details):
            job_id = str(posting.get("id") or "")
            if not job_id:
                continue
            out.append(RawJob(
                ats_job_id=job_id,
                title=(posting.get("name") or "").strip(),
                location=_location(posting),
                description=_description(d),
                absolute_url=d.get("postingUrl") or d.get("applyUrl") or "",
                posted_at=parse_iso(posting.get("releasedDate")),
                compensation=_compensation(d),
                raw={
                    "refNumber": posting.get("refNumber"),
                    "experienceLevel": (posting.get("experienceLevel") or {}).get("label"),
                    "function": (d.get("function") or {}).get("label"),
                    "typeOfEmployment": (posting.get("typeOfEmployment") or {}).get("label"),
                },
            ))
        return out
