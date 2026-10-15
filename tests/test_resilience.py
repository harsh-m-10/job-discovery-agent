"""LLM resilience (docs/PLAN.md Phase 7): rules fallback, circuit breaker,
usage ledger, chaos switch. No network — providers are fakes.

The property that matters: when every provider fails, jobs still come out with
a score -- provisional, capped, and marked -- instead of being stranded.

    python tests/test_resilience.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("FAKE_KEY", "x")

from score import llm
from score.providers import (ChatProvider, PaymentRequired, ProviderConfig,
                             QuotaExhausted, RateLimited)
from score.rules import (FALLBACK_PRIORS, MIN_SAMPLES, PROVISIONAL_CAP,
                         Calibration, score_job)

GOOD_REPLY = '[{"job_id": %d, "fit_score": 7.5, "min_years": 1, "max_years": 3, ' \
             '"matched_skills": ["python"], "gap_skills": [], "reasoning": "ok"}]'


class Fake(ChatProvider):
    """A ChatProvider whose complete() is scripted: a list of exceptions or
    'ok', consumed one per call."""

    def __init__(self, name: str, script: list):
        super().__init__(ProviderConfig(name=name, model=f"{name}-model", base_url="x",
                                        api_key_env="FAKE_KEY", batch_size=4, max_chars=2000))
        self.script = list(script)
        self.calls = 0

    def available(self) -> bool:
        return True

    def complete(self, system, user, max_output):
        self.calls += 1
        step = self.script.pop(0) if self.script else "ok"
        if isinstance(step, Exception):
            raise step
        return GOOD_REPLY % self._job_id

    def for_job(self, job_id):
        self._job_id = job_id
        return self


def job(i=1, title="Agentic AI Engineer"):
    return {"id": i, "title": title, "description": "Build agents.", "companies": {}}


def reset_usage():
    llm.USAGE.clear()


# --- rules scorer ------------------------------------------------------------

def test_calibration_takes_each_family_median_from_llm_history():
    history = [("genai", 8.0)] * MIN_SAMPLES + [("backend", 4.0)] * MIN_SAMPLES
    cal = Calibration.from_history(history)
    assert cal.priors["genai"] == 8.0 and cal.priors["backend"] == 4.0


def test_a_family_with_too_little_history_borrows_the_overall_median():
    history = [("genai", 8.0)] * MIN_SAMPLES + [("ml", 2.0)]          # ml: 1 sample
    cal = Calibration.from_history(history)
    assert cal.priors["ml"] == 8.0, cal.priors["ml"]


def test_no_history_at_all_uses_the_measured_fallback():
    assert Calibration.from_history([]).priors == FALLBACK_PRIORS


def test_a_provisional_score_never_claims_strong():
    cal = Calibration(priors={**FALLBACK_PRIORS, "genai": 9.5})
    row = score_job(job(), cal)
    assert row["fit_score"] == PROVISIONAL_CAP and row["verdict"] != "strong"


def test_a_senior_ask_is_penalised():
    cal = Calibration()
    plain = score_job(job(), cal)["fit_score"]
    senior = score_job(job(), cal, min_years=7)["fit_score"]
    assert senior < plain


def test_every_rules_row_says_it_is_provisional():
    row = score_job(job(), Calibration())
    assert row["provisional"] is True and row["provider"] == "rules"
    assert row["reasoning"].startswith("Provisional")
    assert row["reject_reason"] is None


# --- circuit breaker and ledger -----------------------------------------------

def test_a_quota_failure_opens_the_circuit_and_the_next_provider_scores():
    reset_usage()
    dead = Fake("alpha", [QuotaExhausted(3600, "over quota")]).for_job(1)
    live = Fake("beta", []).for_job(1)
    tripped: set[str] = set()
    rows = llm.score_batch([job(1)], [dead, live], system="s", tripped=tripped)
    assert rows[0]["provider"] == "beta" and rows[0]["fit_score"] == 7.5
    assert "alpha" in tripped

    # The next batch does not call the tripped provider at all.
    llm.score_batch([job(1)], [dead, live], system="s", tripped=tripped)
    assert dead.calls == 1, "a tripped provider was called again"


def test_payment_required_and_persistent_rate_limits_also_trip():
    reset_usage()
    tripped: set[str] = set()
    unpaid = Fake("unpaid", [PaymentRequired("402")]).for_job(1)
    llm.score_batch([job(1)], [unpaid, Fake("ok", []).for_job(1)], system="s", tripped=tripped)
    assert "unpaid" in tripped

    real_sleep = llm.time.sleep
    llm.time.sleep = lambda s: None
    try:
        limited = Fake("limited", [RateLimited(1, "429")] * 3).for_job(1)
        llm.score_batch([job(1)], [limited, Fake("ok2", []).for_job(1)], system="s", tripped=tripped)
    finally:
        llm.time.sleep = real_sleep
    assert "limited" in tripped


def test_every_provider_failing_raises_for_the_caller_to_fall_back():
    reset_usage()
    try:
        llm.score_batch([job(1)], [Fake("a", [QuotaExhausted(60, "q")]).for_job(1)],
                        system="s", tripped=set())
    except llm.AllProvidersExhausted:
        return
    raise AssertionError("an all-failed batch did not raise")


def test_the_ledger_counts_calls_outcomes_and_sets_a_cooldown():
    reset_usage()
    dead = Fake("alpha", [QuotaExhausted(7200, "over")]).for_job(1)
    live = Fake("beta", []).for_job(1)
    llm.score_batch([job(1)], [dead, live], system="s", tripped=set())
    a, b = llm.USAGE["alpha"], llm.USAGE["beta"]
    assert a["requests"] == 1 and a["quota_exhausted"] == 1 and a["cooldown_until"] is not None
    assert b["requests"] == 1 and b["successes"] == 1 and b["jobs_scored"] == 1
    assert b["last_success_at"] is not None and b["cooldown_until"] is None


# --- chaos switch and storage -------------------------------------------------

def test_simulate_outage_makes_a_provider_fail_like_a_real_outage():
    from score.run import simulate_outage
    p = Fake("google", [])
    simulate_outage([p, Fake("nvidia", [])], "google")
    try:
        p.complete("s", "u", 10)
    except QuotaExhausted as exc:
        assert "simulated" in str(exc)
        return
    raise AssertionError("simulated outage did not fail")


def test_every_upserted_score_carries_the_provisional_flag():
    """An LLM row replacing a rules row must write provisional=false, or the
    job is re-scored forever."""
    from score.store import ScoreStore
    sent = []

    class Resp:
        status_code = 201
        text = ""

    store = ScoreStore.__new__(ScoreStore)
    store.url = "https://example.invalid"
    store.session = type("S", (), {"post": lambda self, url, data, headers, timeout:
                                   sent.append(data) or Resp()})()
    store.upsert_scores([{"job_id": 1, "fit_score": 7.0},
                         {"job_id": 2, "fit_score": 5.0, "provisional": True}])
    import json
    rows = json.loads(sent[0])
    assert rows[0]["provisional"] is False and rows[1]["provisional"] is True


# --- watchdog: scoring health (PLAN 7.6) ---------------------------------------

def run_llm_health(scores, usage, now):
    from notify import watchdog as wd

    class R:
        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    def fake_get(url, headers=None, timeout=None):
        return R(scores if "job_scores" in url else usage)

    real_get, real_db = wd.requests.get, wd.db
    wd.requests.get, wd.db = fake_get, (lambda: ("https://x.invalid", {}))
    try:
        return wd.llm_health(now)
    finally:
        wd.requests.get, wd.db = real_get, real_db


def test_rules_scoring_with_no_recent_llm_success_is_an_outage():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    scores = [{"provider": "rules", "scored_at": "2026-10-15T11:30:00Z"}]
    usage = [{"provider": "google", "last_success_at": "2026-10-15T08:00:00Z"}]
    found = run_llm_health(scores, usage, now)
    assert any("no LLM provider has scored" in f for f in found), found


def test_a_recent_llm_success_is_not_an_outage():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    scores = [{"provider": "rules", "scored_at": "2026-10-15T11:30:00Z"}] + \
             [{"provider": "google", "scored_at": "2026-10-15T11:00:00Z"}] * 9
    usage = [{"provider": "google", "last_success_at": "2026-10-15T11:50:00Z"}]
    assert run_llm_health(scores, usage, now) == []


def test_a_mostly_rules_day_is_flagged_even_without_an_outage_now():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    scores = [{"provider": "rules", "scored_at": "2026-10-15T03:00:00Z"}] * 4 + \
             [{"provider": "google", "scored_at": "2026-10-15T11:00:00Z"}] * 6
    usage = [{"provider": "google", "last_success_at": "2026-10-15T11:50:00Z"}]
    found = run_llm_health(scores, usage, now)
    assert len(found) == 1 and "40%" in found[0], found


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
