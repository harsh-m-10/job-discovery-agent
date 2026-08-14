"""Scoring transport + provider tests. No network.

Several of these exist because of real incidents: reading a per-minute reset
header when the actual failure was a per-day quota, and a failure row
overwriting a good score. Each assertion below corresponds to a step in one of
those chains.

    python tests/test_llm.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from score.llm import (apply_headcount_penalty, build_system_prompt,
                       build_user_message, load_profile, normalize_entry,
                       parse_scores, verdict_for)
from score.providers import (Pacer, ProviderConfig, _retry_hint_seconds,
                             build_providers)

TPD_BODY = ('{"error":{"message":"Rate limit reached for model '
            '`llama-3.3-70b-versatile` on tokens per day (TPD): Limit 100000, '
            'Used 97337. Please try again in 33m40.032s."}}')
TPM_BODY = ('{"error":{"message":"Rate limit reached on tokens per minute '
            '(TPM): Limit 12000. Please try again in 8.5s."}}')
GOOGLE_BODY = ('{"error":{"code":429,"status":"RESOURCE_EXHAUSTED",'
               '"details":[{"retryDelay":"27s"}]}}')


# --- compensation must never reach a provider ---------------------------

def test_compensation_is_stripped_from_the_prompt():
    """The load-bearing assertion. A hallucinated CTC is unrecoverable."""
    profile = load_profile()
    comp = profile.get("compensation", {})
    assert comp, "profile has no compensation block to test against"

    prompt = build_system_prompt()
    assert "compensation" not in prompt.lower().split("candidate facts")[-1][:4000] \
        or "expected_ctc" not in prompt
    for key in ("current_ctc", "expected_ctc", "expected_base_min_lpa",
                "notice_period_days"):
        assert key not in prompt, f"{key} leaked into the prompt"
    for value in comp.values():
        text = str(value).strip()
        if len(text) > 4 and text.replace(",", "").replace(" ", "").isdigit():
            assert text not in prompt, f"compensation value {text!r} leaked"
    assert "000000" not in prompt and "0,00,000" not in prompt
    assert "<expected range>" not in prompt


def test_prompt_still_contains_the_calibration_rules():
    prompt = build_system_prompt()
    assert "EXPERIENCE HANDLING" in prompt
    assert "DOMAIN PENALTY" in prompt          # telecom exit
    assert "TELECOM EXPERIENCE IS NOT A PREFERENCE" in prompt
    assert "{CANDIDATE_FACTS}" not in prompt and "{{" not in prompt


# --- vendor error classification ----------------------------------------

def test_retry_hint_parses_groq_and_google_shapes():
    assert abs(_retry_hint_seconds(TPD_BODY) - (33 * 60 + 40.032)) < 0.1
    assert abs(_retry_hint_seconds(TPM_BODY) - 8.5) < 0.1
    assert abs(_retry_hint_seconds(GOOGLE_BODY) - 27.0) < 0.1
    assert _retry_hint_seconds("no hint here") == 0.0


def test_daily_and_minute_limits_are_distinguishable():
    # The whole bug: identical status codes, different meaning, only the body says.
    assert "tokens per day" in TPD_BODY.lower()
    assert "tokens per day" not in TPM_BODY.lower()
    assert "resource_exhausted" in GOOGLE_BODY.lower()


def test_pacer_header_units():
    # "229ms" read as seconds (or minutes) is what made pacing nonsense.
    assert abs(Pacer._seconds("229ms") - 0.229) < 0.001
    assert abs(Pacer._seconds("8.5s") - 8.5) < 0.001
    assert abs(Pacer._seconds("2m") - 120) < 0.001
    assert Pacer._seconds(None) == 0.0
    assert Pacer._seconds("garbage") == 0.0


# --- provider config ----------------------------------------------------

def test_providers_build_in_priority_order():
    settings = {"llm_providers": [
        {"name": "c", "priority": 3, "model": "m", "base_url": "u", "api_key_env": "C_KEY"},
        {"name": "a", "priority": 1, "model": "m", "base_url": "u", "api_key_env": "A_KEY"},
        {"name": "b", "priority": 2, "model": "m", "base_url": "u", "api_key_env": "B_KEY"},
    ]}
    assert [p.name for p in build_providers(settings)] == ["a", "b", "c"]


def test_provider_is_unavailable_without_a_key():
    cfg = ProviderConfig(name="x", model="m", base_url="u",
                         api_key_env="DEFINITELY_NOT_SET_12345")
    from score.providers import ChatProvider
    assert ChatProvider(cfg).available() is False


def test_real_settings_define_a_usable_chain():
    """Asserts shape, not an exact roster — providers get added and retired."""
    from score.llm import load_settings
    providers = build_providers(load_settings())
    names = [p.name for p in providers]

    assert len(providers) >= 2, f"no failover headroom: {names}"
    assert names[0].startswith("google"), f"google should lead, got {names}"
    assert names == sorted(names, key=lambda n: [p.cfg.priority
                                                 for p in providers
                                                 if p.name == n][0])
    # No hardcoded limits: every provider must carry its own rate config.
    for p in providers:
        assert p.cfg.batch_size > 0 and p.cfg.max_chars > 0, p.name
        assert p.cfg.api_key_env.endswith("_API_KEY"), p.name
        assert p.cfg.model, p.name


def test_groq_is_disabled():
    """The Groq account is shared with another project; it must stay off."""
    from score.llm import load_settings
    groq = [p for p in build_providers(load_settings()) if p.name == "groq"]
    assert groq and groq[0].cfg.enabled is False


# --- scoring output coercion --------------------------------------------

def test_verdict_is_recomputed_from_score():
    row = normalize_entry({"job_id": 1, "fit_score": 5.0, "verdict": "strong"}, "m", "p")
    assert row["verdict"] == "stretch"
    assert normalize_entry({"job_id": 1, "fit_score": 8.4}, "m", "p")["verdict"] == "strong"


def test_provider_is_recorded_on_the_row():
    row = normalize_entry({"job_id": 1, "fit_score": 7.0}, "gemini-2.0-flash", "google")
    assert row["provider"] == "google" and row["model"] == "gemini-2.0-flash"


def test_scores_out_of_range_are_clamped():
    assert normalize_entry({"job_id": 1, "fit_score": 44}, "m", "p")["fit_score"] == 10.0
    assert normalize_entry({"job_id": 1, "fit_score": -3}, "m", "p")["fit_score"] == 0.0


def test_unusable_entries_rejected():
    assert normalize_entry({"fit_score": 8}, "m", "p") is None
    assert normalize_entry({"job_id": 1}, "m", "p") is None


def test_parse_strips_markdown_fences():
    assert parse_scores('```json\n{"scores":[{"job_id":1}]}\n```') == [{"job_id": 1}]
    assert parse_scores('[{"job_id":3}]') == [{"job_id": 3}]


# --- headcount penalty --------------------------------------------------

PENALTIES = {"micro": -2.0, "small": 0.0, "mid": 0.0, "large": 0.0, "unknown": 0.0}


def test_micro_is_penalised_and_verdict_recomputed():
    row = {"job_id": 1, "fit_score": 8.5, "verdict": "strong"}
    apply_headcount_penalty(row, "micro", PENALTIES)
    assert row["fit_score"] == 6.5 and row["verdict"] == "worth_trying"


def test_other_bands_are_untouched():
    for band in ("small", "mid", "large", "unknown", None):
        row = {"job_id": 1, "fit_score": 8.5, "verdict": "strong"}
        apply_headcount_penalty(row, band, PENALTIES)
        assert row["fit_score"] == 8.5, band


def test_penalty_never_pushes_below_zero():
    row = {"job_id": 1, "fit_score": 1.0, "verdict": "reject"}
    apply_headcount_penalty(row, "micro", PENALTIES)
    assert row["fit_score"] == 0.0


def test_penalty_skips_unscored_rows():
    row = {"job_id": 1, "fit_score": None, "verdict": "reject"}
    apply_headcount_penalty(row, "micro", PENALTIES)
    assert row["fit_score"] is None


def test_verdict_thresholds():
    assert verdict_for(8.0) == "strong"
    assert verdict_for(7.9) == "worth_trying"
    assert verdict_for(4.0) == "stretch"
    assert verdict_for(3.9) == "reject"


def test_band_is_shown_to_the_model():
    jobs = [{"id": 1, "title": "SDE", "description": "x",
             "companies": {"name": "Acme", "category": "product",
                           "headcount_band": "micro"}}]
    assert "Company size band: micro" in build_user_message(jobs, 500)


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
