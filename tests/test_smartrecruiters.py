"""SmartRecruiters adapter. No network — the HTTP helper is stubbed.

Shapes are copied from live responses (Freshworks, ServiceNow, Experian,
2026-10-15). The properties that matter: every page is read, a failed detail
call fails the whole board instead of shortening it, and a too-large board
raises rather than being diffed partially.

    python tests/test_smartrecruiters.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ingest.adapters.smartrecruiters as sr
from ingest.models import BoardFetchError


def posting(i, full="Bengaluru, KA, India", country="in", remote=False):
    return {"id": str(i), "name": f"Engineer {i}", "releasedDate": "2026-10-15T07:10:42.372Z",
            "ref": f"https://api.smartrecruiters.com/v1/companies/Acme/postings/{i}",
            "location": {"fullLocation": full, "country": country, "remote": remote},
            "experienceLevel": {"label": "Mid-Senior Level"}}


def detail(i, comp=(0, 0)):
    return {"postingUrl": f"https://jobs.smartrecruiters.com/Acme/{i}",
            "compensation": {"min": comp[0], "max": comp[1], "currency": "INR", "period": "YEARLY"},
            "jobAd": {"sections": {
                "companyDescription": {"title": "Company Description", "text": "<p>We are Acme.</p>"},
                "jobDescription": {"title": "Job Description", "text": "<p>Build things.</p>"},
                "qualifications": {"title": "Qualifications", "text": "<ul><li>2+ years Python</li></ul>"},
            }}}


def run(listing_pages, details=None, fail_detail=None):
    """Stub get_json: listing pages by offset, details by posting id."""
    calls = []

    def fake(url, timeout=20):
        calls.append(url)
        if "/postings/" in url:
            pid = url.rsplit("/", 1)[1]
            if pid == fail_detail:
                raise BoardFetchError("board not found (404)")
            return (details or {}).get(pid) or detail(pid)
        offset = int(url.split("offset=")[1])
        return listing_pages[offset // sr.PAGE]

    real = sr.get_json
    sr.get_json = fake
    try:
        return sr.SmartRecruitersAdapter().fetch("Acme"), calls
    finally:
        sr.get_json = real


def test_every_page_is_read():
    total = 250
    pages = [{"totalFound": total, "content": [posting(i) for i in range(o, min(o + 100, total))]}
             for o in (0, 100, 200)]
    jobs, calls = run(pages)
    assert len(jobs) == 250
    assert sum(1 for c in calls if "offset=" in c) == 3
    assert all("country=in" in c for c in calls if "offset=" in c), "India must be filtered server-side"


def test_a_failed_detail_fails_the_board_transiently():
    pages = [{"totalFound": 3, "content": [posting(i) for i in range(3)]}]
    try:
        run(pages, fail_detail="1")
    except BoardFetchError as exc:
        assert exc.transient, "a removed posting is a race, not a dead board"
        return
    raise AssertionError("a board with a missing detail came back short")


def test_a_board_beyond_the_page_cap_raises():
    pages = [{"totalFound": 99999, "content": [posting(i + p * 100) for i in range(100)]}
             for p in range(sr.MAX_PAGES)]
    try:
        run(pages)
    except BoardFetchError as exc:
        assert "truncated" in str(exc)
        return
    raise AssertionError("a truncated board was returned as complete")


def test_an_empty_board_is_empty_not_an_error():
    jobs, _ = run([{"totalFound": 0, "content": []}])
    assert jobs == []


def test_fields_are_normalised():
    pages = [{"totalFound": 1, "content": [posting(7)]}]
    [job], _ = run(pages, details={"7": detail(7, comp=(1_500_000, 2_500_000))})
    assert job.ats_job_id == "7" and job.title == "Engineer 7"
    assert job.location == "Bengaluru, KA, India", job.location
    assert job.posted_at is not None and job.posted_at.tzinfo is not None
    assert job.absolute_url == "https://jobs.smartrecruiters.com/Acme/7"
    assert job.description.startswith("Job Description"), "requirements before boilerplate"
    assert "2+ years Python" in job.description
    assert job.description.rstrip().endswith("We are Acme."), "company blurb goes last"
    assert job.compensation == "INR 1,500,000–2,500,000 / yearly"


def test_undisclosed_compensation_is_none():
    pages = [{"totalFound": 1, "content": [posting(1)]}]
    [job], _ = run(pages)
    assert job.compensation is None


def test_location_cleanup():
    assert sr._location(posting(1, full="Hyderabad, , India")) == "Hyderabad, India"
    assert sr._location(posting(1, full="Pune, MH", country="in")) == "Pune, MH, India"
    assert sr._location(posting(1, full="Bengaluru, KA, India", remote=True)) == "Bengaluru, KA, India, Remote"


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
