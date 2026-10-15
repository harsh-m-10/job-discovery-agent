"""SuccessFactors Career Site Builder adapter. No network — stubbed.

The parts most likely to go quietly wrong: reading the description out of the
posting page's HTML, the day/month order of the search's dates (a swap would
silently misdate a third of the year), and reading every search page.

    python tests/test_successfactors.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ingest.adapters.successfactors as sf
from ingest.models import BoardFetchError

PAGE_HTML = """<html><head><meta name="description" content="Not this"></head><body>
<span itemprop="title">Backend Engineer</span>
<div class="sidebar"><p>Similar jobs</p></div>
<span itemprop="description"><div><div style="padding:10px"><h2><b>Key Responsibilities</b></h2></div>
<div><p>Build &amp; run payment services &ndash; in Python.</p><ul><li>2+ years</li></ul><br/></div></div></span>
<footer>About cookies</footer></body></html>"""


def test_the_description_is_exactly_the_itemprop_element():
    text = sf.extract_description(PAGE_HTML)
    assert text.startswith("Key Responsibilities"), text
    assert "Build & run payment services – in Python." in text
    assert "2+ years" in text
    for outside in ("Similar jobs", "About cookies", "Not this", "Backend Engineer"):
        assert outside not in text, f"leaked {outside!r} from outside the element"


def test_a_page_without_the_marker_yields_empty_not_garbage():
    assert sf.extract_description("<html><body><p>No description here</p></body></html>") == ""


def test_dates_follow_the_locale_and_never_guess():
    gb = sf.parse_start("03/04/2026", "en_GB")
    us = sf.parse_start("03/04/2026", "en_US")
    assert (gb.day, gb.month) == (3, 4), "en_GB is day/month"
    assert (us.day, us.month) == (4, 3), "en_US is month/day"
    assert sf.parse_start("2026-10-15", "fr_FR").month == 10, "ISO works for any locale"
    assert sf.parse_start("17/09/2026", "fr_FR") is None, "unknown locale must not guess"
    assert sf.parse_start("", "en_GB") is None


def test_token_parsing():
    assert sf.parse_token("jobs.standardchartered.com|en_GB") == ("jobs.standardchartered.com", "en_GB")
    assert sf.parse_token("careers.example.com") == ("careers.example.com", "en_US")
    for bad in ("", "|en_GB", "https://x.com/path|en_GB"):
        try:
            sf.parse_token(bad)
        except BoardFetchError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def run(total, pages):
    adapter = sf.SuccessFactorsAdapter()
    adapter._search = lambda host, locale, page: pages[page] if page < len(pages) else \
        {"totalJobs": total, "jobSearchResult": []}
    adapter._page = lambda url: PAGE_HTML
    return adapter.fetch("jobs.example.com|en_GB")


def row(i):
    return {"response": {"id": str(i), "unifiedStandardTitle": f"Engineer {i}",
                         "unifiedUrlTitle": f"Engineer-{i}", "jobLocationShort": ["Chennai, IND "],
                         "jobLocationCountry": ["India"], "unifiedStandardStart": "17/09/2026"}}


def test_every_search_page_is_read():
    total = 25
    pages = [{"totalJobs": total, "jobSearchResult": [row(i) for i in range(o, min(o + 10, total))]}
             for o in (0, 10, 20)]
    jobs = run(total, pages)
    assert len(jobs) == 25 and len({j.ats_job_id for j in jobs}) == 25


def test_fields_are_normalised():
    [job] = run(1, [{"totalJobs": 1, "jobSearchResult": [row(7)]}])
    assert job.title == "Engineer 7"
    assert job.location == "Chennai, IND, India"
    assert job.posted_at.day == 17 and job.posted_at.month == 9
    assert job.posted_at_precision == "date", "the search gives a date, not a time"
    assert job.absolute_url == "https://jobs.example.com/job/Engineer-7/7-en_GB"
    assert job.description.startswith("Key Responsibilities")


def test_a_failed_posting_page_fails_the_board_transiently():
    adapter = sf.SuccessFactorsAdapter()
    adapter._search = lambda h, l, p: {"totalJobs": 1, "jobSearchResult": [row(1)]}

    def boom(url):
        raise BoardFetchError("posting page http 503", transient=True)

    adapter._page = boom
    try:
        adapter.fetch("jobs.example.com|en_GB")
    except BoardFetchError as exc:
        assert exc.transient
        return
    raise AssertionError("a board with a missing description came back short")


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
