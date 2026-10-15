"""Store tests — the run_log PATCH degradation path. No network, no DB.

Why this exists: code and schema cannot be deployed together in this project.
A migration is applied by hand (Supabase SQL editor, or psycopg2 over the
ap-south-1 pooler) while the code that writes the new column reaches the runner
on the next trigger. Whichever order those happen in, there is a window where
the code knows about a column the database does not.

That window must cost at most the new column. Before 0004 this was handled with
a one-off `if "boards" in str(exc)` branch; with 0005 adding two more optional
columns the branch became a loop, and a loop that drops columns from a write is
worth testing properly — a bug here silently stops recording telemetry rather
than failing, which is the hardest kind of failure to notice.

    python tests/test_store.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.store import Store


def store() -> Store:
    """A Store with credentials that are never used — every test stubs _request."""
    return Store(url="https://example.invalid", key="test-key")


def pgrst204(column: str) -> RuntimeError:
    """The real shape of PostgREST's missing-column error."""
    return RuntimeError(
        f"supabase PATCH run_log -> 400: "
        f'{{"code":"PGRST204","details":null,"hint":null,'
        f'"message":"Could not find the \'{column}\' column of '
        f"'run_log' in the schema cache\"}}"
    )


def recorder(missing: set[str]):
    """Stub _request that rejects any body touching a column in `missing`.

    Returns (fn, attempts) where attempts collects each body it was handed, so a
    test can assert both the end state and how many round-trips it took.
    """
    attempts: list[dict] = []

    def fn(method, table, *, params="", body=None, prefer=None, headers=None):
        attempts.append(dict(body))
        for column in missing:
            if column in body:
                raise pgrst204(column)
        return None

    return fn, attempts


def test_a_full_schema_writes_everything_in_one_request():
    s = store()
    fn, attempts = recorder(set())
    s._request = fn
    s.finish_run(1, jobs_seen=10, jobs_new=2, jobs_closed=3, errors=[],
                 boards=[{"name": "x"}], trigger_source="cronjob",
                 duration_s=12.5)
    assert len(attempts) == 1, f"expected one PATCH, got {len(attempts)}"
    body = attempts[0]
    assert body["trigger_source"] == "cronjob"
    assert body["duration_s"] == 12.5
    assert body["jobs_closed"] == 3
    assert body["boards"] == [{"name": "x"}]


def test_a_missing_optional_column_is_dropped_and_the_rest_still_lands():
    """The 0005-not-yet-applied case: trigger_source and duration_s unknown."""
    s = store()
    fn, attempts = recorder({"trigger_source", "duration_s"})
    s._request = fn
    s.finish_run(1, jobs_seen=10, jobs_new=2, jobs_closed=3, errors=[],
                 boards=None, trigger_source="cronjob", duration_s=12.5)
    final = attempts[-1]
    assert "trigger_source" not in final and "duration_s" not in final
    assert final["jobs_seen"] == 10 and final["jobs_closed"] == 3, \
        "dropping an optional column must not drop the core counters"


def test_every_optional_column_can_be_absent_at_once():
    """A database still on 0003 — nothing from 0004 or 0005 exists."""
    s = store()
    fn, attempts = recorder({"boards", "trigger_source", "duration_s"})
    s._request = fn
    s.finish_run(1, jobs_seen=1, jobs_new=1, jobs_closed=0, errors=[],
                 boards=[{"name": "x"}], trigger_source="schedule",
                 duration_s=1.0)
    final = attempts[-1]
    assert set(final) == {"finished_at", "jobs_seen", "jobs_new",
                          "jobs_closed", "errors"}, f"unexpected body {final}"


def test_a_missing_core_column_still_raises():
    """A PGRST204 on jobs_seen means the schema is broken, not merely behind.

    Degrading here would turn a corrupt deployment into a silently half-written
    run log, which is exactly what this telemetry exists to rule out.
    """
    s = store()
    fn, _ = recorder({"jobs_seen"})
    s._request = fn
    try:
        s.finish_run(1, jobs_seen=1, jobs_new=1, jobs_closed=0, errors=[])
    except RuntimeError:
        return
    raise AssertionError("a missing core column was swallowed")


def test_a_non_pgrst204_error_is_never_swallowed():
    s = store()

    def fn(method, table, *, params="", body=None, prefer=None, headers=None):
        raise RuntimeError("supabase PATCH run_log -> 503: upstream unavailable")

    s._request = fn
    try:
        s.finish_run(1, jobs_seen=1, jobs_new=1, jobs_closed=0, errors=[])
    except RuntimeError as exc:
        assert "503" in str(exc)
        return
    raise AssertionError("a transport error was swallowed")


def test_no_run_id_is_a_no_op():
    """--dry-run never opened a run row, so there is nothing to close."""
    s = store()
    called = []

    def fn(*a, **k):
        called.append(1)

    s._request = fn
    s.finish_run(None, jobs_seen=0, jobs_new=0, jobs_closed=0, errors=[])
    assert not called, "finish_run wrote without a run_id"


def test_the_company_key_fill_never_takes_down_a_run():
    """Housekeeping at the head of ingest. On 2026-10-15 it crashed a run
    seconds after migration 0009, before PostgREST's schema cache caught up."""
    s = store()

    def unknown_column(*a, **k):
        raise RuntimeError("supabase GET companies -> 400: PGRST204 company_key")

    s._select_all = unknown_column
    assert s.fill_missing_company_keys() == 0

    s._select_all = lambda *a, **k: [{"id": 1, "name": "Razorpay Software Private Limited"},
                                     {"id": 2, "name": "Acme"}]
    written = []

    def flaky(method, table, **k):
        written.append(k["body"])
        if len(written) == 2:
            raise RuntimeError("supabase PATCH companies -> 503")

    s._request = flaky
    assert s.fill_missing_company_keys() == 1, "must report what it managed, not raise"
    assert written[0] == {"company_key": "razorpay"}, "the key must come from common/"


def test_layer_1_still_refuses_layer_2_tables():
    """The boundary rule, asserted at the one place it is enforced in code."""
    s = store()
    for table in ("job_scores", "connections", "applications"):
        try:
            s._path(table)
        except PermissionError:
            continue
        raise AssertionError(f"Layer 1 accepted {table}")
    assert s._path("jobs").endswith("/rest/v1/jobs")


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
