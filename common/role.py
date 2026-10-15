"""Role family from a job title — one classifier for every layer.

    from common.role import role_family
    role_family("SDE III Gen AI")   # -> "genai"

docs/PLAN.md Phase 3.3 wants each ping to name the resume variant to send, and
Phase 4.5 wants the funnel sliced by role family. The plan assumed the LLM
scorer already classified roles; it does not. Adding it to the prompt would
cost a full re-score to backfill and would leave the 87% of jobs the prefilter
rejects unclassified. A title classifier is free, deterministic, covers every
job ever seen, and is what Phase 7.4's rules-only fallback scorer needs anyway.

Built from the 172 titles that have ever scored >= 6.5. Rules are checked in
order and the first match wins, which is where the judgement lives:

  * genai before ml before data: "Software Engineer - Backend & AI Integration"
    is a GenAI role that happens to touch a backend, not the reverse.
  * an explicit "backend" before "platform": "Backend Engineer - Studio Media
    Platform" is backend; "Software Engineer II - Platform & Infrastructure" is
    platform.
  * a bare "Software Engineer" / "SDE" / "Member of Technical Staff" falls to
    backend, which is what those titles mean in this corpus far more often
    than not.

Stored on jobs.role_family at ingest (db/migrations/0007) so SQL and the
dashboard read the column rather than re-implementing these rules.
"""

from __future__ import annotations

import re

FAMILIES = ("genai", "ml", "data", "fullstack", "backend", "platform",
            "security", "other")


def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(parts), re.I)


RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("genai", _rx(r"\bgen\s*-?\s*ai\b", r"\bllm", r"\bagent(ic|s)?\b", r"\bai\b",
                  r"\bconversation(al)?\s+ai\b", r"\brag\b", r"\bprompt\b",
                  r"\bforward[\s-]deployed\b")),
    ("ml", _rx(r"\bmachine\s+learning\b", r"\bml\b", r"\bmlops\b",
               r"\bdata\s+scien", r"\bdeep\s+learning\b", r"\bcomputer\s+vision\b",
               r"\binference\b", r"\bnlp\b", r"\bds\s*/\s*ml\b")),
    ("data", _rx(r"\bdata\s+(engineer|engineering|platform|integration|pipelines?|"
                 r"analyst|analytics|products?|warehouse|infrastructure)\b",
                 r"\banalytics\s+engineer\b", r"\betl\b", r"\bdata\s+engineer")),
    # Engineering-titled but not engineering work. After genai/ml/data so that
    # "AI Analyst" stays genai; before the generic "engineer" fallback so that
    # "GTM Engineer" is not filed as backend.
    ("other", _rx(r"\bgtm\b", r"\bdeveloper\s+(relations|advocate)\b", r"\bsales\b",
                  r"\brecruit", r"\bdemo\s+engineer\b", r"\btalent\b")),
    ("fullstack", _rx(r"\bfull[\s-]?stack\b", r"\bfront[\s-]?end\b", r"\bui\s+engineer\b")),
    ("backend", _rx(r"\bback[\s-]?end\b")),
    ("platform", _rx(r"\bsre\b", r"\bsite\s+reliability\b", r"\bdev\s*ops\b",
                     r"\binfrastructure\b", r"\bplatform\b", r"\bcloud\b",
                     r"\breliability\b", r"\bkubernetes\b", r"\bproduction\s+engineering\b",
                     r"\bpipeline\s+engineering\b", r"\bdevex\b", r"\bdev\s+ex\b")),
    ("security", _rx(r"\bsecurity\b", r"\bappsec\b")),
    ("backend", _rx(r"\bsoftware\s+(development\s+)?engineer", r"\bsw\s+engineer\b",
                    r"\bsde\b", r"\bsde[-\s]?(i{1,3}|\d)\b", r"\bdeveloper\b",
                    r"\bmember\s+of\s+technical\s+staff\b", r"\bmts\b",
                    r"\bengineer\b")),
)


def role_family(title: str | None) -> str:
    """One of FAMILIES. Never raises; an empty title is "other"."""
    text = (title or "").strip()
    if not text:
        return "other"
    for family, pattern in RULES:
        if pattern.search(text):
            return family
    return "other"
