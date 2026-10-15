"""The India location gate — one matcher, shared by every layer.

    from common.geo import is_india_relevant, names_india

Phase 1.1 of docs/PLAN.md (gap G3). Until now the same question — "does this
string name a place in India?" — was answered by three separate regexes that
had already drifted apart:

    ingest/normalize.py        11 city terms, the real gate
    ingest/adapters/workday.py 11 city terms, for picking location facet ids
    scripts/verify_boards.py    5 city terms, a diagnostic

and a fourth list, `location_keywords` in config/settings.yaml, that was read
by nothing at all. Adding a city meant finding all four. Phase 6 adds four more
adapters, which would have made it eight places.

The vocabulary now lives in config/settings.yaml and is loaded once here.

**What widening actually bought, measured 2026-10-15** across all 120 verified
boards fetched with no filter (14,294 postings; the gate kept 2,264):

    Delhi        10 postings   Sarvam AI, InMobi
    Lucknow       6 postings   InMobi, Razorpay
    Kolkata       1 posting    Razorpay
    ----------------------------------------
    total        17 postings   (0.12% of everything fetched)

Delhi is the genuine bug: the old list carried the NCR satellites — Gurgaon,
Gurugram, Noida — but not the city they are satellites of. Lucknow and Kolkata
are ordinary omissions.

Every other city in this list recovered **zero** postings on the current
roster: Ahmedabad, Coimbatore, Thiruvananthapuram, Kochi, Indore, Nagpur,
Jaipur, Bhubaneswar, Vadodara, Mysuru and Chandigarh are not absent because the
regex missed them, they are absent because no board in the roster hires there.
They are kept as cheap insurance for Phase 6, which adds the India-HQ employers
that do — not as a fix for anything today. docs/STATE.md G3 claimed this gate
was discarding 42% of Target's Workday board; that was wrong, and DECISIONS.md
records why.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

import yaml

SETTINGS = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"

# Used only if config/settings.yaml is missing or has no location_keywords. The
# gate must never silently become "match nothing", which would close every open
# job on the next run, nor "match everything", which would spend LLM budget on
# the whole planet. A wrong-but-sane default is the safer failure.
FALLBACK_KEYWORDS = (
    "india", "bengaluru", "bangalore", "hyderabad", "pune", "chennai",
    "mumbai", "delhi", "noida", "gurgaon", "gurugram", "karnataka",
)

# A posting is remote-and-India only if the description also names India.
# "Remote" alone is overwhelmingly Remote-US.
REMOTE = re.compile(r"\bremote\b|\banywhere\b|\bworldwide\b|\bglobal\b", re.I)

# How much of the description to search for an India mention on a remote
# posting. The office is named early if it is named at all, and scanning whole
# JDs made the gate measurably slower for no additional matches.
DESCRIPTION_SCAN_CHARS = 1500


def _load_keywords() -> tuple[str, ...]:
    try:
        raw = yaml.safe_load(SETTINGS.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return FALLBACK_KEYWORDS
    words = raw.get("location_keywords")
    if not isinstance(words, list):
        return FALLBACK_KEYWORDS
    cleaned = tuple(
        str(w).strip().lower() for w in words if str(w or "").strip()
    )
    return cleaned or FALLBACK_KEYWORDS


@functools.lru_cache(maxsize=1)
def india_pattern() -> re.Pattern[str]:
    """One alternation of whole-word keywords, longest first.

    Whole-word matching is what keeps "India" out of "Indiana" and
    "Indianapolis" — 232 postings on Target's state facet alone — because the
    `n` after `india` is a word character and `\\b` fails there. It is also why
    the bare country code "IN" must never be a keyword: under re.I it would
    match the English word "in" and admit every remote-US posting in the
    corpus.
    """
    words = sorted(set(_load_keywords()), key=len, reverse=True)
    return re.compile(
        "|".join(r"\b" + re.escape(w) + r"\b" for w in words), re.I
    )


def names_india(text: str | None) -> bool:
    """True if `text` names an Indian location.

    The raw predicate, with no remote or empty-string policy attached. Used for
    matching ATS location-facet labels, where the input is a facet name rather
    than a posting's location.
    """
    if not text:
        return False
    return india_pattern().search(text) is not None


def is_remote(text: str | None) -> bool:
    return bool(text) and REMOTE.search(text) is not None


def is_india_relevant(location: str | None, description: str = "") -> bool:
    """The Layer 1 location gate (spec §6.2).

    Postings with no location string at all are kept: several boards omit it
    and the description usually names the office. The Layer 2 prefilter sees
    the description and can still drop them.
    """
    loc = (location or "").strip()
    if not loc:
        return True
    if names_india(loc):
        return True
    if is_remote(loc) and names_india(
        f"{loc} {(description or '')[:DESCRIPTION_SCAN_CHARS]}"
    ):
        return True
    return False


def _reset_cache_for_tests() -> None:
    """Drop the compiled pattern so a test can swap the keyword list."""
    india_pattern.cache_clear()
    _preferred.cache_clear()

# --- the operator's city (Layer 2 preference; decided 2026-10-16) -------------
#
# is_india_relevant stays the Layer 1 gate: the public board is India-wide.
# Which city the operator will actually work in is a preference, applied only
# by the scoring and delivery layers (score/run.py, notify/, the dashboard via
# job_scores.location_ok). Settings: config/settings.yaml:preferred_location.

FALLBACK_PREFERRED = ("bengaluru", "bangalore", "blr", "karnataka", "ka")


def _load_preferred() -> tuple[tuple[str, ...], bool]:
    try:
        raw = (yaml.safe_load(SETTINGS.read_text(encoding="utf-8")) or {}).get(
            "preferred_location") or {}
    except (OSError, yaml.YAMLError):
        raw = {}
    words = tuple(str(w).strip().lower() for w in raw.get("keywords") or []
                  if str(w or "").strip()) or FALLBACK_PREFERRED
    return words, bool(raw.get("include_remote_india", True))


@functools.lru_cache(maxsize=1)
def _preferred() -> tuple[re.Pattern[str], bool]:
    words, remote = _load_preferred()
    words = sorted(set(words), key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.I), remote


def in_preferred_location(location: str | None, description: str = "") -> bool:
    """True if a posting can be worked from the operator's city.

    The city named anywhere in the location string counts, so a multi-city
    Workday posting that lists Bangalore among five passes. "KA, India" is how
    Indeed writes Bengaluru. Remote-in-India passes when include_remote_india
    is on, since it can be done from Bengaluru. With no location string the
    description decides, as it does for the India gate.
    """
    pattern, remote_ok = _preferred()
    loc = (location or "").strip()
    head = (description or "")[:DESCRIPTION_SCAN_CHARS]
    if not loc:
        return pattern.search(head) is not None
    if pattern.search(loc):
        return True
    if remote_ok and is_remote(loc) and is_india_relevant(loc, description):
        return True
    return False
