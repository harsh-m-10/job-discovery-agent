"""Stable ordering for paged reads. No network.

LIMIT/OFFSET pages are consistent only under a unique ORDER BY. On 2026-10-15
the scorer's open-jobs read (order=posted_at.desc, not unique) returned
duplicates across pages and failed the pipeline run. Every pager now appends
the table's primary key; these tests pin that, and pin that the TypeScript copy
in dashboard/lib/db.ts agrees.

    python tests/test_paging.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.paging import PRIMARY_KEY, stable_order


def test_a_query_with_no_order_gets_the_primary_key():
    assert stable_order("jobs", "select=id&closed_at=is.null") == \
        "select=id&closed_at=is.null&order=id.asc"


def test_a_non_unique_order_gets_a_tie_breaker():
    """The exact query that broke the pipeline."""
    assert stable_order("jobs", "select=id&order=posted_at.desc") == \
        "select=id&order=posted_at.desc,id.asc"


def test_an_order_already_ending_in_the_key_is_left_alone():
    q = "select=id&order=posted_at.desc,id.asc"
    assert stable_order("jobs", q) == q
    assert stable_order("job_scores", "order=job_id.desc") == "order=job_id.desc"


def test_tables_keyed_differently_use_their_own_key():
    assert stable_order("job_scores", "select=job_id").endswith("order=job_id.asc")
    assert stable_order("v_funnel", "select=*").endswith("order=application_id.asc")


def test_an_unknown_table_is_not_guessed_at():
    assert stable_order("mystery", "select=*") == "select=*"


def test_an_empty_query_still_gets_an_order():
    assert stable_order("jobs", "") == "order=id.asc"


def test_the_dashboard_copy_has_the_same_keys():
    ts = (ROOT / "dashboard" / "lib" / "db.ts").read_text(encoding="utf-8")
    block = re.search(r"PRIMARY_KEY: Record<string, string> = \{(.*?)\};", ts, re.S).group(1)
    ts_keys = dict(re.findall(r"(\w+): \"(\w+)\"", block))
    assert ts_keys == PRIMARY_KEY, f"TS {ts_keys} != Python {PRIMARY_KEY}"


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
