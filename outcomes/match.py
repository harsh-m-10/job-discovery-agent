"""Which application an outcome email is about. Pure, no I/O.

ATS mail rarely names the job unambiguously, but it nearly always names the
company: in the sender's display name ("Okta Recruiting"), the subject ("Your
application to Glean"), the sender's own domain (no-reply@razorpay.com), or,
for Workday, the mailbox (cisco@myworkday.com). The company is read with the
same key the referral join uses (common/company_key.py); when one company has
several live applications the job title breaks the tie, and when nothing
breaks it the email goes to the operator's review queue rather than being
guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parseaddr

from common.company_key import keys_in_text

# Domains that belong to the ATS, not the employer: their label names no one.
ATS_DOMAINS = {
    "greenhouse", "greenhouse-mail", "lever", "hire", "ashbyhq", "myworkday",
    "workday", "smartrecruiters", "successfactors", "workablemail", "workable",
    "darwinbox", "keka", "icims", "jobvite", "taleo", "eightfold", "gmail",
    "googlemail", "outlook", "mail", "email", "notifications", "noreply",
}
# Workday mails as <tenant>@myworkday.com: the local part is the employer.
LOCAL_PART_IS_EMPLOYER = {"myworkday.com"}

WINDOW_BEFORE = timedelta(days=120)    # an outcome later than this is not ours
WINDOW_AFTER = timedelta(days=1)       # clock skew, and same-day acks

# Words in a job title that say nothing about which job it is.
GENERIC_TITLE_WORDS = {
    "engineer", "engineering", "software", "senior", "junior", "staff", "lead",
    "developer", "development", "the", "and", "for", "with", "india", "remote",
    "member", "technical", "sde", "intermediate", "associate", "principal",
}


@dataclass(frozen=True)
class App:
    id: int
    status: str
    applied_at: datetime | None
    company_key: str
    title: str


@dataclass(frozen=True)
class Match:
    application_id: int | None
    state: str                 # matched | needs_review | unmatched | ignored
    candidates: tuple[int, ...]
    companies: tuple[str, ...]


def sender_text(sender: str | None) -> str:
    """Display name plus whatever of the address names the employer."""
    name, address = parseaddr(sender or "")
    parts = [name]
    if "@" in address:
        local, domain = address.lower().rsplit("@", 1)
        if domain in LOCAL_PART_IS_EMPLOYER:
            parts.append(local)
        label = domain.split(".")[0]
        if label not in ATS_DOMAINS:
            parts.append(label)
    return " ".join(p for p in parts if p)


def _title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", title.lower())
    return {w for w in words if len(w) >= 3 and w not in GENERIC_TITLE_WORDS}


def match(sender: str | None, subject: str | None, snippet: str | None,
          received_at: datetime, kind: str, apps: list[App]) -> Match:
    text = f"{sender_text(sender)}\n{subject or ''}\n{(snippet or '')[:400]}"
    live = [a for a in apps if a.status not in ("queued", "skipped") and a.applied_at]
    found = keys_in_text(text, {a.company_key for a in live if a.company_key})

    candidates = [
        a for a in live
        if a.company_key in found
        and received_at - WINDOW_BEFORE <= a.applied_at <= received_at + WINDOW_AFTER
    ]
    ids = tuple(sorted(a.id for a in candidates))
    companies = tuple(sorted(found))

    if len(candidates) == 1:
        return Match(candidates[0].id, "matched", ids, companies)

    if len(candidates) > 1:
        # Tie-break on the distinctive words of the job title.
        haystack = set(re.findall(r"[a-z0-9]+", f"{subject or ''} {snippet or ''}".lower()))
        scored = []
        for a in candidates:
            tokens = _title_tokens(a.title)
            if tokens:
                scored.append((len(tokens & haystack) / len(tokens), a.id))
        scored.sort(reverse=True)
        if scored and scored[0][0] >= 0.6 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            return Match(scored[0][1], "matched", ids, companies)
        # Several live applications at one company and nothing to tell them
        # apart. An acknowledgement is not worth the operator's time; anything
        # that changes an outcome is.
        state = "ignored" if kind in ("acknowledgement", "other") else "needs_review"
        return Match(None, state, ids, companies)

    # No candidate. Either the company is not one we applied to, or the
    # application was made outside the dashboard. Only outcomes worth knowing
    # about are kept for review.
    state = "unmatched" if kind in ("rejection", "interview_invite", "assessment") else "ignored"
    return Match(None, state, ids, companies)
