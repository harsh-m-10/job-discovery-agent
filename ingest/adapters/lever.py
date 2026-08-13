"""LAYER 1. Lever postings API.

    GET https://api.lever.co/v0/postings/{token}?mode=json

Server-side filtering by location exists but the strings are inconsistent across
tenants, so the whole board is fetched and filtered locally instead — one
consistent code path, and location filtering stays in normalize.py.
"""

from __future__ import annotations

from ..http import get_json
from ..models import RawJob
from ..normalize import clean_text, html_to_text, parse_epoch_ms

URL = "https://api.lever.co/v0/postings/{token}?mode=json"


def _description(posting: dict) -> str:
    """Lever splits a JD across several fields. `lists` holds the bulleted
    requirements sections, which is exactly where the years-of-experience line
    lives — dropping it would blind the prefilter."""
    parts = [
        posting.get("openingPlain") or "",
        posting.get("descriptionPlain") or html_to_text(posting.get("description")),
    ]
    body = posting.get("descriptionBodyPlain") or html_to_text(posting.get("descriptionBody"))
    if body and body not in parts[1]:
        parts.append(body)
    for section in posting.get("lists") or []:
        parts.append(section.get("text") or "")
        parts.append(html_to_text(section.get("content")))
    parts.append(posting.get("additionalPlain") or html_to_text(posting.get("additional")))
    return clean_text("\n\n".join(p.strip() for p in parts if p and p.strip()))


class LeverAdapter:
    ats_name = "lever"

    def fetch(self, board_token: str) -> list[RawJob]:
        payload = get_json(URL.format(token=board_token))
        out: list[RawJob] = []
        for posting in payload or []:
            job_id = str(posting.get("id") or "")
            if not job_id:
                continue
            cats = posting.get("categories") or {}
            bits = [
                cats.get("location") or "",
                ", ".join(cats.get("allLocations") or []),
                # Lever's country is a real ISO code, unlike a free-text location
                # string, so "IN" can be expanded safely here.
                "India" if (posting.get("country") or "").upper() == "IN" else "",
                posting.get("workplaceType") or "",
            ]
            location = ", ".join(dict.fromkeys(b for b in bits if b))
            out.append(
                RawJob(
                    ats_job_id=job_id,
                    title=(posting.get("text") or "").strip(),
                    location=location.strip(", "),
                    description=_description(posting),
                    absolute_url=posting.get("hostedUrl") or posting.get("applyUrl") or "",
                    posted_at=parse_epoch_ms(posting.get("createdAt")),
                    raw={
                        "department": cats.get("department"),
                        "team": cats.get("team"),
                        "commitment": cats.get("commitment"),
                        "workplaceType": posting.get("workplaceType"),
                        "country": posting.get("country"),
                    },
                )
            )
        return out
