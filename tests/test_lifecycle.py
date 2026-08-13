"""Lifecycle + normalization tests. No network, no DB.

The lifecycle rules are the part of Layer 1 that is easy to get subtly wrong and
impossible to notice in production: a bad close rule silently deletes the queue,
and a bad first_seen rule silently corrupts the latency telemetry that the whole
system exists to measure.

    python -m pytest tests/ -q      (or: python tests/test_lifecycle.py)
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.lifecycle import plan
from ingest.models import RawJob
from ingest.normalize import (clean_text, content_hash, html_to_text,
                              is_india_relevant, parse_epoch_ms, parse_iso,
                              truncate_for_llm)


def job(job_id="1", title="Backend Engineer", location="Bengaluru, India",
        description="Build things.") -> RawJob:
    return RawJob(ats_job_id=job_id, title=title, location=location,
                  description=description, absolute_url=f"https://x/{job_id}")


def existing(job_id="1", pk=10, digest=None, closed_at=None, reopen_count=0):
    j = job(job_id)
    return {job_id: {"id": pk,
                     "content_hash": digest or content_hash(j.title, j.location, j.description),
                     "closed_at": closed_at, "reopen_count": reopen_count}}


def test_new_job_inserts():
    p = plan(1, [job()], {})
    assert p.n_new == 1 and len(p.upserts) == 1
    assert p.upserts[0]["closed_at"] is None
    assert p.upserts[0]["reopen_count"] == 0


def test_first_seen_at_never_written():
    # PostgREST updates exactly the columns present in the payload. If
    # first_seen_at ever appears, every re-sighting resets the posting's age and
    # hours_since_posted becomes meaningless.
    p = plan(1, [job()], {})
    assert "first_seen_at" not in p.upserts[0]


def test_unchanged_job_only_touched():
    p = plan(1, [job()], existing())
    assert p.upserts == [] and p.touch_ids == [10]
    assert p.n_new == 0 and p.n_changed == 0


def test_edited_job_upserts_and_counts_as_changed():
    p = plan(1, [job(description="Build things. Now with Kafka.")], existing())
    assert p.n_changed == 1 and len(p.upserts) == 1
    assert p.touch_ids == []


def test_disappeared_job_closes():
    p = plan(1, [], existing())
    assert p.close_ids == [10] and p.n_closed == 1


def test_already_closed_job_is_not_closed_twice():
    p = plan(1, [], existing(closed_at="2026-01-01T00:00:00Z"))
    assert p.close_ids == []


def test_reopened_job_increments_count_and_clears_closed_at():
    p = plan(1, [job()], existing(closed_at="2026-01-01T00:00:00Z", reopen_count=1))
    assert p.n_reopened == 1
    assert p.upserts[0]["reopen_count"] == 2
    assert p.upserts[0]["closed_at"] is None


def test_upsert_payloads_share_one_key_set():
    # A ragged batch makes PostgREST update different columns per row.
    jobs = [job("1"), RawJob(ats_job_id="2", title="ML Engineer", location="",
                             description="", absolute_url="https://x/2",
                             compensation="INR 20-30L")]
    p = plan(1, jobs, {})
    assert {frozenset(row) for row in p.upserts} == {frozenset(p.upserts[0])}


def test_idempotent_second_run_is_a_no_op():
    # GitHub Actions cron drifts and re-fires; a repeated run must not churn rows.
    first = plan(1, [job()], {})
    stored = {"1": {"id": 10, "content_hash": first.upserts[0]["content_hash"],
                    "closed_at": None, "reopen_count": 0}}
    second = plan(1, [job()], stored)
    assert second.upserts == [] and second.touch_ids == [10] and second.close_ids == []


# --- normalization ------------------------------------------------------

def test_html_stripped_and_entities_unescaped_twice():
    # Greenhouse serves the JD escaped, so entities survive one unescape pass.
    raw = "&lt;p&gt;5+ years&amp;nbsp;Python&lt;/p&gt;&lt;li&gt;Kafka&lt;/li&gt;"
    text = html_to_text(raw)
    assert "<" not in text and "&nbsp;" not in text
    assert "5+ years" in text and "Kafka" in text


def test_bullet_structure_survives():
    text = html_to_text("<ul><li>Python</li><li>Go</li></ul>")
    assert "Python" in text.split("\n")[0] or "Python\nGo" in text


def test_remote_without_india_is_rejected():
    assert not is_india_relevant("Remote - United States")
    assert not is_india_relevant("San Francisco, CA")
    assert not is_india_relevant("Remote")


def test_india_locations_accepted():
    for loc in ["Bengaluru, Karnataka, India", "Bangalore - EC, India, onsite",
                "India, Remote", "Hyderabad", "Pune, IN"]:
        assert is_india_relevant(loc), loc


def test_remote_naming_india_in_description_accepted():
    assert is_india_relevant("Remote", "This role is open to candidates in India.")


def test_missing_location_kept_for_the_prefilter():
    assert is_india_relevant("")
    assert is_india_relevant(None)


def test_content_hash_is_stable_and_sensitive():
    a = content_hash("SDE", "Bengaluru", "text")
    assert a == content_hash(" SDE ", " Bengaluru ", " text ")
    assert a != content_hash("SDE", "Bengaluru", "text changed")


def test_clean_text_normalizes_nbsp():
    assert clean_text("a\xa0b\r\nc") == "a b\nc"


def test_truncate_keeps_head_and_tail():
    text = "HEAD" + ("x" * 5000) + "TAIL"
    out = truncate_for_llm(text, 500)
    assert out.startswith("HEAD") and out.endswith("TAIL") and len(out) <= 520


def test_date_parsers():
    assert parse_iso("2026-03-04T00:02:52.786+00:00").year == 2026
    assert parse_iso("2026-03-04T00:02:52Z").tzinfo is not None
    assert parse_iso(None) is None and parse_iso("garbage") is None
    assert parse_epoch_ms("1782114322811") > datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert parse_epoch_ms(None) is None


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
