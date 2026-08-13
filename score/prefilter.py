"""LAYER 2, stage 1 — drop the obvious rejects before spending an LLM call.

Roughly 70% of ingested volume dies here, which is what keeps Groq usage inside
the free tier. Everything in this file is a pure function over strings so it can
be calibrated against real postings without a database or an API key:

    python -m score.prefilter --live          # run against live boards
    python -m score.prefilter --live --show-passes

Calibration bias: the operator has ~1.3 years experience and explicitly prefers
volume over missed opportunities. The sub-2-year filter is the thing this whole
system exists to test, so this stage must not enforce it more strictly than
employers do. It rejects only what is *unambiguously* out of range — a stated
minimum above 4 years, or a title that is plainly senior. Anything uncertain is
passed through and priced by the LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --- title rules --------------------------------------------------------

# Word boundaries everywhere. Bare "ai"/"ml" as substrings would match "email",
# "retail", "html"; bare "lead" would match "leadership".
SENIOR_TITLE = re.compile(
    r"\b(?:staff|principal|senior|sr\.?|lead|leader|manager|director|"
    r"head\s+of|architect|intern|interns|internship|vp|vice\s+president|"
    r"president|chief|fellow|distinguished)\b",
    re.I,
)

# "Member of Technical Staff" is an ordinary IC title at several of the tracked
# companies — Rubrik, Zeta, Databricks — and is frequently entry level. Matching
# it on "staff" would reject some of the best-fitting reqs on the board.
MTS = re.compile(r"member\s+of\s+(?:the\s+)?technical\s+staff", re.I)

TARGET_FUNCTION = re.compile(
    r"\b(?:engineer|engineering|developer|development|sde|swe|scientist|"
    r"ml|ai|gen\s?ai|llm|data|backend|back-end|platform|software|"
    r"infrastructure|systems?|full\s?stack|fullstack|mts)\b",
    re.I,
)

# Checked *before* TARGET_FUNCTION, because these all satisfy it by accident.
# "Business Development" and "Account Development Representative" match on
# "development"; "Technical Support Engineer" and "Solutions Engineer" match on
# "engineer". Without this list the survivors are majority sales and support.
HARD_OFF_FUNCTION = re.compile(
    r"\b(?:business|account|sales|corporate|market|partner|talent|learning)\s+"
    r"(?:&\s+)?development\b"
    r"|\b(?:account|sales)\s+(?:executive|representative)\b"
    r"|\bsolutions?\s+(?:engineer|architect|consultant|specialist)\b"
    r"|\bcustomer\s+(?:engineer|success|support)\b"
    r"|\b(?:technical|product|customer|designated)\s+support\s+engineer\b"
    r"|\btechnical\s+services\s+engineer\b"
    r"|\b(?:sales|field|presales|pre-sales)\s+engineer\b"
    r"|\bbusiness\s+systems?\s+analyst\b"
    r"|\btalent\s+acquisition\b"
    r"|\boperations\s+associate\b",
    re.I,
)

# Business-domain words. On their own they mean a finance or marketing job, but
# fintech backend reqs are routinely titled "Analytics Engineer - Finance" or
# "Software Engineer, Treasury Platform" — so these only disqualify a title that
# carries no engineering role noun at all.
DOMAIN_OFF_FUNCTION = re.compile(
    r"\b(?:treasury|finance|financial|payroll|accounting|marketing|recruiting|"
    r"procurement|legal|compensation|brand)\b",
    re.I,
)

STRONG_ENGINEERING = re.compile(
    r"\b(?:engineer|engineering|developer|sde|swe|scientist|mts|programmer)\b",
    re.I,
)

# --- experience extraction ---------------------------------------------

NUM = r"(\d{1,2})"
YEARS = r"(?:\+\s*)?(?:years?|yrs?)"
RANGE = re.compile(rf"{NUM}\s*\+?\s*(?:-|–|—|to)\s*{NUM}\s*{YEARS}", re.I)
# "5 years to 13Years" — the unit repeated on both ends. Common in Indian
# enterprise reqs, and missing it lets 5-13 year roles through the gate.
RANGE_BOTH_UNITS = re.compile(
    rf"{NUM}\s*{YEARS}\s*(?:-|–|—|to)\s*{NUM}\s*{YEARS}", re.I
)
AT_LEAST = re.compile(
    rf"(?:minimum(?:\s+of)?|at\s+least|min\.?|over|more\s+than)\s+{NUM}\s*\+?\s*{YEARS}",
    re.I,
)
PLUS = re.compile(rf"{NUM}\s*\+\s*{YEARS}", re.I)
PLAIN = re.compile(rf"{NUM}\s*{YEARS}", re.I)

# A "years" figure only counts as a requirement if experience is being discussed
# nearby. JDs are full of unrelated ones: "10 years of category leadership",
# "grown 3x in 2 years".
EXPERIENCE_CONTEXT = re.compile(
    r"experience|exp\b|yoe|professional|industry|hands[-\s]?on|background|"
    r"working|worked|building|developing|software|engineering|relevant|"
    r"track\s+record|proven",
    re.I,
)
NOT_A_REQUIREMENT = re.compile(
    r"ago\b|history|founded|anniversary|past\s+\d|next\s+\d|over\s+the\s+years|"
    r"in\s+the\s+last|year[-\s]over[-\s]year|per\s+year|visa|notice\s+period|"
    r"grown|revenue|customers|funding",
    re.I,
)

CONTEXT_WINDOW = 70
MAX_PLAUSIBLE_YEARS = 15


@dataclass
class Prefiltered:
    passed: bool
    reject_reason: str | None = None
    min_years: float | None = None
    max_years: float | None = None


def _candidates(text: str, require_context: bool = True) -> list[tuple[float, float | None]]:
    """Every (min, max) experience requirement the text plausibly states.

    `require_context` is relaxed for job titles: a years figure in a title is
    always the requirement ("Software Engineer - 5 years to 13Years"), and real
    titles carry none of the surrounding prose the body-text heuristic needs.
    """
    found: list[tuple[float, float | None]] = []
    seen_spans: list[tuple[int, int]] = []

    def consider(match: re.Match, low: float, high: float | None) -> None:
        start, end = match.span()
        # A range match and a plain match overlap on the same digits; keep the
        # richer one, which is whichever pattern was tried first.
        if any(s <= start < e or s < end <= e for s, e in seen_spans):
            return
        if low > MAX_PLAUSIBLE_YEARS:
            return
        window = text[max(0, start - CONTEXT_WINDOW): end + CONTEXT_WINDOW]
        if require_context and not EXPERIENCE_CONTEXT.search(window):
            return
        if NOT_A_REQUIREMENT.search(window):
            return
        seen_spans.append((start, end))
        found.append((low, high))

    # Both-units first: it spans the widest text, and `consider` keeps whichever
    # pattern claims a span first, so the richer reading wins.
    for pattern in (RANGE_BOTH_UNITS, RANGE):
        for match in pattern.finditer(text):
            low, high = float(match.group(1)), float(match.group(2))
            consider(match, min(low, high), max(low, high))
    for match in AT_LEAST.finditer(text):
        consider(match, float(match.group(1)), None)
    for match in PLUS.finditer(text):
        consider(match, float(match.group(1)), None)
    for match in PLAIN.finditer(text):
        consider(match, float(match.group(1)), float(match.group(1)))
    return found


def extract_experience(text: str, require_context: bool = True
                       ) -> tuple[float | None, float | None]:
    """-> (min_years, max_years), either may be None if unstated.

    When a JD states several figures ("3+ years backend, 5+ years distributed
    systems") the lowest is taken. Over-reading the requirement would reject reqs
    the operator should be applying to, which is the exact failure this system
    exists to avoid.
    """
    found = _candidates(text or "", require_context)
    if not found:
        return None, None
    low = min(f[0] for f in found)
    highs = [f[1] for f in found if f[0] == low and f[1] is not None]
    return low, (max(highs) if highs else None)


def check_title(title: str) -> str | None:
    """-> reject_reason, or None if the title is worth reading further."""
    if not title.strip():
        return "empty_title"
    if HARD_OFF_FUNCTION.search(title):
        return "title_off_function"
    if DOMAIN_OFF_FUNCTION.search(title) and not STRONG_ENGINEERING.search(title):
        return "title_off_function"

    # MTS is handled before the function and seniority checks: the bare title
    # contains no engineering noun to match on, and its "staff" would otherwise
    # trip the seniority rule. Only the phrase itself is exempt, so a genuine
    # "Senior Member of Technical Staff" is still rejected on the remainder.
    if MTS.search(title):
        match = SENIOR_TITLE.search(MTS.sub(" ", title))
        return f"title_seniority:{match.group(0).lower().strip()}" if match else None

    if not TARGET_FUNCTION.search(title):
        return "title_off_function"
    match = SENIOR_TITLE.search(title)
    if match:
        return f"title_seniority:{match.group(0).lower().strip()}"
    return None


def prefilter(title: str, description: str, *, location_ok: bool = True,
              max_experience_years: float = 4.0,
              already_scored: bool = False) -> Prefiltered:
    """The full stage-1 gate. Order matters only for which reason gets recorded;
    the cheapest and most certain checks run first."""
    if already_scored:
        return Prefiltered(False, "already_scored")
    if not location_ok:
        return Prefiltered(False, "location")

    reason = check_title(title)
    if reason:
        return Prefiltered(False, reason)

    # A requirement stated in the title wins outright. "Thermal Engineer - 7 to
    # 10 yrs" is unambiguous, and the lowest-figure-wins rule used for body text
    # would otherwise let any stray "2 years" elsewhere in the JD override it.
    # Body text only decides when the title is silent, where the rule is still
    # to take the lowest figure so an in-range job is never rejected.
    title_min, title_max = extract_experience(title, require_context=False)
    if title_min is not None:
        min_years, max_years = title_min, title_max
    else:
        min_years, max_years = extract_experience(description)
    if min_years is not None and min_years > max_experience_years:
        return Prefiltered(False, f"experience:{min_years:g}y_min",
                           min_years, max_years)

    return Prefiltered(True, None, min_years, max_years)


# --- calibration harness ------------------------------------------------

def _live(show_passes: bool, limit: int | None) -> int:
    """Run the prefilter over live boards and print the survival rate.

    This is the only honest way to tune the patterns: synthetic examples always
    look fine, and real JDs are where "10 years of category leadership" lives.
    """
    import argparse  # noqa: F401  (kept local; this block is diagnostics only)
    from collections import Counter
    from pathlib import Path

    import yaml

    from ingest.adapters import get_adapter
    from ingest.normalize import is_india_relevant

    root = Path(__file__).resolve().parents[1]
    settings = yaml.safe_load((root / "config" / "settings.yaml").read_text(encoding="utf-8"))
    boards = yaml.safe_load((root / "seeds" / "companies.verified.yaml").read_text(encoding="utf-8"))
    max_years = float(settings.get("max_experience_years", 4))

    reasons: Counter[str] = Counter()
    passes: list[tuple[str, str, str]] = []
    total = 0

    for board in boards:
        try:
            jobs = get_adapter(board["ats"]).fetch(str(board["token"]))
        except Exception as exc:
            print(f"  skip {board['name']}: {exc}")
            continue
        for job in jobs:
            if not is_india_relevant(job.location, job.description):
                continue
            total += 1
            result = prefilter(job.title, job.description, max_experience_years=max_years)
            if result.passed:
                reasons["PASSED"] += 1
                exp = (f"{result.min_years:g}-{result.max_years:g}y"
                       if result.min_years is not None and result.max_years is not None
                       else f"{result.min_years:g}y+" if result.min_years is not None
                       else "unstated")
                passes.append((board["name"], job.title, exp))
            else:
                reasons[result.reject_reason.split(":")[0]] += 1
        if limit and total >= limit:
            break

    kept = reasons["PASSED"]
    print(f"\n{total} India-relevant postings -> {kept} passed "
          f"({kept / total * 100:.0f}%), {total - kept} rejected\n")
    for reason, count in reasons.most_common():
        print(f"  {count:>4}  {reason}")

    if show_passes:
        print(f"\n--- {len(passes)} survivors ---")
        for company, title, exp in sorted(passes):
            print(f"  {company:<16} {exp:<10} {title}")
    return 0


if __name__ == "__main__":
    import argparse
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true", help="run against live boards")
    ap.add_argument("--show-passes", action="store_true", help="list every survivor")
    ap.add_argument("--limit", type=int, help="stop after roughly N postings")
    args = ap.parse_args()
    if not args.live:
        ap.print_help()
        raise SystemExit(1)
    raise SystemExit(_live(args.show_passes, args.limit))
