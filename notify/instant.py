"""The instant channels: one ntfy push and one email per match (decided 2026-10-15).

Two channels, both sent for every job, because each covers the other's way of
failing. ntfy.sh is a free push service with no account: the phone app
subscribes to a long random topic (NTFY_TOPIC), which is the only secret --
anyone who knows it can read and post. The email is the record you can search
and forward, and it comes from an address that is NOT the operator's own
Gmail, because Gmail treats self-sent mail as already read and the phone stays
silent. That sender is Resend's shared onboarding@resend.dev (RESEND_API_KEY;
without a domain Resend only delivers to the account's own address, which is
exactly the one recipient here). Without a Resend key the email still goes out
over the Gmail SMTP path, so a missing key costs the notification sound, never
the job.

Subjects start "[JOB 8.4]" -- the fit score -- so the Gmail filter and the lock
screen can both triage without opening anything.

Every send raises on failure; notify/run.py decides what a failure means.
"""

from __future__ import annotations

import os

import requests

from notify import email as email_channel
from notify import whatsapp

NTFY_SERVER = "https://ntfy.sh"
MATCH_EMAIL_FROM = "Job Agent <onboarding@resend.dev>"
MAX_CHARS = 4000


class NotConfigured(RuntimeError):
    pass


def subject(job: dict) -> str:
    """'[JOB 8.4] Backend Engineer - Okta (Bengaluru)'. The prefix is what the
    Gmail filter keys on; keep it stable."""
    score = job.get("fit_score")
    tag = f"[JOB {float(score):.1f}]" if score is not None else "[JOB]"
    where = f" ({job['location']})" if job.get("location") else ""
    line = f"{tag} {job['title']} - {job.get('company') or '?'}{where}"
    return line if len(line) <= 180 else line[:179] + "…"


def format_message(job: dict, dashboard_url: str = "") -> str:
    """Triage-first body: score, company, age, experience asked, skills, then
    the lines that make it actionable from the phone -- which resume, who to
    ask, and the apply link."""
    lines = [whatsapp.format_message(job, dashboard_url)]
    family = job.get("role_family")
    variant = job.get("resume_variant")
    if family and variant:
        lines.append(f"Resume: {family} -> {variant}")
    elif family:
        lines.append(f"Role family: {family}")
    # Who to ask, with a link. PLAN section 2 cites ~40% of referred candidates
    # reaching interview against ~3% cold.
    for c in (job.get("referrals") or [])[:3]:
        who = c.get("full_name") or "?"
        if c.get("title"):
            who += f" ({c['title'][:40]})"
        if c.get("is_batchmate"):
            who += " · batchmate"
        elif c.get("near_batch"):
            who += " · near-batch"
        lines.append(f"Ask: {who}" + (f" {c['profile_url']}" if c.get("profile_url") else ""))
    if job.get("source") and job["source"] != "ats":
        lines.append(f"Source: {job['source']}")
    if job.get("provisional"):
        lines.append("(provisional score — rules only, LLM unavailable)")
    if job.get("absolute_url"):
        lines.append(f"Apply: {job['absolute_url']}")
    text = "\n".join(lines)
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS - 1] + "…"


# --- ntfy -------------------------------------------------------------------

def ntfy_configured() -> bool:
    return bool((os.environ.get("NTFY_TOPIC") or "").strip())


def ntfy_payload(job: dict, dashboard_url: str = "") -> dict:
    """JSON publish, not header publish: headers must be Latin-1 and job titles
    routinely are not."""
    score = float(job.get("fit_score") or 0)
    view = (f"{dashboard_url.rstrip('/')}/j/{job['job_id']}" if dashboard_url
            else job.get("absolute_url") or "")
    payload = {
        "topic": (os.environ.get("NTFY_TOPIC") or "").strip(),
        "title": subject(job),
        "message": format_message(job, dashboard_url),
        # 4 = high: sound and heads-up on Android. 5 would bypass Do Not Disturb.
        "priority": 4 if score >= 8 else 3,
        "tags": ["briefcase"] + (["warning"] if job.get("provisional") else []),
    }
    if view:
        payload["click"] = view
    if job.get("absolute_url"):
        payload["actions"] = [{"action": "view", "label": "Apply",
                               "url": job["absolute_url"]}]
    return payload


def send_ntfy(payload: dict, timeout: int = 20) -> None:
    if not payload.get("topic"):
        raise NotConfigured("NTFY_TOPIC is not set")
    resp = requests.post(NTFY_SERVER, json=payload, timeout=timeout)
    if resp.status_code >= 300:
        # The topic travels in the body only, and ntfy's error does not echo it.
        raise RuntimeError(f"ntfy {resp.status_code}: {resp.text[:200]}")


# --- email ------------------------------------------------------------------

def email_transport() -> str | None:
    """resend (the non-Gmail sender) when keyed, else whatever email.py has."""
    if os.environ.get("RESEND_API_KEY") and os.environ.get("ALERT_EMAIL_TO"):
        return "resend"
    return email_channel.transport()


def send_email(job: dict, dashboard_url: str = "") -> str:
    """-> transport used. Raises on failure."""
    kind = email_transport()
    if kind is None:
        raise NotConfigured("no email transport configured")
    sender = None
    if kind == "resend":
        sender = os.environ.get("MATCH_EMAIL_FROM") or MATCH_EMAIL_FROM
    return email_channel.send(subject(job), format_message(job, dashboard_url),
                              transport=kind, sender=sender)
