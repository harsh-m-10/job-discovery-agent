"""Role family classifier. No network, no DB.

Every case below is a real title from the 172 that have scored >= 6.5, so a
rule change that breaks one breaks a decision the operator actually made.

    python tests/test_role.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.role import FAMILIES, role_family

CASES = {
    "genai": [
        "Agentic AI Engineer", "LLMops Engineer", "SDE III Gen AI", "Agent Engineer",
        "Conversation AI Engineer", "AI Platform Engineer",
        # GenAI wins over backend when a title names both.
        "Software Engineer – Backend & AI Integration",
        "Software Engineer (1 - 2 years of experience in Python, GenAI, ML, Deep Learning)",
        "Forward Deployed Engineer, India",
    ],
    "ml": [
        "Machine Learning Engineer", "SDE II - ML", "DS / ML Engineer", "Data Scientist",
        "Associate Data Scientist", "Performance Engineer, On-Device Inference",
        "ML Engineer (Data), Foundational Models",
    ],
    "data": [
        "Data Engineer", "Data Engineer II", "Data Analyst",
        "Software Engineer II -  Data Integration Platform (DIP) Ingestion",
    ],
    "fullstack": [
        "Software Engineer - Fullstack", "Full-Stack Engineer - Creative Studio",
        "Frontend Engineer, Vision", "Member of Technical Staff - Fullstack",
    ],
    "backend": [
        "Backend Engineer, Chanakya",
        # Explicit backend beats the word "platform".
        "Backend Engineer - Studio Media Platform",
        "Software Engineer", "Software Development Engineer 2", "SW Engineer",
        "Member of Technical Staff", "Software Engineer (SDE-2)",
        "Intermediate Backend Engineer - Database Change Management",
    ],
    "platform": [
        "Site Reliability Engineer", "Software Engineer-II SRE", "DevOps Engineer",
        "Software Engineer II - Platform & Infrastructure", "Cloud Platform Engineer",
        "Member of Technical Staff (MTS) – DevOps & Infrastructure", "Engineer - DevEx",
    ],
    "security": ["Security Engineer", "Product Security Engineer 5"],
    "other": ["GTM Engineer", "Developer Relations Engineer - Developer Advocate",
              "Talent Pipeline - Product Engineering", "Product Manager", "", None],
}


def test_every_real_title_lands_in_its_family():
    wrong = []
    for family, titles in CASES.items():
        for title in titles:
            got = role_family(title)
            if got != family:
                wrong.append(f"{title!r}: expected {family}, got {got}")
    assert not wrong, "\n  " + "\n  ".join(wrong)


def test_database_is_not_data():
    """\\bdata\\b must not fire inside "Database"."""
    assert role_family("Intermediate Backend Engineer - Database Change Management") == "backend"


def test_ai_inside_other_words_is_not_genai():
    for title in ("Email Systems Engineer", "Maintenance Engineer", "Chair of Engineering"):
        assert role_family(title) != "genai", title


def test_every_family_is_reachable_and_the_set_matches_the_db_constraint():
    reached = {role_family(t) for titles in CASES.values() for t in titles}
    assert reached == set(FAMILIES)
    sql = (Path(__file__).resolve().parents[1] / "db" / "migrations" /
           "0007_role_family.sql").read_text(encoding="utf-8")
    for family in FAMILIES:
        assert f"'{family}'" in sql, f"{family} missing from the 0007 check constraint"


def test_the_upsert_payload_carries_the_family():
    from ingest.lifecycle import _payload
    from ingest.models import RawJob
    job = RawJob(ats_job_id="1", title="SDE III Gen AI", location="Bengaluru",
                 description="x", absolute_url="https://x.invalid/1")
    assert _payload(1, job, "h", 0)["role_family"] == "genai"


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  pass  {name}")
            except AssertionError as exc:
                failed += 1
                print(f"  FAIL  {name}: {exc or 'assertion failed'}")
    print(f"\n{failed} failed" if failed else "\nall tests passed")
    sys.exit(1 if failed else 0)
