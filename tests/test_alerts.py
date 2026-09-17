"""Alert cooldown tests. No network, no DB.

These exist because of one incident: for a month every operational alert was
"suppressed — already sent within 6h" when nothing had been sent at all. The
cooldown query put an un-encoded "+00:00" in the URL, PostgREST read the "+"
as a space, answered 400 with a JSON error object, and bool(dict) is True.
`--test` never caught it because force=True skips the cooldown entirely.

    python tests/test_alerts.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")

import requests  # noqa: E402

from notify import alerts  # noqa: E402


class FakeResponse:
    def __init__(self, status: int, payload):
        self.status_code = status
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._payload


POSTGREST_400 = {"code": "22007", "details": None, "hint": None,
                 "message": "invalid input syntax for type timestamp with time zone"}


def test_since_has_no_bare_plus():
    stamp = alerts._since(6)
    assert "+" not in stamp, stamp
    assert "%2B00%3A00" in stamp or "%2B00:00" in stamp, stamp


def test_cooldown_query_url_is_encoded():
    seen = {}

    def fake_get(url, **kw):
        seen["url"] = url
        return FakeResponse(200, [])

    with mock.patch.object(alerts.requests, "get", fake_get):
        assert alerts._recently_alerted("ingest_errors") is False
    assert "sent_at=gte." in seen["url"]
    assert "+00:00" not in seen["url"], seen["url"]


def test_a_prior_send_suppresses():
    with mock.patch.object(alerts.requests, "get",
                           lambda url, **kw: FakeResponse(200, [{"id": 1}])):
        assert alerts._recently_alerted("pipeline_dry") is True


def test_a_rejected_query_is_not_read_as_a_prior_send():
    # The failure mode itself: a 400 must raise, never return True.
    with mock.patch.object(alerts.requests, "get",
                           lambda url, **kw: FakeResponse(400, POSTGREST_400)):
        try:
            alerts._recently_alerted("ingest_errors")
        except requests.HTTPError:
            pass
        else:
            raise AssertionError("400 was swallowed and would suppress the alert")


def test_dry_check_treats_a_rejected_query_as_an_error_not_as_flow():
    with mock.patch.object(alerts.requests, "get",
                           lambda url, **kw: FakeResponse(400, POSTGREST_400)):
        try:
            alerts.check_pipeline_dry(72)
        except requests.HTTPError:
            pass
        else:
            raise AssertionError("400 was read as 'postings are flowing'")


def test_dry_check_returns_false_when_a_job_was_seen():
    with mock.patch.object(alerts.requests, "get",
                           lambda url, **kw: FakeResponse(200, [{"id": 2603}])):
        assert alerts.check_pipeline_dry(72) is False


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
