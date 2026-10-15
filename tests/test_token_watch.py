"""Dispatch-token expiry watch. No network — classify() is pure.

If the token lapses, both external triggers fail together and the pipeline
falls back to GitHub's ~5h schedule. The warning has to arrive early, and a
401 has to be treated as an emergency rather than as "could not read expiry".

    python tests/test_token_watch.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from notify import token_watch as tw

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def header(days: float, fmt: str = "utc") -> str:
    when = NOW + timedelta(days=days)
    if fmt == "utc":
        return when.strftime("%Y-%m-%d %H:%M:%S") + " UTC"
    return when.astimezone(timezone(timedelta(hours=-8))).strftime(
        "%Y-%m-%d %H:%M:%S %z")


def test_both_accepted_header_formats_parse_to_the_same_instant():
    utc = tw.parse_expiry(header(30, "utc"))
    offset = tw.parse_expiry(header(30, "offset"))
    assert utc is not None and offset is not None
    assert abs((utc - offset).total_seconds()) < 1


def test_garbage_and_missing_headers_do_not_parse():
    assert tw.parse_expiry(None) is None
    assert tw.parse_expiry("") is None
    assert tw.parse_expiry("next tuesday") is None


def test_a_distant_expiry_is_ok():
    s = tw.classify(200, header(60), NOW)
    assert s.state == "ok" and round(s.days_left) == 60


def test_fourteen_days_out_starts_warning():
    assert tw.classify(200, header(15), NOW).state == "ok"
    assert tw.classify(200, header(14), NOW).state == "expiring"
    assert tw.classify(200, header(2), NOW).state == "expiring"


def test_a_401_is_an_emergency_not_an_unknown():
    s = tw.classify(401, None, NOW)
    assert s.state == "invalid"
    assert "Act now" in tw.alert_body(s)


def test_the_last_three_days_are_urgent():
    assert "Act now" in tw.alert_body(tw.classify(200, header(2), NOW))
    assert "Act now" not in tw.alert_body(tw.classify(200, header(10), NOW))


def test_other_errors_are_unknown_not_alarming():
    assert tw.classify(503, None, NOW).state == "unknown"
    assert tw.classify(404, None, NOW).state == "unknown"


def test_a_token_with_no_expiry_sends_no_header_and_is_ok():
    assert tw.classify(200, None, NOW).state == "ok"


def test_unconfigured_is_reported_without_a_request():
    saved = os.environ.pop("GH_DISPATCH_PAT", None)
    real_get = tw.requests.get
    try:
        tw.requests.get = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("made a request with no token"))
        assert tw.check().state == "unconfigured"
    finally:
        tw.requests.get = real_get
        if saved is not None:
            os.environ["GH_DISPATCH_PAT"] = saved


def test_the_alert_body_names_every_place_the_token_lives():
    body = tw.alert_body(tw.classify(200, header(10), NOW))
    for place in ("cron-job.org", "wrangler secret put", "GH_DISPATCH_PAT"):
        assert place in body, place


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
