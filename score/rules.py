"""LAYER 2. Deterministic fallback scorer, for when no LLM is available.

    from score.rules import Calibration, score_job

docs/PLAN.md 7.4 and non-negotiable 3: "if the LLMs are down, jobs still flow,
with a provisional score". Before this, an outage marked jobs scoring_failed
and they stayed out of the queue until someone ran --retry-failed by hand.

Rather than invent weights, the rules mimic what the LLM itself concluded. The
prior for a job is the median LLM score of past jobs in the same role family
(common/role.py), computed from job_scores at run time -- measured 2026-10-15
over 579 LLM scores: genai 7.2, ml 6.5, platform 6.5, data 5.5, fullstack 4.5,
security 4.5, backend 4.5, other 3.0. Roles asking 5+ years averaged 3.3
against ~5.0 for the rest, hence the seniority penalty.

Two limits keep a guess from masquerading as a judgement:
  * the score is capped at PROVISIONAL_CAP (7.0): enough to reach the 6.5 ping
    threshold for the families the LLM rates highest, never enough to be
    "strong" -- that stays the LLM's call;
  * every row is marked provisional, says so in its reasoning, and is
    re-scored by the next run that has a working provider.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from common.role import FAMILIES, role_family

PROVISIONAL_CAP = 7.0
SENIOR_YEARS = 5.0
SENIOR_PENALTY = 1.5
MIN_SAMPLES = 10            # below this, a family borrows the overall median
MODEL = "rules-v1"

# Used only when there is no LLM history at all (a fresh database). Same
# ordering as the measured medians above.
FALLBACK_PRIORS = {"genai": 7.0, "ml": 6.5, "platform": 6.5, "data": 5.5,
                   "fullstack": 4.5, "security": 4.5, "backend": 4.5, "other": 3.0}


@dataclass
class Calibration:
    priors: dict[str, float] = field(default_factory=lambda: dict(FALLBACK_PRIORS))
    samples: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_history(cls, rows: list[tuple[str | None, float]]) -> "Calibration":
        """rows: (role_family, llm fit_score) for past LLM-scored jobs."""
        by: dict[str, list[float]] = {}
        for family, score in rows:
            by.setdefault(family or "other", []).append(float(score))
        everything = [s for v in by.values() for s in v]
        if not everything:
            return cls()
        overall = statistics.median(everything)
        priors, samples = {}, {}
        for family in FAMILIES:
            values = by.get(family, [])
            samples[family] = len(values)
            priors[family] = statistics.median(values) if len(values) >= MIN_SAMPLES else overall
        return cls(priors=priors, samples=samples)


def score_job(job: dict, calibration: Calibration,
              min_years: float | None = None) -> dict:
    """A provisional job_scores row for one job."""
    family = job.get("role_family") or role_family(job.get("title"))
    score = calibration.priors.get(family, calibration.priors.get("other", 3.0))
    notes = [f"role family {family} (LLM median {score:.1f}"
             f"{', n=' + str(calibration.samples[family]) if family in calibration.samples else ''})"]
    if min_years is not None and min_years >= SENIOR_YEARS:
        score -= SENIOR_PENALTY
        notes.append(f"asks {min_years:g}+ years (-{SENIOR_PENALTY:g})")
    score = round(max(0.0, min(PROVISIONAL_CAP, score)) * 2) / 2   # half points, like the LLM
    from score.llm import verdict_for
    return {
        "job_id": job["id"],
        "fit_score": score,
        "verdict": verdict_for(score),
        "min_years": min_years,
        "reasoning": "Provisional (no LLM available): " + "; ".join(notes)
                     + ". Will be re-scored by the next run with a working provider.",
        "reject_reason": None,
        "model": MODEL,
        "provider": "rules",
        "provisional": True,
    }
