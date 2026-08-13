"""Scoring-transport tests. No network.

These exist because of a real incident: the code read the per-minute reset
header (229ms) when the actual failure was the per-DAY token quota, retried
immediately, and burned the rest of the day's budget writing failure rows over
good scores. Every assertion here corresponds to a step in that chain.

    python tests/test_llm.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from score.llm import (_Pacer, _retry_hint_seconds, normalize_entry,
                       parse_scores)

TPD_BODY = ('{"error":{"message":"Rate limit reached for model '
            '`llama-3.3-70b-versatile` in organization `org_x` service tier '
            '`on_demand` on tokens per day (TPD): Limit 100000, Used 97337, '
            'Requested 5001. Please try again in 33m40.032s."}}')
TPM_BODY = ('{"error":{"message":"Rate limit reached for model `x` on tokens '
            'per minute (TPM): Limit 12000. Please try again in 8.5s."}}')


def test_retry_hint_parses_minutes_and_seconds():
    assert abs(_retry_hint_seconds(TPD_BODY) - (33 * 60 + 40.032)) < 0.1
    assert abs(_retry_hint_seconds(TPM_BODY) - 8.5) < 0.1
    assert _retry_hint_seconds("no hint here") == 0.0


def test_daily_and_minute_limits_are_distinguishable():
    # The whole bug: these two look identical unless the body is inspected.
    assert "tokens per day" in TPD_BODY.lower()
    assert "tokens per day" not in TPM_BODY.lower()


def test_pacer_header_units():
    # x-ratelimit-reset-tokens comes back as "229ms" or "1m26.4s". Reading
    # "229ms" as 229 seconds (or as minutes) is what made pacing nonsense.
    assert abs(_Pacer._seconds("229ms") - 0.229) < 0.001
    assert abs(_Pacer._seconds("8.5s") - 8.5) < 0.001
    assert abs(_Pacer._seconds("2m") - 120) < 0.001
    assert _Pacer._seconds(None) == 0.0
    assert _Pacer._seconds("garbage") == 0.0


def test_verdict_is_recomputed_from_score():
    # Models routinely return a generous verdict beside a modest number, and the
    # ping threshold keys off the number.
    row = normalize_entry({"job_id": 1, "fit_score": 5.0, "verdict": "strong",
                           "reasoning": "x"}, "m")
    assert row["verdict"] == "stretch"
    assert normalize_entry({"job_id": 1, "fit_score": 8.4}, "m")["verdict"] == "strong"
    assert normalize_entry({"job_id": 1, "fit_score": 3.9}, "m")["verdict"] == "reject"


def test_scores_out_of_range_are_clamped():
    assert normalize_entry({"job_id": 1, "fit_score": 44}, "m")["fit_score"] == 10.0
    assert normalize_entry({"job_id": 1, "fit_score": -3}, "m")["fit_score"] == 0.0


def test_unusable_entries_rejected():
    assert normalize_entry({"fit_score": 8}, "m") is None            # no job_id
    assert normalize_entry({"job_id": 1}, "m") is None               # no score
    assert normalize_entry({"job_id": "abc", "fit_score": 8}, "m") is None


def test_parse_strips_markdown_fences():
    assert parse_scores('```json\n{"scores":[{"job_id":1}]}\n```') == [{"job_id": 1}]
    assert parse_scores('{"scores":[{"job_id":2}]}') == [{"job_id": 2}]
    assert parse_scores('[{"job_id":3}]') == [{"job_id": 3}]


def test_parse_recovers_object_from_surrounding_prose():
    assert parse_scores('Here you go: {"scores":[{"job_id":4}]} hope that helps')


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
