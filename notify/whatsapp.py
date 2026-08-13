"""CallMeBot WhatsApp ping. One-way, free, no uptime guarantee.

That last part is the design constraint: a silent CallMeBot outage must not cost
a week of jobs, so every failure here is surfaced to the caller and re-sent over
email rather than swallowed.
"""

from __future__ import annotations

import os
from urllib.parse import quote

import requests

ENDPOINT = "https://api.callmebot.com/whatsapp.php"


class NotConfigured(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.environ.get("CALLMEBOT_PHONE") and os.environ.get("CALLMEBOT_APIKEY"))


def format_message(job: dict, dashboard_url: str = "") -> str:
    """Fit score leads so the ping can be triaged without opening anything."""
    score = job.get("fit_score")
    lines = [f"{float(score):.1f} | {job['title']}" if score is not None else job["title"]]

    where = job.get("company") or ""
    if job.get("location"):
        where += f" - {job['location']}"
    lines.append(where)

    age = job.get("age_label") or "age unknown"
    exp = "exp unstated"
    lo, hi = job.get("min_years"), job.get("max_years")
    if lo is not None and hi is not None:
        exp = f"asks {lo:g}-{hi:g}y"
    elif lo is not None:
        exp = f"asks {lo:g}y+"
    lines.append(f"Posted {age} ago | {exp}")

    if job.get("compensation"):
        lines.append(str(job["compensation"]))

    contacts = job.get("referrals") or []
    if contacts:
        batchmates = sum(1 for c in contacts if c.get("is_batchmate"))
        note = f"{len(contacts)} contact{'s' if len(contacts) != 1 else ''} here"
        if batchmates:
            note += f" ({batchmates} batchmate{'s' if batchmates != 1 else ''})"
        lines.append(note)

    if job.get("matched_skills"):
        lines.append("Match: " + ", ".join(job["matched_skills"][:4]))
    if job.get("gap_skills"):
        lines.append("Gap: " + ", ".join(job["gap_skills"][:3]))

    if dashboard_url:
        lines.append(f"{dashboard_url.rstrip('/')}/j/{job['job_id']}")
    else:
        lines.append(job.get("absolute_url", ""))

    return "\n".join(line for line in lines if line)


def send(message: str, timeout: int = 30) -> None:
    """Raises on failure so the caller can fall back to email."""
    phone = os.environ.get("CALLMEBOT_PHONE")
    apikey = os.environ.get("CALLMEBOT_APIKEY")
    if not phone or not apikey:
        raise NotConfigured("CALLMEBOT_PHONE and CALLMEBOT_APIKEY are not set")

    url = (f"{ENDPOINT}?phone={quote(phone)}&text={quote(message)}"
           f"&apikey={quote(apikey)}")
    resp = requests.get(url, timeout=timeout)
    body = (resp.text or "")[:300]

    # CallMeBot answers 200 with an HTML error page for bad keys and for
    # unregistered numbers, so the status code alone proves nothing.
    if resp.status_code != 200:
        raise RuntimeError(f"http {resp.status_code}: {body}")
    lowered = body.lower()
    if "error" in lowered or "not registered" in lowered or "apikey" in lowered and "invalid" in lowered:
        raise RuntimeError(f"callmebot rejected the send: {body}")
