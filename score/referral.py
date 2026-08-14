"""LAYER 2 — company-name normalization and contact matching.

Joining a LinkedIn connection's free-text employer to a tracked ATS board is the
hard part of the referral feature. "Walmart Global Tech India" and "Walmart" are
the same company; "Walmart" and "Walgreens" are not, and token-overlap scoring
is happy to confuse them. Normalization runs first and does most of the work;
fuzzy matching is only a fallback, deliberately tight.

Pure functions over strings — no DB, no network — so the matching rules can be
tested against synthetic data before any real export is touched.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

try:
    from rapidfuzz import fuzz
except ImportError:  # fuzzy matching is a bonus, exact matching still works
    fuzz = None

FUZZY_THRESHOLD = 85

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


@dataclass
class Contact:
    id: int
    full_name: str
    company_raw: str | None
    company_norm: str
    title: str | None
    connected_on: date | None
    is_batchmate: bool = False
    profile_url: str | None = None


@dataclass
class Match:
    contact: Contact
    method: str          # "exact" | "fuzzy"
    confidence: int      # 100 for exact, else the fuzzy ratio


# Role words that make a contact useful for an engineering referral. Used only
# for ranking, never for filtering — a non-engineer at the company still beats
# no contact at all.
ENGINEERING_HINT = re.compile(
    r"\b(engineer|engineering|developer|sde|swe|architect|technical|tech|"
    r"software|data|ml|ai|platform|backend|infrastructure|devops|sre|"
    r"scientist|manager|lead|principal|staff|director|cto|vp)\b",
    re.I,
)


def _title_affinity(title: str | None, target_role: str | None) -> int:
    """0-100. Higher when the contact can actually speak to the role."""
    if not title:
        return 0
    score = 30 if ENGINEERING_HINT.search(title) else 0
    if target_role and fuzz is not None:
        score += int(fuzz.token_set_ratio(title.lower(), target_role.lower()) * 0.7)
    return min(score, 100)


def match_company(company: str, contacts: list[Contact]) -> list[Match]:
    """Find contacts employed at `company`.

    Exact normalized equality first. Fuzzy is a fallback and requires a high
    token_set_ratio, because a loose threshold here produces confident-looking
    nonsense — the failure mode is messaging a stranger about a job at a company
    they have never worked for.
    """
    target = normalize_company(company)
    if target in TOO_GENERIC:
        return []

    exact = [Match(c, "exact", 100) for c in contacts if c.company_norm == target]
    if exact:
        return exact
    if fuzz is None:
        return []

    out: list[Match] = []
    for contact in contacts:
        if not contact.company_norm or contact.company_norm in TOO_GENERIC:
            continue
        ratio = int(fuzz.token_set_ratio(target, contact.company_norm))
        if ratio < FUZZY_THRESHOLD:
            continue
        # token_set_ratio treats a subset as a perfect match, so "walmart" scores
        # 100 against "walmart labs" but also flatters short unrelated strings.
        # Requiring a shared leading token kills the worst of those.
        if target.split()[0] != contact.company_norm.split()[0]:
            continue
        out.append(Match(contact, "fuzzy", ratio))
    return out


def rank_matches(matches: list[Match], target_role: str | None = None,
                 limit: int = 3) -> list[Match]:
    """Batchmate first, then role affinity, then recency of connection.

    Batchmates outrank everyone because a peer from the same graduating cohort
    replies far more often than a senior stranger at the same company.
    """
    def key(m: Match):
        return (
            0 if m.contact.is_batchmate else 1,
            -_title_affinity(m.contact.title, target_role),
            -(m.contact.connected_on.toordinal() if m.contact.connected_on else 0),
            -m.confidence,
        )

    return sorted(matches, key=key)[:limit]
