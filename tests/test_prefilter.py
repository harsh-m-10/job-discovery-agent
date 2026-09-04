"""Prefilter tests.

Most cases here are real titles and real JD phrasing pulled off the tracked
boards during calibration, not invented examples. The two failure modes have
very different costs and both are covered deliberately:

  * a false pass wastes an LLM call — cheap
  * a false reject loses a job the operator should have applied to, silently,
    with no way to notice — the expensive one

    python tests/test_prefilter.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from score.prefilter import check_title, extract_experience, prefilter


def passes(title: str) -> bool:
    return check_title(title) is None


# --- titles that must survive -------------------------------------------

def test_target_sde_titles_pass():
    for title in [
        "Software Development Engineer II - Data",
        "SDE II - AI",
        "Software Engineer 3",
        "Backend Engineer",
        "Machine Learning Engineer",
        "Data Engineer, Platform",
        "Cloud Infrastructure Engineer",
        "Associate Application Engineer",
        "Member of Technical Staff",
        "Member of Technical Staff (Test Engineering)",
        "GenAI Engineer",
        "Platform Engineer (Observability)",
    ]:
        assert passes(title), f"false reject: {title}"


def test_member_of_technical_staff_survives_the_staff_rule():
    # An ordinary IC title at Rubrik, Zeta, and Databricks — and frequently the
    # best-fitting req on the board. Matching it on "staff" would be costly.
    assert passes("Member of Technical Staff")
    assert check_title("Staff Software Engineer") is not None
    # ...but the exemption covers only the phrase itself.
    assert not passes("Senior Member of Technical Staff")
    assert not passes("Principal Member of Technical Staff")


def test_engineering_role_in_a_business_domain_survives():
    # Fintech backend reqs live under finance-sounding titles.
    for title in ["Analytics Engineer - Finance",
                  "Software Engineer, Treasury Platform",
                  "Data Engineer, Marketing Analytics"]:
        assert passes(title), f"false reject: {title}"


# --- titles that must be rejected ---------------------------------------

def test_seniority_rejected():
    for title in ["Senior Software Engineer - Observability",
                  "Staff Software Engineer - Ingestion",
                  "Principal Software Engineer",
                  "Lead Site Reliability Engineer",
                  "Sr. Delivery Solutions Architect",
                  "Engineering Manager - Database Platform",
                  "Director of Quality Engineering & Automation",
                  "Software Architect (L6)",
                  "Software Engineering Intern"]:
        assert not passes(title), f"false pass: {title}"


def test_sales_and_support_titles_matching_on_development_or_engineer():
    # Every one of these passed the first version of the filter.
    for title in ["business development & partnerships",
                  "Account Development Representative",
                  "Sales Development Representative",
                  "Technical Support Engineer 2",
                  "Technical Services Engineer, Partners",
                  "Corporate Solutions Engineer, Nordics",
                  "Customer Engineer, India (Based in Mumbai)",
                  "Business System Analyst",
                  "AI Specialist, Treasury Finance Operations",
                  "Seller Systems Operations Associate"]:
        assert not passes(title), f"false pass: {title}"


def test_non_engineering_titles_rejected():
    for title in ["Program Manager", "Senior Payroll Accountant",
                  "Brand Associate", "Specialist, Sales Compensation",
                  "Associate Field Marketing Manager"]:
        assert not passes(title), f"false pass: {title}"


def test_empty_title():
    assert check_title("   ") == "empty_title"


# --- experience extraction ----------------------------------------------

def test_common_requirement_phrasings():
    cases = {
        "3+ years of experience in backend development": (3, None),
        "2-4 years of relevant industry experience": (2, 4),
        "Minimum of 5 years of professional experience": (5, None),
        "at least 2 years experience building software": (2, None),
        "5+ yrs of hands-on engineering experience": (5, None),
        "1 to 3 years of experience": (1, 3),
        "0-2 years of experience": (0, 2),
        # Unit repeated on both ends — a real Cisco title. Without this the
        # role reads as unstated and a 5-13 year req reaches the LLM.
        "Engineer - 5 years to 13Years experience - Bangalore": (5, 13),
        "2 Years to 5 Years of relevant experience": (2, 5),
    }
    for text, expected in cases.items():
        assert extract_experience(text) == expected, text


def test_unstated_experience_is_none():
    assert extract_experience("We want great engineers.") == (None, None)
    assert extract_experience("") == (None, None)


def test_non_requirement_year_figures_ignored():
    # These sentences are everywhere in JDs and would fake a requirement.
    for text in ["We have grown 10x in 3 years.",
                 "Founded 12 years ago, we serve millions.",
                 "Revenue doubled over the past 5 years.",
                 "Our platform processes 2 years of historical data."]:
        assert extract_experience(text)[0] is None, text


def test_lowest_stated_requirement_wins():
    # A JD asking "3+ years backend, 5+ years distributed systems" states a real
    # floor of 3. Reading the higher figure would reject a job in range.
    text = ("You have 3+ years of experience with backend services. "
            "Ideally 6+ years of experience with distributed systems.")
    assert extract_experience(text)[0] == 3


def test_absurd_figures_ignored():
    assert extract_experience("20 years of engineering experience")[0] is None


# --- full gate ----------------------------------------------------------

def test_over_max_experience_rejected_with_reason():
    result = prefilter("Software Engineer",
                       "We need 8+ years of professional experience.")
    assert not result.passed
    assert result.reject_reason == "experience:8y_min"
    assert result.min_years == 8


def test_in_range_experience_passes_and_reports_range():
    result = prefilter("Software Development Engineer II",
                       "2-4 years of experience required.")
    assert result.passed and result.min_years == 2 and result.max_years == 4


def test_stretch_range_is_not_rejected():
    # The sub-2-year filter is the hypothesis under test. A 2-4y req must reach
    # the LLM to be scored worth_trying, not die here.
    assert prefilter("Backend Engineer", "3+ years of experience.").passed


def test_location_and_already_scored_short_circuit():
    assert prefilter("Backend Engineer", "", location_ok=False).reject_reason == "location"
    assert prefilter("Backend Engineer", "", already_scored=True).reject_reason == "already_scored"


def test_title_requirement_beats_a_stray_body_figure():
    # Real Cisco req. The body mentions "2 years" in passing; the title is the
    # actual requirement and must win, or a 7-10 year role reaches the LLM.
    result = prefilter(
        "Thermal Engineer - 7 to 10 yrs",
        "Our team has grown fast. You will have 2 years to make an impact. "
        "Strong experience with thermal simulation required.",
    )
    assert not result.passed and result.min_years == 7


def test_body_still_decides_when_the_title_is_silent():
    result = prefilter("Backend Engineer",
                       "3+ years of experience. Ideally 8+ years with Kafka.")
    assert result.passed and result.min_years == 3


def test_threshold_is_configurable():
    jd = "6+ years of experience."
    assert not prefilter("Backend Engineer", jd, max_experience_years=4).passed
    assert prefilter("Backend Engineer", jd, max_experience_years=8).passed


# --- age gate -----------------------------------------------------------
# The gate runs before the LLM, so a bug here either burns money on stale reqs
# or silently discards fresh ones. Both directions are pinned.

def test_a_stale_posting_is_rejected_with_its_age():
    result = prefilter("Backend Engineer", "", age_days=12, max_age_days=5)
    assert not result.passed
    assert result.reject_reason == "age:12d"


def test_a_fresh_posting_survives_the_gate():
    assert prefilter("Backend Engineer", "", age_days=2, max_age_days=5).passed


def test_the_boundary_day_is_kept():
    # Exactly at the ceiling must pass: "older than 5 days" is the rule, and an
    # off-by-one here quietly drops a day's worth of postings.
    assert prefilter("Backend Engineer", "", age_days=5, max_age_days=5).passed
    assert not prefilter("Backend Engineer", "", age_days=5.1, max_age_days=5).passed


def test_the_gate_is_open_when_either_side_is_unknown():
    # No max configured, or no usable date on the posting. Neither may discard.
    assert prefilter("Backend Engineer", "", age_days=900).passed
    assert prefilter("Backend Engineer", "", age_days=None, max_age_days=5).passed


def test_age_outranks_the_other_reasons():
    # Cheapest and most certain check first, so a stale senior req is recorded
    # as stale rather than as senior. Keeps the spend attribution honest.
    result = prefilter("Senior Staff Engineer", "10+ years of experience.",
                       age_days=40, max_age_days=5)
    assert result.reject_reason == "age:40d"


def test_already_scored_still_wins_over_age():
    result = prefilter("Backend Engineer", "", already_scored=True,
                       age_days=40, max_age_days=5)
    assert result.reject_reason == "already_scored"


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
