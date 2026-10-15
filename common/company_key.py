"""Company identity key — one normaliser for every layer (docs/PLAN.md 5.1).

    from common.company_key import company_key
    company_key("Razorpay Software Private Limited")   # -> "razorpay"

Three things need to agree on what "the same company" means: the referral
join (a LinkedIn employer string vs a tracked board), outcome capture (an
ATS email's sender name or subject vs an application), and the dashboard.
Until Phase 5.1 the dashboard carried its own shorter copy of this noise list
in TypeScript; the measured cost on 2026-10-15 happened to be zero, but the
two had already drifted (the TS list lacked "software", "systems",
"private limited" and more).

Stored as companies.company_key (db/migrations/0009) and, for contacts, as
connections.company_norm, which was always written by this same function.
Nothing else may re-implement it: scripts/check_boundaries.py fails if the
noise list appears under dashboard/.

Moved verbatim from score/referral.py, which re-exports it.
"""

from __future__ import annotations

import re
import unicodedata

# Legal and geographic noise that carries no identity. Order matters only in
# that multi-word phrases must be stripped before their component words.
NOISE = re.compile(
    r"\b("
    r"global tech(nology|nologies)?|development cent(er|re)|"
    r"r ?& ?d|research and development|"
    r"technologies|technology|solutions|services|systems|labs|laboratories|"
    r"software|consulting|holdings|ventures|group|"
    r"india|bharat|"
    r"private limited|pvt ltd|private|limited|pvt|ltd|llc|llp|inc|incorporated|"
    r"corp|corporation|co|company|plc|gmbh|sa|nv|bv|ag|"
    r"gcc|global capability cent(er|re)|global in house cent(er|re)"
    r")\b",
    re.I,
)

# Single-token names that become empty or dangerously generic after stripping.
TOO_GENERIC = {"", "the", "tech", "global", "digital", "data", "cloud", "ai"}


def normalize_company(raw: str | None) -> str:
    """Reduce an employer string to its identity core.

    'Walmart Global Tech India' -> 'walmart'
    'Razorpay Software Private Limited' -> 'razorpay'
    'Zeta Suite (Zeta India)' -> 'zeta suite zeta'
    """
    if not raw:
        return ""
    text = unicodedata.normalize("NFKD", raw)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    # Drop anything parenthesised only if content remains without it.
    stripped = re.sub(r"\([^)]*\)", " ", text)
    if stripped.strip():
        text = stripped
    text = re.sub(r"[^a-z0-9& ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    previous = None
    while previous != text:            # phrases can nest: "technologies india"
        previous = text
        text = NOISE.sub(" ", text)
        text = re.sub(r"\s+", " ", text).strip()

    return text


def company_key(raw: str | None) -> str:
    """The stored identity key. Same as normalize_company; the name says what
    it is for."""
    return normalize_company(raw)


def keys_in_text(text: str | None, keys: set[str]) -> set[str]:
    """Every known company key that appears in `text` as a whole-word phrase.

    Used to read which company an ATS email is about from its sender name and
    subject ("Thank you for applying to Okta" -> {"okta"}). The text is
    normalised the same way as the keys, so legal suffixes on either side do
    not matter. Generic keys are never matched: "data" or "cloud" would be
    found in half of all mail.
    """
    hay = f" {normalize_company(text)} "
    if not hay.strip():
        return set()
    return {k for k in keys
            if k and k not in TOO_GENERIC and f" {k} " in hay}
