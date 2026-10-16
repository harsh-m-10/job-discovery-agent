"""Gem, Oracle Recruiting Cloud and Atlassian adapters. No network: get_json
is stubbed with payloads shaped like the live responses of 2026-10-16.

    python tests/test_more_adapters.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ingest.adapters.atlassian as atl
import ingest.adapters.gem as gem
import ingest.adapters.oracle as orc
from ingest.models import BoardFetchError


def stub(module, responder):
    real = module.get_json
    module.get_json = lambda url, timeout=20: responder(url)
    return lambda: setattr(module, "get_json", real)


def test_gem_reads_location_and_plain_content():
    payload = [{"id": "abc", "title": "Forward Deployed Analyst, Bangalore",
                "location": {"name": "Bengaluru, India"}, "location_type": "remote",
                "content_plain": "About the team\xa0\nWe build.", "absolute_url": "https://g/abc",
                "first_published_at": "2025-12-08T20:59:26.966Z", "departments": [{"name": "FD"}]}]
    undo = stub(gem, lambda u: payload)
    try:
        [j] = gem.GemAdapter().fetch("promptql")
    finally:
        undo()
    assert j.ats_job_id == "abc" and j.location == "Bengaluru, India (Remote)"
    assert j.description.startswith("About the team") and "\xa0" not in j.description
    assert j.posted_at.year == 2025


def test_oracle_pages_and_reads_details():
    def respond(url):
        if "recruitingCEJobRequisitionDetails" in url:
            return {"items": [{"ExternalDescriptionStr": "<p>Do things</p>",
                               "ExternalQualificationsStr": "<li>Python</li>"}]}
        assert "siteNumber=CX_45001,location=India," in url, url
        offset = int(url.split("offset=")[1].split(",")[0])
        reqs = [{"Id": str(offset + i), "Title": f"Consultant {offset + i}",
                 "PrimaryLocation": "BENGALURU, KARNATAKA, India", "PostedDate": "2026-10-12",
                 "secondaryLocations": [{"Name": "Hyderabad, India"}]}
                for i in range(orc.PAGE if offset == 0 else 3)]
        return {"items": [{"TotalJobsCount": orc.PAGE + 3, "requisitionList": reqs}]}
    undo = stub(orc, respond)
    try:
        jobs = orc.OracleAdapter().fetch("eeho.fa.us2|CX_45001|India")
    finally:
        undo()
    assert len(jobs) == orc.PAGE + 3, "must page past the first response"
    j = jobs[0]
    assert j.location == "BENGALURU, KARNATAKA, India; Hyderabad, India"
    assert "Do things" in j.description and "Python" in j.description
    assert j.posted_at_precision == "date"
    assert j.absolute_url.endswith("/sites/CX_45001/job/0")


def test_oracle_rejects_a_malformed_token():
    try:
        orc.OracleAdapter().fetch("CX_45001")
    except BoardFetchError:
        return
    raise AssertionError("a token without host|site|location was accepted")


def test_atlassian_merges_a_req_posted_on_two_portals():
    row = {"id": "25480", "title": "Engineer", "type": "Full-Time",
           "portalJobPost": {"portalUrl": "https://i/25480", "updatedDate": "2026-10-15 10:17 AM"},
           "overview": "<p>Hi</p>", "responsibilities": "<ul><li>Build</li></ul>",
           "applyUrl": "https://i/25480/apply"}
    payload = [{**row, "locations": ["Bengaluru - India -   Bengaluru,  560071 India"]},
               {**row, "locations": ["Remote - India - Remote"]}]
    undo = stub(atl, lambda u: payload)
    try:
        [j] = atl.AtlassianAdapter().fetch("atlassian")
    finally:
        undo()
    assert j.location == "Bengaluru - India - Bengaluru, 560071 India; Remote - India - Remote"
    assert "Build" in j.description and j.absolute_url == "https://i/25480/apply"
    assert j.posted_at.isoformat() == "2026-10-15T00:00:00+00:00" and j.posted_at_precision == "date"


def test_avature_listing_and_detail_fields():
    import ingest.adapters.avature as av
    listing = ('<article><h3><a class="link" href="https://jobs.x.com/en_US/p/JobDetail/523697" '
               'data-au="a">\n  Technical Sales (Bangalore)\n</a></h3></article>')
    assert av.parse_listing(listing) == [("https://jobs.x.com/en_US/p/JobDetail/523697", "523697",
                                          "Technical Sales (Bangalore)")]
    detail = ('<div class="article__content__view__field tf_locations">'
              '<div class="article__content__view__field__label" > Location(s) </div>'
              '<div class="article__content__view__field__value"><ul><li>Bangalore - Karnataka - India</li>'
              '</ul></div></div>'
              '<div class="article__content__view__field">'
              '<div class="article__content__view__field__label">Posted since</div>'
              '<div class="article__content__view__field__value">28-Jul-2026</div></div>'
              '<div class="article__content__view__field tf_replaceFieldVideoTokens">'
              '<div class="article__content__view__field__value"><p>Sell things in Bangalore.</p></div></div>'
              '<div class="article__content__separator"></div>')
    d = av.parse_detail(detail)
    assert d["location"] == "Bangalore - Karnataka - India", d
    assert d["posted_at"].isoformat() == "2026-07-28T00:00:00+00:00"
    assert d["description"] == "Sell things in Bangalore."
    try:
        av.parse_token("jobs.siemens.com/en_US/externaljobs")
    except BoardFetchError:
        pass
    else:
        raise AssertionError("a token without a keyword was accepted")


def test_socgen_document_to_job():
    from ingest.adapters.socgen import to_job
    doc = {"sourcestr4": "26000KO7", "resulttitle": "Analyst", "sourcestr7": "Bangalore, India",
           "sourcestr13": "Are you looking for a new opportunity?",
           "sourcevarchar2": "<p>Client/stakeholder management</p>",
           "url1": "https://careers.societegenerale.com/en/job-offers/analyst-26000KO7-en",
           "sourcedatetime1": "2026-10-15 14:00:00", "sourcedatetime2": "2026-10-12 14:00:00"}
    j = to_job(doc)
    assert (j.ats_job_id, j.title, j.location) == ("26000KO7", "Analyst", "Bangalore, India")
    assert "stakeholder management" in j.description and "<p>" not in j.description
    assert j.posted_at.isoformat() == "2026-10-12T00:00:00+00:00", "publication date, not last update"
    assert to_job({"resulttitle": "no id"}) is None


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
