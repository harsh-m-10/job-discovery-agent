"""Aggregator planning (Indeed + alert emails). Pure, no database.

    python tests/test_aggregate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.company_key import company_key
from ingest.aggregate import Item, agg_id, identity, norm_title, plan


def item(title="Backend Engineer", company="Acme", source="indeed", desc="", url=None):
    return Item(source=source, title=title, company=company, location="Bengaluru, India",
                url=url or f"https://example.invalid/{title}", description=desc)


IDS = {company_key("Acme"): 11, company_key("Globex"): 12}


def test_one_posting_from_two_sources_is_one_row():
    a = item(source="indeed", desc="Full JD " * 20)
    b = item(title="Backend  Engineer!", source="alert-email")
    assert identity(a) == identity(b)
    p = plan([a, b], IDS, {}, set())
    assert len(p.upserts) == 1 and p.n_new == 1
    row = p.upserts[0]
    assert row["ats_job_id"] == agg_id(identity(a)) and row["ats_job_id"].startswith("agg:")
    assert row["source"] == "indeed" and row["company_id"] == 11
    assert "first_seen_at" not in row, "never write first_seen_at"


def test_a_posting_open_on_a_polled_ats_board_is_skipped():
    p = plan([item()], IDS, {}, {(company_key("Acme"), norm_title("Backend Engineer"))})
    assert not p.upserts and p.skipped_ats == 1


def test_a_title_only_alert_never_blanks_an_indeed_description():
    key = agg_id(identity(item()))
    existing = {key: {"id": 5, "closed_at": None, "reopen_count": 0,
                      "description": "Full JD " * 20, "source": "indeed"}}
    p = plan([item(source="alert-email")], IDS, existing, set())
    assert not p.upserts and p.touch_ids == [5]


def test_a_richer_description_upgrades_the_row_but_keeps_the_first_source():
    key = agg_id(identity(item()))
    existing = {key: {"id": 5, "closed_at": None, "reopen_count": 0,
                      "description": "", "source": "alert-email"}}
    p = plan([item(desc="Full JD " * 20)], IDS, existing, set())
    assert len(p.upserts) == 1 and p.upserts[0]["source"] == "alert-email"
    assert p.upserts[0]["description"].startswith("Full JD")
    assert p.n_new == 0


def test_a_ttl_closed_posting_seen_again_reopens():
    key = agg_id(identity(item()))
    existing = {key: {"id": 5, "closed_at": "2026-10-01T00:00:00Z", "reopen_count": 1,
                      "description": "x", "source": "indeed"}}
    p = plan([item(source="alert-email")], IDS, existing, set())
    assert len(p.upserts) == 1
    assert p.upserts[0]["closed_at"] is None and p.upserts[0]["reopen_count"] == 2
    assert p.upserts[0]["description"] == "x", "the stored description is kept"


def test_rows_without_a_company_row_are_skipped_not_misfiled():
    p = plan([item(company="Initech")], IDS, {}, set())
    assert not p.upserts and p.skipped_other == 1


def test_every_upsert_has_the_same_key_set():
    p = plan([item(), item(title="Data Engineer", company="Globex", desc="jd")], IDS, {}, set())
    assert len({tuple(sorted(r)) for r in p.upserts}) == 1


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
