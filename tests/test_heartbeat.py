"""Heartbeat tests. No network — every transport is stubbed.

One invariant, worth a file of its own because breaking it inverts the meaning
of a green run: `notify.heartbeat` must never raise and must always exit 0.
It is monitoring. A successful ingest that could not reach the monitor is still
a successful ingest, and a future "fix" that propagates the error would turn
every monitor outage into a failed pipeline.

    python tests/test_heartbeat.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import notify.heartbeat as hb


class FakeResponse:
    def __init__(self, status_code=200, text="OK"):
        self.status_code = status_code
        self.text = text


def run_with(monkey_post, env_url, **kwargs):
    """Call ping() with requests.post and HC_PING_URL both stubbed."""
    real_post, real_get = hb.requests.post, hb.os.environ.get

    def fake_get(key, default=None):
        if key == "HC_PING_URL":
            return env_url
        return real_get(key, default)

    hb.requests.post = monkey_post
    hb.os.environ.get = fake_get
    try:
        return hb.ping(**kwargs)
    finally:
        hb.requests.post = real_post
        hb.os.environ.get = real_get


def test_an_unset_url_is_a_no_op_not_an_error():
    calls = []
    out = run_with(lambda *a, **k: calls.append(a) or FakeResponse(), "", ok=True)
    assert out is False
    assert not calls, "pinged despite having no URL"


def test_a_whitespace_only_url_counts_as_unset():
    calls = []
    out = run_with(lambda *a, **k: calls.append(a) or FakeResponse(), "   ", ok=True)
    assert out is False and not calls


def test_success_pings_the_base_url():
    seen = {}

    def post(url, data=None, timeout=None):
        seen["url"] = url
        return FakeResponse()

    assert run_with(post, "https://hc.example/abc", ok=True) is True
    assert seen["url"] == "https://hc.example/abc"


def test_failure_pings_the_fail_endpoint_with_the_detail():
    seen = {}

    def post(url, data=None, timeout=None):
        seen["url"] = url
        seen["data"] = data
        return FakeResponse()

    assert run_with(post, "https://hc.example/abc", ok=False,
                    detail="score.run exited 1") is True
    assert seen["url"] == "https://hc.example/abc/fail"
    assert b"score.run exited 1" in seen["data"]


def test_a_trailing_slash_does_not_double_up():
    seen = {}

    def post(url, data=None, timeout=None):
        seen["url"] = url
        return FakeResponse()

    run_with(post, "https://hc.example/abc/", ok=False)
    assert seen["url"] == "https://hc.example/abc/fail", seen["url"]


def test_an_error_status_is_reported_but_not_raised():
    def post(url, data=None, timeout=None):
        return FakeResponse(503, "upstream unavailable")

    assert run_with(post, "https://hc.example/abc", ok=True) is False


def test_a_transport_exception_is_swallowed():
    def post(url, data=None, timeout=None):
        raise OSError("dns go boom")

    assert run_with(post, "https://hc.example/abc", ok=True) is False


def test_a_detail_longer_than_the_cap_is_truncated():
    seen = {}

    def post(url, data=None, timeout=None):
        seen["data"] = data
        return FakeResponse()

    run_with(post, "https://hc.example/abc", ok=False, detail="x" * 99_999)
    assert len(seen["data"]) == hb.MAX_DETAIL


def test_a_short_timeout_is_always_passed():
    """A hung monitor must not hold the Actions lane."""
    seen = {}

    def post(url, data=None, timeout=None):
        seen["timeout"] = timeout
        return FakeResponse()

    run_with(post, "https://hc.example/abc", ok=True)
    assert seen["timeout"] == hb.TIMEOUT_S
    assert 0 < hb.TIMEOUT_S <= 30, "the heartbeat timeout has grown unreasonable"


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
