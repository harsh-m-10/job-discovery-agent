"""Digest rendering tests. Synthetic jobs only — nothing is sent.

The expensive failure here is a silent drop: the digest is now the only thing
carrying a run's jobs to the operator, so a job missing from the body is a job
they never hear about. Most of these exist to pin that down.

    python tests/test_digest.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from notify.run import digest


def job(job_id: int, score: float, title: str, company: str = "Acme") -> dict:
    return {
        "job_id": job_id,
        "title": title,
        "company": company,
        "location": "Bengaluru",
        "absolute_url": f"https://example.invalid/jobs/{job_id}",
        "compensation": None,
        "age_label": "3d",
        "age_hours": 72.0,
        "fit_score": score,
        "min_years": None,
        "max_years": None,
        "matched_skills": ["Python"],
        "gap_skills": [],
        "reasoning": "synthetic",
        "referrals": [],
    }


UNSET = "whatsapp not configured"


def test_every_job_appears_in_the_body():
    entries = [(job(i, 9.0 - i * 0.1, f"Engineer {i}"), UNSET) for i in range(12)]
    _, body = digest(entries, 6.5)
    for j, _ in entries:
        assert j["title"] in body, f"{j['title']} missing from digest"
        assert j["absolute_url"] in body, f"url for {j['title']} missing"


def test_subject_names_the_top_job():
    entries = [(job(1, 9.5, "Agent Engineer", "Sarvam"), UNSET),
               (job(2, 7.0, "Backend Engineer", "Zeta"), UNSET)]
    subject, _ = digest(entries, 6.5)
    assert "9.5" in subject
    assert "Agent Engineer" in subject
    assert "Sarvam" in subject
    # The runner-up must not crowd the subject line.
    assert "Backend Engineer" not in subject


def test_counts_are_consistent_between_subject_and_body():
    entries = [(job(i, 8.0, f"Engineer {i}"), UNSET) for i in range(7)]
    subject, body = digest(entries, 6.5)
    assert "7 new matches" in subject
    assert "7 jobs scoring" in body
    assert "[7/7]" in body
    assert "[8/7]" not in body


def test_single_job_reads_as_singular():
    subject, body = digest([(job(1, 8.0, "Only Engineer"), UNSET)], 6.5)
    assert "1 new match " in subject or subject.rstrip().endswith("1 new match"), subject
    assert "matches" not in subject
    assert "1 job scoring" in body
    assert "1 jobs" not in body


def test_threshold_is_reported_as_given():
    _, body = digest([(job(1, 8.0, "Engineer"), UNSET)], 7.25)
    assert "7.25" in body


def test_distinct_whatsapp_reasons_are_summarised_once():
    entries = [(job(1, 8.0, "A"), "http 500"),
               (job(2, 8.0, "B"), "http 500"),
               (job(3, 8.0, "C"), UNSET)]
    _, body = digest(entries, 6.5)
    tail = body.rsplit("=" * 58, 1)[-1]
    assert tail.count("http 500") == 1, "duplicate reason repeated in the footer"
    assert UNSET in tail


def test_dashboard_link_is_included_only_when_configured():
    entries = [(job(1, 8.0, "Engineer"), UNSET)]
    _, with_dash = digest(entries, 6.5, "https://example.invalid/d/secret/")
    assert "https://example.invalid/d/secret" in with_dash
    _, without = digest(entries, 6.5, "")
    assert "Dashboard:" not in without


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
