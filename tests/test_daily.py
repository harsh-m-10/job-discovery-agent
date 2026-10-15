"""The daily digest's composition. Pure, no database.

    python tests/test_daily.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from notify.daily import compose

NOW = datetime(2026, 10, 15, 15, 0, tzinfo=timezone.utc)


def row(i, score, status="queued", **extra):
    return {"job_id": i, "title": f"Engineer {i}", "company": "Acme", "location": "Pune",
            "absolute_url": f"https://x.invalid/{i}", "fit_score": score,
            "status": status, **extra}


def test_untouched_matches_are_listed_best_first_and_acted_ones_counted():
    d = {"now": NOW, "threshold": 6.5, "pinged": 3, "queued": 12,
         "rows": [row(1, 7.0), row(2, 9.1), row(3, 8.0, status="applied")]}
    subject, body = compose(d, "https://d.invalid/d/s")
    assert subject.startswith("[job agent] daily: 2 untouched of 3 pinged — top 9.1 Engineer 2")
    assert body.index("9.1") < body.index("7.0")
    assert "Engineer 3" not in body and "Already acted on: 1." in body
    assert "Whole queue at or above 6.5: 12." in body
    assert "Queue: https://d.invalid/d/s" in body


def test_a_provisional_score_is_marked():
    d = {"now": NOW, "threshold": 6.5, "pinged": 1, "queued": 1,
         "rows": [row(1, 7.2, provisional=True)]}
    assert "7.2 (provisional)" in compose(d)[1]


def test_a_day_with_nothing_sends_nothing():
    assert compose({"now": NOW, "threshold": 6.5, "pinged": 0, "queued": 0, "rows": []}) is None


def test_a_quiet_day_with_a_backlog_still_reports_the_backlog():
    subject, body = compose({"now": NOW, "threshold": 6.5, "pinged": 0, "queued": 4, "rows": []})
    assert "0 untouched of 0 pinged" in subject and "Whole queue at or above 6.5: 4." in body


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
