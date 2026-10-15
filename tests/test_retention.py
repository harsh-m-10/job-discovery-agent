"""notify.run read paging and notification retention. No network — stubbed.

Two failure modes, both silent, both pinned here:

  * Paging. PostgREST caps a response at 1000 rows. A truncated dedupe list
    re-pings jobs that were already delivered; a truncated jobs lookup drops a
    job as "closed between scoring and notifying" and nobody hears about it.

  * Retention. The prune must only ever delete ok = false rows. ok = true rows
    are the dedupe record, so one wrong filter re-pings every job ever sent.

    python tests/test_retention.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SUPABASE_URL", "https://example.invalid")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")

import notify.run as nr


class Resp:
    def __init__(self, rows=None, status=200, headers=None):
        self._rows = rows if rows is not None else []
        self.status_code = status
        self.headers = headers or {}

    def json(self):
        return self._rows

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def with_stub(name, fn, body):
    original = getattr(nr.requests, name)
    setattr(nr.requests, name, fn)
    try:
        return body()
    finally:
        setattr(nr.requests, name, original)


# --- paging -------------------------------------------------------------

def paged_source(total):
    """A stub that serves `total` rows, 1000 at a time, by Range header."""
    calls = []

    def get(url, headers=None, timeout=None):
        start, end = (int(x) for x in headers["Range"].split("-"))
        calls.append((start, end))
        return Resp([{"job_id": i} for i in range(start, min(end + 1, total))])

    return get, calls


def test_a_result_over_the_cap_is_read_in_full():
    get, calls = paged_source(2265)        # the open-jobs count that broke the dashboard
    rows = with_stub("get", get, lambda: nr.get_all("jobs?select=id", {}))
    assert len(rows) == 2265, f"read {len(rows)} of 2265"
    assert len({r["job_id"] for r in rows}) == 2265, "pages overlapped"
    assert calls == [(0, 999), (1000, 1999), (2000, 2999)]


def test_an_exact_multiple_of_the_page_size_stops_cleanly():
    get, calls = paged_source(2000)
    rows = with_stub("get", get, lambda: nr.get_all("jobs?select=id", {}))
    assert len(rows) == 2000
    assert len(calls) == 3, "must ask once more to learn the result is finished"


def test_a_small_result_costs_one_request():
    get, calls = paged_source(170)
    rows = with_stub("get", get, lambda: nr.get_all("notifications?select=job_id", {}))
    assert len(rows) == 170 and len(calls) == 1


def test_a_runaway_query_raises_instead_of_looping():
    def get(url, headers=None, timeout=None):
        return Resp([{"job_id": 0}] * nr.PAGE)

    try:
        with_stub("get", get, lambda: nr.get_all("jobs?select=id", {}))
    except RuntimeError as exc:
        assert "missing a filter" in str(exc)
        return
    raise AssertionError("an endless result was accepted")


def test_an_http_error_raises_rather_than_being_iterated_as_rows():
    """The old code called .json() on an error body and iterated a dict."""
    def get(url, headers=None, timeout=None):
        return Resp({"message": "boom"}, status=400)

    try:
        with_stub("get", get, lambda: nr.get_all("jobs?select=id", {}))
    except RuntimeError:
        return
    raise AssertionError("an HTTP 400 was treated as data")


# --- retention ----------------------------------------------------------

def capture_delete(status=204, content_range="*/26134"):
    seen = {}

    def delete(url, headers=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers
        return Resp(status=status, headers={"Content-Range": content_range})

    return delete, seen


def test_the_prune_only_ever_targets_failed_rows():
    delete, seen = capture_delete()
    with_stub("delete", delete, lambda: nr.prune_failures(30))
    url = unquote(seen["url"])
    assert "/notifications?" in url
    assert "ok=is.false" in url, "the prune is not restricted to failures"
    assert "ok=is.true" not in url and "ok=eq.true" not in url
    assert "sent_at=lt." in url, "the prune has no age bound"


def test_the_prune_cutoff_is_url_safe():
    """A raw "+00:00" decodes to a space and PostgREST 400s (alerts._since)."""
    delete, seen = capture_delete()
    with_stub("delete", delete, lambda: nr.prune_failures(30))
    query = seen["url"].split("?", 1)[1]
    assert "+" not in query, f"unquoted '+' in {query!r}"


def test_the_prune_reports_the_deleted_count():
    delete, _ = capture_delete(content_range="*/26134")
    assert with_stub("delete", delete, lambda: nr.prune_failures(30)) == 26134


def test_nothing_to_prune_is_zero_not_an_error():
    delete, _ = capture_delete(content_range="*/0")
    assert with_stub("delete", delete, lambda: nr.prune_failures(30)) == 0


def test_a_failed_prune_never_raises():
    """Housekeeping must never cost a ping."""
    def delete(url, headers=None, timeout=None):
        raise OSError("connection reset")

    assert with_stub("delete", delete, lambda: nr.prune_failures(30)) is None

    def delete_500(url, headers=None, timeout=None):
        return Resp(status=500)

    assert with_stub("delete", delete_500, lambda: nr.prune_failures(30)) is None


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
