"""Instant channels (ntfy + per-match email) and their routing. No network.

The property that matters most: no job is ever dropped. A job neither instant
channel takes -- because both failed, or because max_pings_per_day is spent --
must land in the overflow digest in the same run.

    python tests/test_instant.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SUPABASE_URL", "https://example.invalid")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")

import notify.run as nr
from notify import email as email_mod
from notify import instant


def job(i: int, score: float = 8.0, **extra) -> dict:
    return {"job_id": i, "title": f"Engineer {i}", "company": "Acme",
            "location": "Bengaluru", "absolute_url": f"https://x.invalid/{i}",
            "fit_score": score, "age_label": "2h", "min_years": 2, "max_years": 4,
            **extra}


def with_env(env: dict, fn):
    saved = {k: os.environ.get(k) for k in env}
    try:
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return fn()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


ALL = {"NTFY_TOPIC": "jobagent-test", "RESEND_API_KEY": "re_test",
       "ALERT_EMAIL_TO": "me@example.invalid", "SMTP_USER": "me@example.invalid",
       "SMTP_PASS": "pw", "MATCH_EMAIL_FROM": None}
NOTHING = {"NTFY_TOPIC": None, "RESEND_API_KEY": None, "SMTP_USER": None,
           "SMTP_PASS": None, "ALERT_EMAIL_TO": None}


# --- formatting ---------------------------------------------------------------

def test_the_subject_starts_with_the_score_tag_the_gmail_filter_keys_on():
    assert instant.subject(job(1, 8.44)) == "[JOB 8.4] Engineer 1 - Acme (Bengaluru)"
    assert instant.subject({**job(1), "fit_score": None}).startswith("[JOB] ")
    assert len(instant.subject({**job(1), "title": "x" * 400})) <= 180


def test_the_body_carries_what_you_need_to_act_from_the_phone():
    j = job(1, role_family="backend", resume_variant="https://r.invalid/be",
            referrals=[{"full_name": "Asha Rao", "title": "Staff Engineer",
                        "near_batch": True, "profile_url": "https://p.invalid/asha"}])
    text = instant.format_message(j, "https://dash.invalid/d/s")
    assert text.startswith("8.0 | Engineer 1"), "score must lead"
    assert "Resume: backend -> https://r.invalid/be" in text
    assert "Ask: Asha Rao (Staff Engineer) · near-batch https://p.invalid/asha" in text
    assert "Apply: https://x.invalid/1" in text
    assert "https://dash.invalid/d/s/j/1" in text
    assert "provisional" not in text


def test_a_provisional_score_and_a_non_ats_source_say_so():
    text = instant.format_message(job(1, provisional=True, source="indeed"))
    assert "provisional" in text and "Source: indeed" in text


def test_the_ntfy_payload_is_json_with_click_and_apply():
    p = with_env(ALL, lambda: instant.ntfy_payload(job(7, 8.5), "https://d.invalid/d/s"))
    assert p["topic"] == "jobagent-test"
    assert p["title"].startswith("[JOB 8.5]")
    assert p["priority"] == 4, "8+ must make a sound"
    assert p["click"] == "https://d.invalid/d/s/j/7"
    assert p["actions"][0]["url"] == "https://x.invalid/7"
    low = with_env(ALL, lambda: instant.ntfy_payload(job(7, 6.9)))
    assert low["priority"] == 3 and low["click"] == "https://x.invalid/7"


def test_the_match_email_comes_from_resend_not_gmail_when_keyed():
    sent = {}
    real = email_mod.send
    email_mod.send = lambda subject, body, **kw: sent.update(kw, subject=subject) or kw["transport"]
    try:
        via = with_env(ALL, lambda: instant.send_email(job(1)))
    finally:
        email_mod.send = real
    assert via == "resend" and sent["sender"] == instant.MATCH_EMAIL_FROM
    assert "gmail" not in sent["sender"]


def test_without_resend_the_match_email_still_goes_by_smtp():
    real = email_mod.send
    email_mod.send = lambda subject, body, **kw: kw["transport"]
    try:
        via = with_env({**ALL, "RESEND_API_KEY": None}, lambda: instant.send_email(job(1)))
    finally:
        email_mod.send = real
    assert via == "smtp"


def test_digests_and_alerts_prefer_smtp_so_a_gmail_from_is_never_sent_via_resend():
    assert with_env(ALL, email_mod.transport) == "smtp"
    assert with_env({**ALL, "SMTP_PASS": None}, email_mod.transport) == "resend"


# --- routing in notify.run.main -------------------------------------------------

def run_main(jobs, *, env=None, sent_today=0, cap=40, ntfy_fails=False, email_fails=False):
    records, digests, pushes, mails = [], [], [], []
    patches = {
        "pending_jobs": lambda *a, **k: list(jobs),
        "record": lambda job_id, channel, ok, err: records.append((job_id, channel, ok)),
        "prune_failures": lambda days: 0,
        "pings_sent_today": lambda channels: sent_today,
    }
    saved = {k: getattr(nr, k) for k in patches}
    real = (instant.send_ntfy, instant.send_email, nr.email_channel.send, nr.yaml.safe_load)
    real_argv = sys.argv

    def fake_ntfy(payload):
        if ntfy_fails:
            raise RuntimeError("ntfy 502: bad gateway")
        pushes.append(payload)

    def fake_email(j, dashboard=""):
        if email_fails:
            raise RuntimeError("resend 403")
        mails.append(j["job_id"])
        return "resend"

    try:
        for k, v in patches.items():
            setattr(nr, k, v)
        instant.send_ntfy, instant.send_email = fake_ntfy, fake_email
        def fake_digest(subject, body, **kw):
            if email_mod.transport() is None:      # as the real send() behaves
                raise email_mod.NotConfigured("no email transport configured")
            digests.append(body)
            return "smtp"
        nr.email_channel.send = fake_digest
        nr.yaml.safe_load = lambda _: {"ping_threshold": 6.5, "max_pings_per_day": cap}
        sys.argv = ["notify.run"]
        with_env(env or ALL, nr.main)
    finally:
        for k, v in saved.items():
            setattr(nr, k, v)
        instant.send_ntfy, instant.send_email, nr.email_channel.send, nr.yaml.safe_load = real
        sys.argv = real_argv
    return records, digests, pushes, mails


def test_every_job_gets_a_push_and_an_email():
    records, digests, pushes, mails = run_main([job(1), job(2)])
    assert len(pushes) == 2 and mails == [1, 2] and not digests
    assert {(j, c) for j, c, ok in records if ok} == {
        (1, "ntfy"), (1, "email-instant"), (2, "ntfy"), (2, "email-instant")}


def test_one_channel_failing_still_counts_as_delivered():
    records, digests, pushes, mails = run_main([job(1)], ntfy_fails=True)
    assert mails == [1] and not digests
    assert (1, "ntfy", False) in records and (1, "email-instant", True) in records


def test_both_failing_falls_back_to_the_digest_in_the_same_run():
    records, digests, _, _ = run_main([job(1)], ntfy_fails=True, email_fails=True)
    assert len(digests) == 1 and "ntfy: ntfy 502" in digests[0]
    assert (1, "email", True) in records


def test_jobs_over_the_daily_cap_go_to_the_digest_not_dropped():
    records, digests, pushes, _ = run_main([job(1, 9.5), job(2, 8.0), job(3, 7.0)],
                                           sent_today=39, cap=40)
    assert len(pushes) == 1 and pushes[0]["title"].startswith("[JOB 9.5]"), \
        "the best job must take the last slot"
    assert len(digests) == 1 and "daily ping cap (40) reached" in digests[0]
    assert {j for j, c, ok in records if c == "email" and ok} == {2, 3}


def test_with_nothing_configured_nothing_is_lost():
    # No ntfy and no email transport: nothing can be sent, and no success row
    # is written, so the next run retries every job.
    records, digests, pushes, mails = run_main([job(1)], env=NOTHING)
    assert not pushes and not mails
    assert not any(ok for _, _, ok in records)


def test_a_zero_cap_means_unlimited():
    _, digests, pushes, _ = run_main([job(i) for i in range(5)], sent_today=500, cap=0)
    assert len(pushes) == 5 and not digests


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
