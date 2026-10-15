"""What kind of outcome an ATS email reports. Pure, no I/O.

The order of the checks is the substance here, because the same words appear
in emails that mean opposite things:

  1. Definitive rejection phrases ("move forward with other candidates",
     "position has been filled"). Nothing else uses them.
  2. Acknowledgements written in the conditional -- "if your profile is a
     match, we will contact you to schedule an interview". They contain the
     word "interview" but promise nothing; reading them as invites would turn
     every application receipt into a fake interview.
  3. Assessments (HackerRank, Codility, "take-home"). Before interviews,
     because an assessment email often also mentions the interview after it.
  4. Interview invitations, including reschedules.
  5. Weak rejection words ("unfortunately", "we regret"). After interviews,
     because "unfortunately the interviewer is unavailable, could you share
     new times" is a reschedule, not a rejection.
  6. Plain acknowledgements.
  7. Anything else.
"""

from __future__ import annotations

import re

KINDS = ("rejection", "interview_invite", "assessment", "acknowledgement", "other")


def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(parts), re.I)


STRONG_REJECTION = _rx(
    r"\b(not|won'?t|will not|decided not to|unable to) (be )?(mov(e|ing)|proceed(ing)?|progress(ing)?) "
    r"(forward|further|with your (application|candidacy))",
    r"\b(pursue|proceed with|move forward with|go(ing)? with) (other|another) (candidate|applicant)s?",
    r"\bother candidates (whose|who)\b",
    r"\b(have|has) not been selected\b", r"\bnot (been )?shortlisted\b",
    r"\bposition (has|have) (now )?been filled\b", r"\brole (has|have) (now )?been filled\b",
    r"\bno longer (under consideration|being considered|considering)\b",
    r"\bdecided not to proceed\b", r"\bunable to offer you\b",
    r"\bnot (a|the right) (match|fit) for (this|the) (role|position)\b",
    r"\b(will|would) not be (offering|extending)\b",
    r"\bdecline(d)? (your|the) application\b",
)

CONDITIONAL = _rx(
    r"\bif (you are|you're|your (profile|experience|background|skills)( is| are)?) "
    r"(selected|shortlisted|a (good )?(match|fit))",
    r"\bshould (your|you) (profile|experience|background|qualifications)?\s*"
    r"(match|be a match|meet|be selected)",
    r"\bwe will (be in touch|reach out|contact you|get back to you) (if|should|when)",
    r"\bif (we|there is a) (match|fit|wish to proceed|would like to proceed)",
)

ASSESSMENT = _rx(
    r"\bhackerrank\b", r"\bcodility\b", r"\bcodesignal\b", r"\bhackerearth\b",
    r"\btestgorilla\b", r"\bcoderbyte\b", r"\bkarat\b",
    r"\bcoding (challenge|test|exercise|assessment|round)\b",
    r"\btake[- ]home\b", r"\bonline (test|assessment)\b",
    r"\b(technical|skills?) assessment\b", r"\bassessment (link|invitation|invite)\b",
    r"\bcomplete (the|this|an|your) assessment\b",
)

INTERVIEW = _rx(
    r"\binterview (invitation|invite|request|confirmation|scheduled|schedule)\b",
    r"\b(invite|invited|inviting) you (to|for) (an?|the|a first|a technical|our)?\s*"
    r"(interview|conversation|call|chat|discussion)",
    r"\bschedul(e|ing) (an?|your|the|a quick|a short)?\s*(interview|call|chat|conversation|meeting|screen)",
    r"\bphone screen\b", r"\bcalendly\.com\b", r"\bgoodtime\.io\b",
    r"\b(book|pick|choose|select) a (time|slot)\b",
    r"\b(share|send|provide) (your|some) availability\b",
    r"\breschedul", r"\bnext round\b",
    r"\binterview (with|for) (the|our)\b",
)

WEAK_REJECTION = _rx(r"\bunfortunately\b", r"\bwe regret\b", r"\bregret to inform\b",
                     r"\bunsuccessful\b")

ACKNOWLEDGEMENT = _rx(
    r"\bthank(s| you) for (applying|your application|your interest|submitting)",
    r"\b(we('ve| have)|has been) received\b", r"\breceived your application\b",
    r"\bapplication (has been |was )?(received|submitted)\b",
    r"\byour application (to|for)\b",
)


def classify(sender: str | None, subject: str | None, snippet: str | None) -> str:
    """One of KINDS. The sender is not used for the verdict -- the same ATS
    domain sends every kind -- but is accepted so callers pass one record."""
    text = f"{subject or ''}\n{snippet or ''}"
    if not text.strip():
        return "other"
    if STRONG_REJECTION.search(text):
        return "rejection"
    if CONDITIONAL.search(text) and ACKNOWLEDGEMENT.search(text):
        return "acknowledgement"
    if ASSESSMENT.search(text):
        return "assessment"
    if INTERVIEW.search(text):
        return "interview_invite"
    if WEAK_REJECTION.search(text):
        return "rejection"
    if ACKNOWLEDGEMENT.search(text):
        return "acknowledgement"
    return "other"
