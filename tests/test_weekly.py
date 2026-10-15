"""Weekly report arithmetic and wording. No network — compose() is pure.

    python tests/test_weekly.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from notify.weekly import compose

NOW = datetime(2026, 10, 18, 3, 30, tzinfo=timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def base(**over):
    d = {"now": NOW, "threshold": 6.5, "runs": [], "new_jobs": [], "scores": [],
         "pings": [], "funnel": [], "events": [], "asks": [], "inactive": [],
         "active": [{"id": 1}], "usage": []}
    d.update(over)
    return d


def test_interviews_per_ten_leads_the_report():
    funnel = ([{"applied_at": iso(NOW - timedelta(days=30)), "interview_at": iso(NOW), "status": "interviewing"}] * 2
              + [{"applied_at": iso(NOW - timedelta(days=30)), "status": "rejected"}] * 8)
    subject, body = compose(base(funnel=funnel))
    assert "INTERVIEWS PER 10 APPLICATIONS: 2.0  (2 of 10 all time)" in body
    assert "2.0 interviews/10" in subject


def test_a_new_boards_back_catalogue_is_not_discovery_latency():
    old = {"first_seen_at": iso(NOW - timedelta(hours=1)),
           "posted_at": iso(NOW - timedelta(days=90)), "posted_at_precision": "timestamp"}
    fresh = {"first_seen_at": iso(NOW - timedelta(hours=10)),
             "posted_at": iso(NOW - timedelta(hours=12)), "posted_at_precision": "timestamp"}
    _, body = compose(base(new_jobs=[old] * 50 + [fresh]))
    assert "discovery latency median 2.0h" in body, body
    assert "n=1" in body


def test_a_board_added_this_week_does_not_count_as_late_discovery():
    """A posting from 3 days ago at a board first polled today was not found
    late; the board did not exist yet."""
    new_board = {"company_id": 9, "first_seen_at": iso(NOW - timedelta(hours=1)),
                 "posted_at": iso(NOW - timedelta(days=3)), "posted_at_precision": "timestamp"}
    old_board = {"company_id": 1, "first_seen_at": iso(NOW - timedelta(hours=10)),
                 "posted_at": iso(NOW - timedelta(hours=14)), "posted_at_precision": "timestamp"}
    _, body = compose(base(new_jobs=[new_board, old_board],
                           board_first_seen={9: iso(NOW - timedelta(hours=1)),
                                             1: iso(NOW - timedelta(days=200))}))
    assert "discovery latency median 4.0h" in body and "n=1" in body, body


def test_date_only_postings_are_excluded_from_latency():
    dated = {"first_seen_at": iso(NOW), "posted_at": iso(NOW - timedelta(hours=5)),
             "posted_at_precision": "date"}
    _, body = compose(base(new_jobs=[dated]))
    assert "discovery latency median —" in body


def test_only_automated_runs_count_toward_cadence():
    runs = [{"started_at": iso(NOW - timedelta(minutes=15 * i)), "trigger_source": "cronjob"}
            for i in range(5)] + [{"started_at": iso(NOW - timedelta(minutes=7)), "trigger_source": "manual"}]
    _, body = compose(base(runs=runs))
    assert "median gap between automated runs: 15.0 min" in body


def test_a_mostly_rules_week_is_flagged():
    scores = [{"fit_score": 5, "provider": "rules"}] * 4 + [{"fit_score": 7, "provider": "google"}] * 6
    _, body = compose(base(scores=scores))
    assert "provisional (rules) scores: 4 of 10  <- over 30%" in body


def test_an_empty_week_says_why_rather_than_showing_zeros_silently():
    _, body = compose(base())
    assert "Nothing to measure without applications" in body


def test_the_trigger_mix_flags_a_clocked_trigger_that_went_quiet():
    trig = ([{"fired_at": iso(NOW - timedelta(minutes=15 * i)), "source": "cronjob",
              "outcome": "ran"} for i in range(4)]
            + [{"fired_at": iso(NOW - timedelta(hours=5)), "source": "cf", "outcome": "lease"}]
            + [{"fired_at": iso(NOW - timedelta(hours=30)), "source": "apps-script",
                "outcome": "ran"}])
    _, body = compose(base(triggers=trig))
    lines = {l.split()[0]: l for l in body.splitlines() if "fires:" in l}
    assert "4 fires: 4 ran, 0 collapsed" in lines["cronjob"] and "QUIET" not in lines["cronjob"]
    assert "1 fires: 0 ran, 1 collapsed" in lines["cf"] and "QUIET" in lines["cf"], lines["cf"]
    assert "QUIET" not in lines["apps-script"], "a standby is quiet by design"


def test_a_clocked_trigger_with_no_fires_at_all_is_flagged():
    _, body = compose(base(triggers=[]))
    assert any("cf" in l and "never" in l and "QUIET" in l for l in body.splitlines())


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
