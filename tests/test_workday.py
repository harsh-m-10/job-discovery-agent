"""Workday facet resolution and truncation. No network.

Both behaviours here were bought with real data loss.

The facet lookup used to read only the top level of the facet tree and require
a descriptor equal to "india". Target answers with a flat `Location_Country`
whose value is exactly "India", so it worked there and nowhere else. Cisco and
Micron nest city entries ("Bengaluru, India") one level down under
`locationMainGroup`, so both fell back to paging the entire global board — 1,303
and 3,075 postings against an 800 cap — and every India posting past the cut-off
was closed and reopened as the board reordered.

The trap in loosening the match is Target's `Location_Region_State_Province`,
which carries "Indiana" with 232 postings. \\bindia\\b rejects it; "india" in
descriptor would not.

    python tests/test_workday.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.adapters.workday import (INDIA_FACET, MAX_PAGES, PAGE,
                                     WorkdayAdapter, parse_token)
from ingest.models import BoardFetchError

adapter = WorkdayAdapter()


def facets_from(payload: dict) -> dict:
    """Run _india_facet against a canned first-page response."""
    adapter_post = adapter._post
    try:
        adapter._post = lambda *a, **k: payload          # type: ignore[method-assign]
        return adapter._india_facet("https://x/wday/cxs/x/y")
    finally:
        adapter._post = adapter_post                     # type: ignore[method-assign]


# --- the word-boundary rule ---------------------------------------------

def test_india_matches_as_a_whole_word():
    for good in ("India", "india", "Bengaluru, India", "India Remote",
                 "Bangalore, Karnataka, India", "Hyderabad - Skyview, India"):
        assert INDIA_FACET.search(good), good


def test_indiana_is_not_india():
    # Target's state facet, 232 postings. Matching it would swap the whole
    # board for Indiana and quietly look like a working filter.
    for bad in ("Indiana", "Indianapolis, Indiana, US", "INDIANA"):
        assert not INDIA_FACET.search(bad), bad


# --- facet shapes --------------------------------------------------------

TARGET_SHAPE = {"facets": [
    {"facetParameter": "Location_Country",
     "values": [{"descriptor": "United States", "id": "us-id", "count": 1931},
                {"descriptor": "India", "id": "india-id", "count": 69}]},
    {"facetParameter": "Location_Region_State_Province",
     "values": [{"descriptor": "Indiana", "id": "indiana-id", "count": 232}]},
]}

CISCO_SHAPE = {"facets": [
    {"facetParameter": "jobFamilyGroup", "values": [{"descriptor": "Engineer", "id": "e"}]},
    {"facetParameter": "locationMainGroup", "values": [
        {"facetParameter": "locations", "descriptor": "Locations", "values": [
            {"descriptor": "Abu Dhabi, United Arab Emirates", "id": "ad", "count": 1},
            {"descriptor": "Ahmedabad, India", "id": "ahm", "count": 2},
            {"descriptor": "Bangalore, India", "id": "blr", "count": 180},
            {"descriptor": "Chennai, India", "id": "maa", "count": 40},
            {"descriptor": "Allen, Texas, US", "id": "tx", "count": 4},
        ]}]},
]}


def test_flat_country_facet_still_resolves():
    assert facets_from(TARGET_SHAPE) == {"Location_Country": ["india-id"]}


def test_nested_city_facet_resolves_and_keeps_every_city():
    # One id would narrow a national board to a single city.
    assert facets_from(CISCO_SHAPE) == {"locations": ["ahm", "blr", "maa"]}


def test_a_board_with_no_location_facet_yields_nothing():
    assert facets_from({"facets": [
        {"facetParameter": "timeType",
         "values": [{"descriptor": "Full time", "id": "ft"}]}]}) == {}


def test_non_location_parameters_are_ignored_even_if_they_say_india():
    # A job family called "India Support" must not become a location filter.
    assert facets_from({"facets": [
        {"facetParameter": "jobFamilyGroup",
         "values": [{"descriptor": "India Support", "id": "nope"}]}]}) == {}


# --- truncation ----------------------------------------------------------

def test_a_truncated_board_raises_instead_of_diffing():
    """lifecycle.plan must never see a partial board.

    Before this, a board larger than MAX_PAGES x PAGE returned its first 800
    postings and the diff closed everything beyond them.
    """
    full_page = [{"title": f"Role {i}", "externalPath": f"/job/{i}",
                  "locationsText": "Bengaluru, India", "bulletFields": [f"R{i}"]}
                 for i in range(PAGE)]

    def always_full(url, body, timeout=30):
        if url.endswith("/jobs") and body.get("limit") == 1:
            return {"facets": [], "total": 99999}
        return {"jobPostings": full_page, "total": 99999}

    original = adapter._post
    try:
        adapter._post = always_full                      # type: ignore[method-assign]
        try:
            adapter.fetch("x|wd1|External")
        except BoardFetchError as exc:
            assert "truncated" in str(exc), exc
            assert str(MAX_PAGES * PAGE) in str(exc) or "99999" in str(exc)
        else:
            raise AssertionError("a truncated board was returned as if complete")
    finally:
        adapter._post = original                         # type: ignore[method-assign]


# --- transient vs permanent --------------------------------------------

class FakeResp:
    def __init__(self, status, ctype="application/json", payload=None):
        self.status_code = status
        self.headers = {"content-type": ctype}
        self._payload = payload if payload is not None else {}
        self.text = "body"

    def json(self):
        return self._payload


def post_raising(status, ctype="application/json"):
    """Drive WorkdayAdapter._post against a canned response."""
    import ingest.adapters.workday as wd
    original = wd.session
    wd.session = lambda: type("S", (), {"post": lambda *a, **k: FakeResp(status, ctype)})()
    try:
        adapter._post("https://x/jobs", {})
    finally:
        wd.session = original


def test_the_spa_shell_is_transient():
    """The maintenance-window failure. Four wd5 tenants hit it at once on
    2026-10-03 and recovered by the next run; counting it toward the
    five-strike cutoff would have deactivated all four."""
    try:
        post_raising(200, ctype="text/html")
    except BoardFetchError as exc:
        assert exc.transient is True, "SPA shell must not count toward deactivation"
        assert "SPA shell" in str(exc)
    else:
        raise AssertionError("no error raised")


def test_a_5xx_is_transient():
    try:
        post_raising(503)
    except BoardFetchError as exc:
        assert exc.transient is True
    else:
        raise AssertionError("no error raised")


def test_a_wrong_site_name_is_permanent():
    # 404 means the tenant is real and the token is wrong. That should retire
    # the board, which is the whole point of having two classes.
    try:
        post_raising(404)
    except BoardFetchError as exc:
        assert exc.transient is False, "a dead token must still deactivate"
    else:
        raise AssertionError("no error raised")


def test_a_wrong_host_is_permanent():
    try:
        post_raising(422)
    except BoardFetchError as exc:
        assert exc.transient is False
    else:
        raise AssertionError("no error raised")


def test_a_truncated_board_is_permanent_not_transient():
    # Truncation means the facet stopped resolving; retrying will not fix it.
    exc = BoardFetchError("truncated at 800 of 3075 postings")
    assert exc.transient is False


def test_get_json_classification():
    """http.get_json covers Greenhouse, Lever and Ashby."""
    import ingest.http as http
    cases = [(404, False), (500, True), (503, True), (403, False)]
    for status, want in cases:
        http_session = http.session
        http.session = lambda s=status: type(
            "S", (), {"get": lambda *a, **k: FakeResp(s)})()
        try:
            http.get_json("https://x")
        except BoardFetchError as exc:
            assert exc.transient is want, f"http {status}: transient={exc.transient}, want {want}"
        else:
            raise AssertionError(f"http {status} did not raise")
        finally:
            http.session = http_session


def test_parse_token_rejects_a_malformed_token():
    for bad in ("micron", "micron|wd1", "micron||External", ""):
        try:
            parse_token(bad)
        except BoardFetchError:
            pass
        else:
            raise AssertionError(f"accepted {bad!r}")


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
