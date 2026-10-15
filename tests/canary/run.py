#!/usr/bin/env python
"""Adapter canaries — assert every ATS still answers in the shape we parse.

    python tests/canary/run.py             # check and report
    python tests/canary/run.py --alert     # ...and email on a hard failure
    python tests/canary/run.py --only workday

Phase 0.4 of docs/PLAN.md. This is the only check in the project that would
notice an upstream API change. Everything else in the pipeline is built to
survive one, which is the problem: a renamed date field or a board that starts
answering `[]` does not raise, the empty-fetch guard correctly suppresses the
resulting closes, and the run goes green with nothing in it. The corpus stops
growing and no alert fires until the 72-hour pipeline_dry threshold, by which
time three days of postings are gone.

It hits live boards on purpose. A fixture-based test proves we can still parse
last month's response, which is exactly the thing that cannot break.

Two failure classes, deliberately treated differently:

  HARD    — a permanent fetch error, or a response we can fetch but no longer
            trust (too few postings, duplicate ids, missing fields, dates that
            will not parse). Exits non-zero; with --alert, emails.

  WARN    — a transient fetch error that survives one retry. Does not fail the
            run. Workday serves its SPA shell instead of JSON during a
            maintenance window that has landed between 06:37 and 07:57 UTC on
            four occasions (see ingest/models.py), taking out every tenant on a
            host at once and recovering by the next tick. A canary that went
            red for those would be teaching its reader to ignore it, which is
            worse than not having one. A genuinely dead board is caught instead
            by the staleness watchdog and by `pipeline_dry`.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ingest.adapters import get_adapter          # noqa: E402
from ingest.models import BoardFetchError        # noqa: E402

BOARDS = Path(__file__).with_name("boards.yaml")

# A posting must carry these, non-empty, on every row. The lifecycle diff keys
# on ats_job_id and the dashboard links absolute_url; either one missing is a
# silent data-integrity failure rather than a visible crash.
REQUIRED_ALWAYS = ("ats_job_id", "title", "absolute_url")

# These are allowed to be missing on a few rows — some boards genuinely post a
# role with no location string — but not on many.
REQUIRED_MOSTLY = ("location", "description")
MOSTLY_THRESHOLD = 0.90

# posted_at drives both the age gate and every latency measurement in the
# project, so a format change here is the most expensive silent failure
# available. All four adapters supplied it on 100% of rows on 2026-10-15.
POSTED_AT_THRESHOLD = 0.90

# Sanity window for a parsed date. Generous on purpose: the point is to catch a
# unit error (epoch seconds read as milliseconds, say), not to police boards.
EARLIEST = datetime(2015, 1, 1, tzinfo=timezone.utc)
FUTURE_SLACK = timedelta(days=2)

TRANSIENT_RETRY_DELAY_S = 20


def check_board(board: dict) -> tuple[str, list[str]]:
    """-> (status, problems) where status is one of ok | hard | warn."""
    ats, token = board["ats"], str(board["token"])
    adapter = get_adapter(ats)

    started = time.monotonic()
    try:
        jobs = adapter.fetch(token)
    except BoardFetchError as exc:
        if not getattr(exc, "transient", False):
            return "hard", [f"permanent fetch error: {exc}"]
        print(f"    transient error, retrying in {TRANSIENT_RETRY_DELAY_S}s: {exc}")
        time.sleep(TRANSIENT_RETRY_DELAY_S)
        try:
            jobs = adapter.fetch(token)
        except BoardFetchError as exc2:
            if getattr(exc2, "transient", False):
                return "warn", [f"transient twice: {exc2}"]
            return "hard", [f"permanent fetch error on retry: {exc2}"]
    except Exception as exc:
        # An adapter bug, not a board problem. Always hard.
        return "hard", [f"adapter raised {type(exc).__name__}: {exc}"]

    elapsed = time.monotonic() - started
    n = len(jobs)
    problems: list[str] = []

    if n < board["min_postings"]:
        problems.append(
            f"only {n} postings, expected at least {board['min_postings']} "
            f"— either the board emptied or the response shape changed"
        )
    if n == 0:
        # Nothing further is checkable, and the count problem above says it all.
        return ("hard" if problems else "ok"), problems

    ids = [j.ats_job_id for j in jobs]
    if len(set(ids)) != n:
        dupes = n - len(set(ids))
        problems.append(
            f"{dupes} duplicate ats_job_id(s) — the lifecycle diff keys on this, "
            f"so duplicates silently collapse postings"
        )

    for field in REQUIRED_ALWAYS:
        missing = sum(1 for j in jobs if not str(getattr(j, field, "") or "").strip())
        if missing:
            problems.append(f"{missing}/{n} postings have no {field}")

    bad_url = sum(1 for j in jobs if not str(j.absolute_url).startswith("http"))
    if bad_url:
        problems.append(f"{bad_url}/{n} absolute_url values are not http(s)")

    for field in REQUIRED_MOSTLY:
        present = sum(1 for j in jobs if str(getattr(j, field, "") or "").strip())
        if present < n * MOSTLY_THRESHOLD:
            problems.append(
                f"only {present}/{n} postings have {field} "
                f"(expected >= {MOSTLY_THRESHOLD:.0%})"
            )

    dated = [j.posted_at for j in jobs if j.posted_at is not None]
    if len(dated) < n * POSTED_AT_THRESHOLD:
        problems.append(
            f"only {len(dated)}/{n} postings have a parseable posted_at "
            f"(expected >= {POSTED_AT_THRESHOLD:.0%}) — a date format change "
            f"would break the age gate and every latency measurement"
        )
    naive = sum(1 for d in dated if d.tzinfo is None)
    if naive:
        problems.append(f"{naive}/{len(dated)} posted_at values are timezone-naive")

    now = datetime.now(timezone.utc)
    aware = [d for d in dated if d.tzinfo is not None]
    too_old = sum(1 for d in aware if d < EARLIEST)
    too_new = sum(1 for d in aware if d > now + FUTURE_SLACK)
    if too_old or too_new:
        problems.append(
            f"{too_old} posted_at before {EARLIEST:%Y-%m-%d} and {too_new} more "
            f"than {FUTURE_SLACK.days}d in the future — likely a unit error"
        )

    status = "hard" if problems else "ok"
    print(f"    {n} postings, {len(dated)} dated, in {elapsed:.1f}s")
    return status, problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated ats names to check")
    ap.add_argument("--alert", action="store_true",
                    help="email a watchdog alert if any board fails hard")
    args = ap.parse_args()

    boards = yaml.safe_load(BOARDS.read_text(encoding="utf-8")) or []
    if args.only:
        wanted = {s.strip().lower() for s in args.only.split(",")}
        boards = [b for b in boards if b["ats"].lower() in wanted]
    if not boards:
        print("no canary boards selected", file=sys.stderr)
        return 2

    failures: dict[str, list[str]] = {}
    warnings: dict[str, list[str]] = {}

    for board in boards:
        label = f"{board['ats']}/{board['token']}"
        print(f"  {board['company']} ({label})")
        status, problems = check_board(board)
        if status == "hard":
            failures[label] = problems
            for p in problems:
                print(f"    FAIL  {p}")
        elif status == "warn":
            warnings[label] = problems
            for p in problems:
                print(f"    WARN  {p}")
        else:
            print("    ok")

    print(f"\n{'-' * 60}")
    print(f"{len(boards) - len(failures) - len(warnings)} ok, "
          f"{len(failures)} failed, {len(warnings)} transient")

    if warnings and not failures:
        print("\nTransient only — not failing the run. If this repeats for days, "
              "the board is not transiently unwell and wants a look.")

    if failures:
        print("\nADAPTERS NEEDING ATTENTION")
        for label, problems in failures.items():
            print(f"  {label}")
            for p in problems:
                print(f"    - {p}")
        if args.alert:
            _alert(failures, warnings)
        return 1
    return 0


def _alert(failures: dict[str, list[str]], warnings: dict[str, list[str]]) -> None:
    """Email through the Layer 3 alert path.

    Imported lazily so that a canary run without --alert needs no database and
    no SMTP configuration — that is the form CI runs on pull requests.
    """
    from notify.alerts import send_alert

    lines = [
        f"{len(failures)} adapter canary board(s) no longer answer in the shape "
        f"this project parses.", "",
        "This is an upstream API change, not a dead board: the fetch succeeded "
        "or failed permanently, and the response did not validate. Until it is "
        "fixed, that ATS is probably ingesting nothing while every run stays "
        "green.", "",
    ]
    for label, problems in failures.items():
        lines.append(f"  {label}")
        for p in problems:
            lines.append(f"    - {p}")
        lines.append("")
    if warnings:
        lines.append("Transient (not counted as failures):")
        for label, problems in warnings.items():
            lines.append(f"  {label}: {'; '.join(problems)}")
        lines.append("")
    lines += [
        "Reproduce:",
        "  python tests/canary/run.py --only " + ",".join(
            sorted({label.split('/')[0] for label in failures})),
        "",
        "Roster and thresholds: tests/canary/boards.yaml",
    ]
    send_alert("adapter_schema",
               f"{len(failures)} adapter canary failure(s)", "\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
